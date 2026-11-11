#!/usr/bin/env python3
"""
main.py — Motor de LegoSearch.

Flujo de trabajo (todo el estado vive en memoria y se exporta al final):

    1. RASTREO       Lee las fuentes RSS de ``config.FEEDS`` con feedparser.
                     Cada feed falla de forma aislada: si una fuente está caída
                     o bloqueada, se registra el error y se continúa.
    2. EXTRACCIÓN    Localiza referencias de set (5xxx/6xxx/7xxx/8xxx/9xxx y
                     temáticas 1xxxx) y precios en euros (regex) en título y
                     resumen de cada entrada.
    3. CRUCE         Solo se conservan las referencias presentes en la
                     WATCHLIST (o en ``watchlist.json`` si existe).
    4. FILTRO DUAL   (a) Criterios de coleccionista (AFOL, piezas, exclusivas)
                     y (b) Motor financiero (compra -> gastos -> beneficio/ROI).
    5. EXPORTACIÓN   Escribe ``opportunities.json``, sumando el histórico sin
                     duplicar set+fuente+oferta, y genera ``reports/reporte_*.md``.

Uso:
    python main.py                          # ejecución normal
    python main.py --dry-run                # no escribe ficheros
    python main.py --simulate               # demo sin red (datos de ejemplo)
    python main.py --only-feeds chollometro,reddit
    python main.py --days 90 --verbose
    python main.py --refresh                # ignora la caché local de RSS
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import os
import random
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlparse
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import feedparser
import requests

import config as cfg

# ---------------------------------------------------------------------------
# CONSTANTES DE FORMATO / METADATOS DE VERSIÓN
# ---------------------------------------------------------------------------
APP_VERSION = "1.0.0"
# Subimos la versión del schema cada vez que cambie la forma del JSON para que
# el superagente externo pueda adaptarse a los cambios de forma explícita.
SCHEMA_VERSION = "1.0"


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


log = logging.getLogger("legosearch")

# ---------------------------------------------------------------------------
# MODELOS DE DATOS
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceMatch:
    """Una cantidad en euros detectada en un texto, con su contexto."""

    valor: float
    texto_original: str    # "599,99 €" — útil para auditoría humana
    posicion: int          # posición en el texto para puntuar cercanía
    etiqueta: str          # "oferta" | "pvp" | "general"


@dataclass
class Entry:
    """Una entrada RSS ya normalizada (independiente del formato del feed)."""

    feed: str
    tipo: str
    valor_eur: float
    titulo: str
    texto: str             # título + resumen, ya sin HTML
    url: str
    fecha: datetime | None
    referencias: list[str] = field(default_factory=list)
    precios: list[PriceMatch] = field(default_factory=list)


@dataclass
class Financials:
    """Resultado del motor financiero para una operación concreta."""

    precio_compra: float
    gastos_plataforma: float
    gastos_envio: float
    coste_total: float
    ingresos_netos: float
    beneficio_neto: float
    roi_pct: float


@dataclass
class Candidate:
    """Una oportunidad potencial ya cruzada con la watchlist."""

    referencia: str
    set: dict
    entry: Entry
    precio_detectado: PriceMatch | None
    precio_compra: float
    origen_precio: str           # "oferta_detectada" | "precio_objetivo_compra"
    score_coleccionista: int     # 0..3
    score_coleccionista_label: str
    confianza_cruce: int         # 0..100
    financiero: Financials
    score_total: float


# ---------------------------------------------------------------------------
# UTILIDADES DE TEXTO
# ---------------------------------------------------------------------------

# Etiquetas HTML que separamos con espacio para no fusionar palabras.
_TAG_SEP_RE = re.compile(r"<(?:br|p|div|li|h[1-6]|tr|td)[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_ESPACIOS_RE = re.compile(r"\s+")


def normalizar(texto: str) -> str:
    """Quita HTML, entidades, acentos y pasa a minúsculas.

    Así "Millennium Fálcon" y "millennium falcon" comparan igual y evitamos
    falsos negativos por mayúsculas o tildes.
    """
    if not texto:
        return ""
    texto = _TAG_SEP_RE.sub(" ", texto)
    texto = _TAG_RE.sub(" ", texto)
    texto = html.unescape(texto)
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return _ESPACIOS_RE.sub(" ", texto).strip().lower()


def parse_fecha(entry: Any) -> datetime | None:
    """Obtiene la fecha de publicación de una entrada RSS con varios fallbacks."""
    for clave in ("published_parsed", "updated_parsed", "created_parsed"):
        valor = entry.get(clave)
        if valor:
            try:
                # feedparser devuelve struct_time en UTC.
                return datetime.fromtimestamp(time.mktime(valor), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue

    for clave in ("published", "updated", "created"):
        valor = entry.get(clave)
        if not valor:
            continue
        try:
            fecha = parsedate_to_datetime(valor)
            if fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=timezone.utc)
            return fecha
        except (TypeError, ValueError):
            continue
    return None


def dias_desde(fecha: datetime | None) -> float:
    """Días transcurridos desde ``fecha``; infinito si no hay fecha conocida."""
    if fecha is None:
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - fecha).total_seconds() / 86400.0)


# ---------------------------------------------------------------------------
# EXTRACCIÓN CON REGEX
# ---------------------------------------------------------------------------

# 1) Referencias de set: 4-6 dígitos aislados (75192, 10294, 21318...).
#    Excluimos grupos de miles tipo "1.499" y "1,299" usando lookarounds.
_REF_RE = re.compile(r"(?<![\d.,])((?:[1-9]\d{3,5}))(?![\d.,])")

# 2) Precios en euros. Cubre los formatos habituales en feeds ES/EN:
#    "599,99 €"  "€599.99"  "EUR 84.99"  "84.99€"  "599 €"  "euros"  "400€".
_EURO_RE = re.compile(
    r"(?:\b(?:eur|euros?)\b|€)\s*"
    r"(?P<pre_num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"(?![\d])"
    r"|"
    r"(?:(?<![\w.,])(?P<num>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*"
    r"(?:€|\b(?:eur|euros?)\b))"
    r"(?![\d])",
    re.IGNORECASE,
)

# Palabras que indican que el precio cercano es una OFERTA (no un PVP).
_OFERTA_CERCA_RE = re.compile(
    r"\bsolo\b|\bpor\b|\bnow\b|\bahora\b|\bat\b|\bprice\b|\bprecio de\b"
    r"|al mejor precio|(?<!-)\d{1,2}\s*% ?(?:off|de descuento|descuento|menos)"
    r"|descuento|rebaja|oferta|chollo|deal|clearance|sale\b|bargain|drop",
    re.IGNORECASE,
)

# Palabras que indican que el precio cercano es el PVP / recomendado.
_PVP_CERCA_RE = re.compile(
    r"\bpvp\b|retail|rrc|recomendado|original(?:ly)?|precio normal|precio oficial"
    r"|antes|was\b|\bwas:",
    re.IGNORECASE,
)

# Señales de "oferta viva": si no aparecen, la cantidad detectada es probable
# que sea "lo pagó por" o un dato histórico, no una oportunidad actual.
_OFERTA_VIVA_RE = re.compile(
    r"\bofertas?\b|\bdeals?\b|\bchollo|\bdescuent|\bdiscount|\bsale\b"
    r"|\bbargain|clearance|\brebaja|\bsolo\b|\bprecio\b|\bprecio de\b"
    r"|\bmuy barato|\bdesde €|\ba €|\ben €|\bat €|\bdesde\b",
    re.IGNORECASE,
)

# La entrada debe hablar de LEGO, no solo mencionar una marca cuyo nombre
# coincide con un set (p. ej. "Nintendo Switch" vs. set 71374 NES).
_CONTEXTO_LEGO_RE = re.compile(r"\blego\b|\bbrick|\bminifig|\blego group", re.IGNORECASE)

# --- Formatos numéricos ----------------------------------------------------


def _parse_numero_eur(raw: str) -> float | None:
    """Convierte "599,99", "79.9", "1.499" o "1,299.99" a float.

    Reglas: si hay coma y punto, el último separador es decimal. Si solo hay
    coma y el grupo final tiene 1-2 dígitos -> decimal; si son 3 dígitos y el
    número es > 999 interpretado como miles -> miles. Lo mismo con el punto.
    """
    raw = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not raw:
        return None

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        parte_izq, parte_der = raw.rsplit(",", 1)
        if len(parte_der) == 3 and len(parte_izq) >= 1 and parte_izq.isdigit():
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif "." in raw:
        partes = raw.split(".")
        if len(partes) > 2:
            raw = "".join(partes[:-1]) + "." + partes[-1]
        elif len(partes[-1]) == 3 and len(partes[0]) >= 1:
            # "1.499" -> miles (un set no cuesta 1 euro con 3 decimales)
            raw = raw.replace(".", "")

    try:
        valor = float(raw)
    except ValueError:
        return None
    return valor if valor > 0 else None


def extraer_precios(texto: str) -> list[PriceMatch]:
    """Devuelve todas las cantidades en euros del texto, clasificadas."""
    matches: list[PriceMatch] = []
    if not texto:
        return matches

    for m in _EURO_RE.finditer(texto):
        numero = m.group("pre_num") or m.group("num")
        valor = _parse_numero_eur(numero or "")
        if valor is None:
            continue

        # Contexto: 50 caracteres ANTES y 35 DESPUÉS. Los indicios de oferta
        # suelen ir antes del número ("solo 79,95€") y el PVP después
        # ("299,90 € (PVP 469,99 €)").
        inicio, fin = m.start(), m.end()
        antes = _ESPACIOS_RE.sub(" ", texto[max(0, inicio - 50):inicio])
        despues = _ESPACIOS_RE.sub(" ", texto[fin:fin + 35])

        if _PVP_CERCA_RE.search(despues) and not _OFERTA_CERCA_RE.search(antes):
            etiqueta = "pvp"
        elif _PVP_CERCA_RE.search(antes):
            etiqueta = "pvp"
        elif _OFERTA_CERCA_RE.search(antes) or _OFERTA_CERCA_RE.search(despues):
            etiqueta = "oferta"
        else:
            etiqueta = "general"

        matches.append(
            PriceMatch(
                valor=valor,
                texto_original=m.group(0).strip(),
                posicion=inicio,
                etiqueta=etiqueta,
            )
        )
    return matches


def extraer_referencias(texto: str, limite: int = 7) -> list[str]:
    """Extrae posibles referencias LEGO y descarta números de piezas.

    Un set LEGO real no tiene 7.541 piezas ni 119,99 referencias: filtramos
    valores con parte decimal, años (hasta 2030) y cantidades absurdas.
    """
    refs: list[str] = []
    for m in _REF_RE.finditer(texto):
        candidato = m.group(1)
        if len(candidato) == 4 and int(candidato) < 2000:
            # 4 dígitos < 2000 son casi siempre años o cantidades.
            continue
        if candidato not in refs and len(refs) < limite:
            refs.append(candidato)
    return refs


# ---------------------------------------------------------------------------
# 5. WATCHLIST
# ---------------------------------------------------------------------------


def _validar_set(referencia: str, datos: dict) -> dict | None:
    """Valida y completa un registro de watchlist. Devuelve None si es inválido."""
    requeridos = (
        "nombre", "tematica", "precio_oficial", "precio_objetivo_compra",
        "precio_estimado_reventa", "estado_eol",
    )
    faltan = [k for k in requeridos if k not in datos]
    if faltan:
        log.warning("Watchlist: set %s ignorado, faltan campos %s", referencia, faltan)
        return None
    if not re.fullmatch(r"\d{4,7}", referencia):
        log.warning("Watchlist: referencia inválida %r ignorada", referencia)
        return None

    limpio = dict(datos)
    for clave in ("precio_oficial", "precio_objetivo_compra", "precio_estimado_reventa"):
        try:
            limpio[clave] = float(limpio[clave])
        except (TypeError, ValueError):
            log.warning("Watchlist: %s tiene %s no numérico", referencia, clave)
            return None
        if limpio[clave] <= 0:
            log.warning("Watchlist: %s tiene %s <= 0", referencia, clave)
            return None

    limpio["piezas"] = int(limpio.get("piezas") or 0)
    limpio["minifiguras_exclusivas"] = bool(limpio.get("minifiguras_exclusivas"))
    limpio["publico_adulto_afol"] = bool(limpio.get("publico_adulto_afol"))
    limpio["alias"] = [str(a) for a in (limpio.get("alias") or [])]
    limpio["notas"] = str(limpio.get("notas") or "")
    return limpio


def cargar_watchlist() -> dict[str, dict]:
    """Fusiona la watchlist de ``config.py`` con la opcional ``watchlist.json``."""
    lista: dict[str, dict] = {}
    for ref, datos in cfg.WATCHLIST.items():
        validado = _validar_set(str(ref), dict(datos))
        if validado:
            lista[str(ref)] = validado

    ruta_json = Path(cfg.WATCHLIST_JSON)
    if ruta_json.exists():
        try:
            with ruta_json.open(encoding="utf-8") as fh:
                extra = json.load(fh)
            if isinstance(extra, dict):
                for ref, datos in extra.items():
                    validado = _validar_set(str(ref), dict(datos))
                    if validado:
                        lista[str(ref)] = validado  # el JSON tiene prioridad
                log.info("Watchlist local fusionada: %d sets", len(extra))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("No se pudo leer %s: %s", ruta_json.name, exc)

    return lista


# ---------------------------------------------------------------------------
# 4a. CRITERIOS DE COLECCIONISTA
# ---------------------------------------------------------------------------


def evaluar_coleccionista(set_: dict) -> tuple[int, str]:
    """Puntúa de 0 a 3 los criterios de inversor/coleccionista.

    Cada criterio vale 1 punto:
        - público adulto AFOL (18+, UCS, Icons, Technic Ultimate...)
        - alto volumen de piezas (>= FILTROS["piezas_alto_volumen"])
        - minifiguras exclusivas
    """
    puntos = 0
    detalle: list[str] = []

    if set_.get("publico_adulto_afol"):
        puntos += 1
        detalle.append("AFOL")
    if set_.get("piezas", 0) >= int(cfg.FILTROS["piezas_alto_volumen"]):
        puntos += 1
        detalle.append(f"{set_['piezas']:,} piezas")
    if set_.get("minifiguras_exclusivas"):
        puntos += 1
        detalle.append("minifiguras exclusivas")

    etiqueta = " + ".join(detalle) if detalle else "sin criterios de coleccionista"
    return puntos, etiqueta


# ---------------------------------------------------------------------------
# 4b. MOTOR FINANCIERO
# ---------------------------------------------------------------------------


def calcular_financiero(precio_compra: float, precio_venta: float) -> Financials:
    """Calcula compra -> gastos de plataforma/envío -> beneficio neto -> ROI.

    Supuestos (configurables en ``cfg.FINANZAS``):
        - Gastos de plataforma = % sobre el precio de venta.
        - Gastos de envío     = % sobre el precio de venta (o importe fijo).
        - ROI                 = beneficio_neto / coste_total * 100
    """
    total_pct = (
        float(cfg.FINANZAS["gastos_plataforma_pct"])
        + float(cfg.FINANZAS["gastos_envio_pct"])
    )
    if total_pct >= float(cfg.FINANZAS["comision_total_max_pct"]):
        log.warning(
            "Comisiones configuradas (%.1f %%) superan el máximo razonable (%.1f %%)",
            total_pct, cfg.FINANZAS["comision_total_max_pct"],
        )

    gastos_plataforma = round(precio_venta * cfg.FINANZAS["gastos_plataforma_pct"] / 100.0, 2)
    gastos_envio = round(precio_venta * cfg.FINANZAS["gastos_envio_pct"] / 100.0, 2)
    coste_total = round(precio_compra + gastos_plataforma + gastos_envio, 2)
    ingresos_netos = round(precio_venta - gastos_plataforma - gastos_envio, 2)
    beneficio = round(ingresos_netos - precio_compra, 2)
    roi = round(beneficio / coste_total * 100.0, 2) if coste_total > 0 else -100.0

    return Financials(
        precio_compra=round(precio_compra, 2),
        gastos_plataforma=gastos_plataforma,
        gastos_envio=gastos_envio,
        coste_total=coste_total,
        ingresos_netos=ingresos_netos,
        beneficio_neto=beneficio,
        roi_pct=roi,
    )


# ---------------------------------------------------------------------------
# 1. RASTREO RSS (robusto ante fallos de conexión)
# ---------------------------------------------------------------------------


def crear_sesion() -> requests.Session:
    """Sesión HTTP reutilizable con User-Agent identificativo."""
    sesion = requests.Session()
    sesion.headers.update({
        "User-Agent": cfg.RED["user_agent"],
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    return sesion


def _descargar_bytes(sesion: requests.Session, url: str) -> bytes | None:
    """Obtiene el cuerpo del feed usando el transporte adecuado al host.

    Devuelve None si el host bloquea la petición (403/429). Se registra el
    motivo para que ``--verbose`` explique por qué falta una fuente.
    """
    host = urlparse(url).hostname or ""
    via_curl = any(h in host for h in cfg.RED.get("hosts_via_curl", []))

    if via_curl:
        # Reddit: requests recibe 429 por su huella TLS; curl pasa.
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", str(cfg.RED["timeout_segundos"]),
             "-A", cfg.RED["user_agent"],
             "-H", "Accept: application/rss+xml, application/xml, text/xml, */*",
             "-w", "\n%{http_code}", url],
            capture_output=True, timeout=cfg.RED["timeout_segundos"] + 5,
        )
        if proc.returncode != 0:
            raise requests.ConnectionError(proc.stderr.decode(errors="replace")[:120])
        cuerpo, _, codigo = proc.stdout.rpartition(b"\n")
        codigo_txt = codigo.decode().strip()
        if codigo_txt in ("403", "429"):
            log.warning("  ↳ HTTP %s (bloqueo/limitación) en %s", codigo_txt, url)
            return None
        return cuerpo

    respuesta = sesion.get(url, timeout=cfg.RED["timeout_segundos"])
    if respuesta.status_code in (403, 429):
        log.warning("  ↳ HTTP %s (bloqueo/limitación) en %s", respuesta.status_code, url)
        return None
    respuesta.raise_for_status()
    return respuesta.content


def descargar_feed(sesion: requests.Session, url: str) -> feedparser.FeedParserDict | None:
    """Descarga y parsea un feed. Devuelve None (sin excepción) si falla.

    Reddit filtra por la huella TLS del cliente, no por cabeceras: ``requests``
    recibe 429 aunque envíe el User-Agent de un navegador. Para los hosts en
    ``RED["hosts_via_curl"]`` se usa ``curl`` como transporte, que sí pasa.
    """
    intentos = int(cfg.RED["reintentos_feedparser"]) + 1
    for intento in range(1, intentos + 1):
        try:
            contenido = _descargar_bytes(sesion, url)
            if contenido is None:
                return None
            feed = feedparser.parse(contenido)
        except requests.RequestException as exc:
            log.warning("  ↳ intento %d/%d fallido (%s)", intento, intentos, type(exc).__name__)
            if intento < intentos:
                time.sleep(min(2 * intento, 5))  # backoff lineal corto
                continue
            return None

        if feed.bozo and not feed.entries:
            log.warning("  ↳ feed mal formado y sin entradas: %s", feed.get("bozo_exception"))
            return None
        return feed
    return None


def construir_entradas(feed_cfg: dict, feed: feedparser.FeedParserDict,
                       limite: int) -> list[Entry]:
    """Normaliza las entradas de un feed a objetos ``Entry``."""
    entradas: list[Entry] = []
    # Prefiltro barato: no procesamos entradas más viejas que la ventana máxima
    # de aceptación (ofertas 45 días / noticias 365 días por defecto).
    # Usamos el límite más amplio para no perder señales de notificaciones EoL.
    max_dias = int(cfg.FILTROS["dias_max_antiguedad_news"])

    for item in feed.entries[:limite]:
        titulo = str(item.get("title") or "")
        resumen_html = str(item.get("summary") or item.get("description") or "")
        texto = normalizar(f"{titulo} {resumen_html}")
        if not texto:
            continue

        fecha = parse_fecha(item)
        if fecha is not None and dias_desde(fecha) > max_dias:
            continue  # entrada demasiado vieja: ni siquiera la procesamos

        entradas.append(
            Entry(
                feed=feed_cfg["nombre"],
                tipo=feed_cfg["tipo"],
                valor_eur=float(feed_cfg["valor_eur"]),
                titulo=_ESPACIOS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(titulo))).strip(),
                texto=texto,
                url=str(item.get("link") or ""),
                fecha=fecha,
                referencias=extraer_referencias(texto),
                precios=extraer_precios(texto),
            )
        )
    return entradas


def _host_con_pausa(host: str) -> bool:
    """¿El host requiere pausa entre peticiones consecutivas?"""
    return any(h in host for h in cfg.RED["hosts_con_pausa"])


def rastrear_feeds(seleccion: list[str] | None = None,
                   limite_override: int | None = None) -> tuple[list[Entry], list[dict]]:
    """Recorre todas las fuentes. Devuelve (entradas, estado_de_cada_feed).

    Una fuente caída NUNCA detiene la ejecución: se reporta en ``estado``
    y se continúa con la siguiente.
    """
    sesion = crear_sesion()
    entradas: list[Entry] = []
    estado: list[dict] = []
    feeds = cfg.FEEDS
    if seleccion:
        buscados = {s.lower() for s in seleccion}
        feeds = [
            f for f in cfg.FEEDS
            if any(b in f["nombre"].lower() or b in f["url"].lower() for b in buscados)
        ]
        if not feeds:
            log.warning("--only-feeds no coincide con ninguna fuente: %s", ", ".join(seleccion))

    limite = int(limite_override or cfg.RED["max_entradas_por_feed"])
    ultimo_host: str | None = None
    for i, feed_cfg in enumerate(feeds, start=1):
        host = urlparse(feed_cfg["url"]).netloc.lower()
        # Pausa cortés entre peticiones consecutivas al mismo host (Reddit).
        if ultimo_host == host and _host_con_pausa(host):
            time.sleep(float(cfg.RED["pausa_entre_peticiones_s"]))
        ultimo_host = host
        log.info("[%d/%d] %s", i, len(feeds), feed_cfg["nombre"])
        feed = descargar_feed(sesion, feed_cfg["url"])
        if feed is None:
            estado.append({"nombre": feed_cfg["nombre"], "ok": False, "entradas": 0})
            continue

        nuevas = construir_entradas(feed_cfg, feed, limite)
        entradas.extend(nuevas)
        estado.append({"nombre": feed_cfg["nombre"], "ok": True, "entradas": len(nuevas)})
        log.info("  ↳ %d entradas relevantes", len(nuevas))

    return entradas, estado


# ---------------------------------------------------------------------------
# 3 + 4. CRUCE CON WATCHLIST Y FILTRO DUAL
# ---------------------------------------------------------------------------


def detectar_referencias_meta(entry: Entry, watchlist: dict[str, dict]) -> set[str]:
    """Referencias explícitas + coincidencias por alias/nombre del set.

    Los nombres se buscan solo en el título (el mejor indicador) para reducir
    falsos positivos: buscar "Titanic" en un resumen largo generaría ruido.
    """
    encontradas: set[str] = {r for r in entry.referencias if r in watchlist}
    titulo_norm = normalizar(entry.titulo)

    for ref, datos in watchlist.items():
        if ref in encontradas:
            continue
        candidatos = [datos["nombre"], *datos.get("alias", [])]
        for nombre in candidatos:
            nombre_norm = normalizar(nombre)
            # Nombres muy cortos generan demasiado ruido: exigimos >= 5 letras.
            if len(nombre_norm) >= 5 and nombre_norm in titulo_norm:
                encontradas.add(ref)
                break
    return encontradas


def confianza_cruce(entry: Entry, referencia: str, set_: dict) -> int:
    """0-100: cuánto creemos que la entrada habla realmente de ESTE set.

    - Referencia numérica explícita: base 75 (95 si está en el título).
    - Coincidencia solo por nombre/alias: base 50 (70 si está en el título).
    - Bonus por palabras financieras (oferta/precio/€/$) y temáticas.
    """
    en_titulo_ref = referencia in entry.titulo
    if referencia in entry.referencias:
        confianza = 95 if en_titulo_ref else 75
    else:
        confianza = 70 if normalizar(set_["nombre"]) in normalizar(entry.titulo) else 50

    if _OFERTA_VIVA_RE.search(entry.texto):
        confianza += 10
    if re.search(r"\bofertas?\b|\bchollo|\bdeal", entry.texto, re.IGNORECASE):
        confianza += 10
    if entry.tipo == "ofertas":
        confianza += 5

    return int(min(100, confianza))


def elegir_precio_compra(entry: Entry, set_: dict) -> tuple[float, PriceMatch | None, str]:
    """Selecciona el precio de compra efectivo y su origen.

    Devuelve ``(precio, match, origen)`` donde ``origen`` es:
        - ``"oferta_detectada"``: hay un precio real con descuento sobre el PVP
        - ``"precio_objetivo_compra"``: no hay oferta; se usa el objetivo

    REGLA ANTI-MSRP (el núcleo del filtrado)
    ----------------------------------------
    Un precio solo cuenta como oferta si es **estrictamente inferior** al PVP
    oficial con un margen mínimo (``descuento_minimo_chollo_pct``). Los blogs
    de noticias citan el PVP al anunciar lanzamientos; sin esta regla, un
    "LEGO 71888 por 279,99 €" (su precio de salida) entraba como chollo con
    ROI calculado sobre una rebaja que nunca existió.

    Anti-ruido adicional: un precio "suelto" (sin €/EUR) en un feed editorial
    se ignora salvo que haya más de un importe; así no se confunden fechas o
    números de set con precios.
    """
    pvp = set_["precio_oficial"]
    objetivo = set_["precio_objetivo_compra"]
    minimo = pvp * float(cfg.FILTROS["precio_minimo_pct_pvp"]) / 100.0
    maximo_oferta = pvp * (1.0 - float(cfg.FILTROS["descuento_minimo_chollo_pct"]) / 100.0)

    precios = entry.precios
    if entry.tipo != "ofertas" and len(precios) < 2:
        tiene_euros_explicitos = bool(re.search(r"€|\beur\b|\beuros?\b", entry.texto))
        if not tiene_euros_explicitos:
            precios = []

    # Preferimos los marcados como "oferta", luego "general"; ignoramos "pvp".
    orden = {"oferta": 0, "general": 1, "pvp": 2}
    candidatos = sorted(
        # Estricto: debe quedar por debajo del umbral anti-MSRP.
        (p for p in precios if minimo <= p.valor <= maximo_oferta),
        key=lambda p: (orden[p.etiqueta], p.valor),
    )
    if candidatos:
        mejor = candidatos[0]
        return mejor.valor, mejor, "oferta_detectada"

    return objetivo, None, "precio_objetivo_compra"


def evaluar_oportunidades(entradas: list[Entry],
                          watchlist: dict[str, dict]) -> tuple[list[Candidate], list[dict]]:
    """Aplica el filtro dual y devuelve (válidas, descartadas con motivo)."""
    filtros = cfg.FILTROS
    roi_minimo = float(cfg.FINANZAS["roi_minimo_pct"])
    beneficio_minimo = float(cfg.FINANZAS["beneficio_neto_minimo_eur"])
    validas: list[Candidate] = []
    descartadas: list[dict] = []

    def descartar(ref: str, entry: Entry, motivo: str) -> None:
        descartadas.append({
            "referencia": ref,
            "set": watchlist[ref]["nombre"],
            "motivo": motivo,
            "titular": entry.titulo,
            "fuente": entry.feed,
            "url": entry.url,
        })

    for entry in entradas:
        # Un chollo de "Nintendo Switch" no es un set LEGO 71374, aunque el
        # alias del set contenga la marca. Sin contexto LEGO explícito, la
        # coincidencia vendrá de otro producto con nombre parecido.
        if not _CONTEXTO_LEGO_RE.search(entry.texto):
            continue

        refs = detectar_referencias_meta(entry, watchlist)
        if not refs:
            continue

        for ref in sorted(refs):
            set_ = watchlist[ref]

            # --- Filtro 0: estado EoL (opcional) -------------------------
            if filtros["solo_estados_retirada"] and set_["estado_eol"] not in filtros["estados_retirada"]:
                descartar(ref, entry, f"estado {set_['estado_eol']} no permite entrar")
                continue

            # --- Filtro A: criterios de coleccionista --------------------
            score_col, col_label = evaluar_coleccionista(set_)
            if score_col < int(filtros["score_minimo_coleccionista"]):
                descartar(ref, entry, f"coleccionista {score_col}/3 (mínimo {filtros['score_minimo_coleccionista']})")
                continue

            # --- Vigencia de la señal -------------------------------------
            edad = dias_desde(entry.fecha)
            limite_dias = (
                int(filtros["dias_max_antiguedad_oferta"])
                if entry.tipo == "ofertas"
                else int(filtros["dias_max_antiguedad_news"])
            )
            if edad > limite_dias:
                descartar(ref, entry, f"entrada de hace {edad:.0f} días (máx {limite_dias})")
                continue

            # --- Precio de compra -----------------------------------------
            precio_compra, match, origen = elegir_precio_compra(entry, set_)
            if match is None:
                # Sin precio real, no hay oportunidad de compra: es una noticia
                # (lanzamiento, rumor, EoL). Con "descartar_sin_precio" activo
                # se descarta siempre, en lugar de generar una "señal_vigilancia"
                # con el precio objetivo como si fuera una oferta.
                if cfg.FILTROS.get("descartar_sin_precio", False):
                    descartar(ref, entry,
                              "sin oferta real (precio = PVP o sin importe)")
                    continue
                # Modo permisivo: solo aceptamos el objetivo si la entrada es
                # una señal de compra (feed de ofertas o lenguaje de precio).
                if entry.tipo != "ofertas" and not _OFERTA_VIVA_RE.search(entry.texto):
                    descartar(ref, entry, "sin precio detectado ni señal de oferta")
                    continue

            # --- Filtro B: motor financiero ------------------------------
            financiero = calcular_financiero(precio_compra, set_["precio_estimado_reventa"])
            if financiero.roi_pct < roi_minimo:
                descartar(ref, entry, f"ROI {financiero.roi_pct:.1f} % < {roi_minimo} %")
                continue
            if financiero.beneficio_neto < beneficio_minimo:
                descartar(ref, entry, f"beneficio {financiero.beneficio_neto:.2f} € < {beneficio_minimo} €")
                continue

            # --- Confianza de cruce y score final -------------------------
            confianza = confianza_cruce(entry, ref, set_)
            if confianza < int(filtros["confianza_minima_cruce"]):
                descartar(ref, entry, f"confianza de cruce {confianza} < mínimo")
                continue

            score_total = calcular_score(
                score_col=score_col,
                confianza=confianza,
                valor_fuente=entry.valor_eur,
                roi_pct=financiero.roi_pct,
                match=match,
                precio_compra=precio_compra,
                set_=set_,
                edad=edad,
                limite_dias=limite_dias,
            )
            if score_total < float(filtros["score_total_minimo"]):
                descartar(ref, entry, f"score total {score_total:.1f} < {filtros['score_total_minimo']}")
                continue

            validas.append(
                Candidate(
                    referencia=ref,
                    set=set_,
                    entry=entry,
                    precio_detectado=match,
                    precio_compra=round(precio_compra, 2),
                    origen_precio=origen,
                    score_coleccionista=score_col,
                    score_coleccionista_label=col_label,
                    confianza_cruce=confianza,
                    financiero=financiero,
                    score_total=score_total,
                )
            )

    # Orden descendente por score: el superagente recibe primero lo mejor.
    validas.sort(key=lambda c: c.score_total, reverse=True)
    return validas, descartadas


def calcular_score(*, score_col: int, confianza: int, valor_fuente: float,
                   roi_pct: float, match: PriceMatch | None, precio_compra: float,
                   set_: dict, edad: float, limite_dias: int) -> float:
    """Score compuesto 0-100 que combina calidad del set y de la oferta.

    Pesos: coleccionista 30, confianza 25, ROI 25, fuente 10, frescura 10.
    """
    peso_roi = 25.0 * min(max(roi_pct, 0.0) / 150.0, 1.0)  # 150 % ROI = puntuación plena
    peso_fuente = 10.0 * max(0.0, min(valor_fuente, 1.0))
    frescura = 1.0 - (edad / limite_dias if limite_dias else 1.0)
    peso_frescura = 10.0 * max(0.0, min(frescura, 1.0))

    # Bonus: precio detectado <= objetivo (compra "esperada").
    bonus_objetivo = 5.0 if match and precio_compra <= set_["precio_objetivo_compra"] else 0.0

    return round(
        (score_col / 3.0) * 30.0 + (confianza / 100.0) * 25.0
        + peso_roi + peso_fuente + peso_frescura + bonus_objetivo,
        1,
    )


# ---------------------------------------------------------------------------
# 5. SERIALIZACIÓN Y EXPORTACIÓN
# ---------------------------------------------------------------------------


def referencia_huella(c: Candidate) -> str:
    """Huella estable para deduplicar set + fuente + precio dentro del JSON."""
    base = "|".join((
        c.referencia,
        c.entry.feed,
        normalize_url(c.entry.url),
        f"{c.precio_compra:.2f}",
    ))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def normalize_url(url: str) -> str:
    """Normaliza URL (sin query/fragmento) para comparar la misma noticia."""
    return url.split("?")[0].split("#")[0].rstrip("/").lower()


# Plazo medio estimado de reventa, en meses, según el estado del set.
# Los sets ya retirados rotan antes (la oferta en segunda mano es escasa);
# los activos requieren esperar a la retirada para capturar la revalorización.
PLAZO_REVENTA_MESES = {
    "Retirado": 1,
    "EoL": 1,
    "Próximo EoL": 6,
    "Activo": 18,
}


def estimar_reventa(set_: dict) -> dict:
    """Escenario de venta: cuándo y a qué precio conviene revender.

    ``precio_venta_sugerido``: el valor estimado de reventa, con un pequeño
    descuento (5 %) para reflejar que en la práctica no se vende al máximo
    teórico del mercado.

    ``valor_verificado``: **False** mientras el precio de reventa venga de
    ``config.py``. La API de Brickset no publica precios de mercado, así que
    esos importes son estimaciones de planificación y el ROI resultante es
    orientativo, no una cotización. Pasa a True si se conecta una fuente de
    precios reales (BrickLink).
    """
    valor = float(set_["precio_estimado_reventa"])
    plazo = int(PLAZO_REVENTA_MESES.get(set_["estado_eol"], 12))
    sugerido = round(valor * 0.95, 2)
    return {
        "precio_venta_sugerido": sugerido,
        "plazo_reventa_meses": plazo,
        "tipo_operacion": "giro_rápido" if plazo <= 3 else "tenencia",
        "valor_verificado": bool(set_.get("reventa_verificada")),
        "horizonte": (
            f"~{plazo} mes(es) — reventa tras la retirada, mercado escaso"
            if plazo <= 3
            else f"~{plazo} mes(es) — mantiene valor y captura la subida del EoL"
        ),
    }


def _contraste_suelo(op: dict, suelo_previo: dict[str, dict]) -> dict:
    """Compara el precio actual con el mínimo histórico visto para el set.

    Responde a la pregunta que el PVP no contesta: "¿esto es realmente barato
    para este set, o ya se ha visto más barato?". Una oferta puede tener buen
    ROI frente al PVP y seguir siendo peor que la de hace tres meses.
    """
    ref = op.get("referencia")
    actual = round(float(op.get("precios", {}).get("compra") or 0), 2)
    origen = op.get("precios", {}).get("origen")
    previo = suelo_previo.get(ref)

    if origen != "oferta_detectada":
        return {
            "precio_actual": actual,
            "precio_minimo_historico": previo["precio"] if previo else None,
            "es_minimo_historico": False,
            "mejora_vs_minimo_pct": None,
            "nota": "sin oferta real: el precio es el objetivo de compra",
        }

    if not previo:
        return {
            "precio_actual": actual,
            "precio_minimo_historico": None,
            "es_minimo_historico": True,  # primera vez que se ve este set
            "mejora_vs_minimo_pct": None,
            "nota": "primer precio real registrado para este set",
        }

    minimo = float(previo["precio"])
    mejora = round((minimo - actual) / minimo * 100.0, 2) if minimo > 0 else 0.0
    es_minimo = actual < minimo
    if es_minimo:
        nota = f"nuevo mínimo histórico (antes {minimo:.2f} €, {mejora:+.1f} %)"
    elif mejora < 0:
        nota = f"por encima del mínimo visto ({minimo:.2f} €)"
    else:
        nota = f"igual al mínimo visto ({minimo:.2f} €)"

    return {
        "precio_actual": actual,
        "precio_minimo_historico": minimo,
        "es_minimo_historico": es_minimo,
        "mejora_vs_minimo_pct": mejora,
        "nota": nota,
    }


def serializar_candidate(c: Candidate, ranking: int | None = None) -> dict:
    """Convierte una ``Candidate`` en el dict que consumirá el superagente."""
    op = asdict(c.financiero)
    descuento_pct = round(
        (1 - c.precio_compra / c.set["precio_oficial"]) * 100.0, 2
    )
    reventa = estimar_reventa(c.set)
    # Margen por mes: útil para comparar un giro rápido de 40 % con una
    # tenencia de 18 meses al 80 %.
    margen_mensual = round(op["roi_pct"] / max(reventa["plazo_reventa_meses"], 1), 2)

    return {
        "id": referencia_huella(c),
        "schema_version": SCHEMA_VERSION,
        "ranking": ranking,
        "tipo": "chollo" if c.origen_precio == "oferta_detectada" else "señal_vigilancia",
        "referencia": c.referencia,
        "nombre": c.set["nombre"],
        "tematica": c.set["tematica"],
        "estado_eol": c.set["estado_eol"],
        "score_total": c.score_total,
        "confianza_cruce": c.confianza_cruce,
        "criterios_coleccionista": {
            "score": c.score_coleccionista,
            "max": 3,
            "detalle": c.score_coleccionista_label,
            "piezas": c.set["piezas"],
            "minifiguras_exclusivas": c.set["minifiguras_exclusivas"],
            "publico_adulto_afol": c.set["publico_adulto_afol"],
        },
        "precios": {
            "oficial": c.set["precio_oficial"],
            "objetivo_compra": c.set["precio_objetivo_compra"],
            "estimado_reventa": c.set["precio_estimado_reventa"],
            "detectado": c.precio_detectado.valor if c.precio_detectado else None,
            "detectado_texto": c.precio_detectado.texto_original if c.precio_detectado else None,
            "compra": c.precio_compra,
            "origen": c.origen_precio,
            "descuento_vs_pvp_pct": descuento_pct,
            "excede_objetivo": bool(c.precio_compra > c.set["precio_objetivo_compra"]),
        },
        "financiero": {**op, "margen_mensual_pct": margen_mensual},
        "plan_reventa": reventa,
        "fuente": {
            "nombre": c.entry.feed,
            "tipo": c.entry.tipo,
            "titulo": c.entry.titulo,
            "url": c.entry.url,
            "fecha": c.entry.fecha.isoformat() if c.entry.fecha else None,
            "antiguedad_dias": round(dias_desde(c.entry.fecha), 1) if c.entry.fecha else None,
        },
        "detectada_en": ahora_iso(),
        "notas_set": c.set.get("notas", ""),
    }


def cargar_historico(ruta: Path) -> dict:
    """Carga el JSON existente; tolera ausencia y corrupción (con backup)."""
    if not ruta.exists():
        return {}
    try:
        with ruta.open(encoding="utf-8") as fh:
            datos = json.load(fh)
        if not isinstance(datos, dict):
            raise ValueError("el JSON raíz no es un objeto")
        return datos
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        backup = ruta.with_suffix(f".corrupto.{int(time.time())}.json")
        log.error("No se pudo leer %s (%s). Backup en %s", ruta.name, exc, backup.name)
        try:
            ruta.replace(backup)
        except OSError:
            pass
        return {}


def actualizar_historico(existente: dict, nuevas: list[dict],
                         estado_feeds: list[dict], resumen: dict,
                         marca_tiempo: str | None = None) -> tuple[dict, set[str]]:
    """Añade oportunidades sin duplicar IDs ni sobrescribir el histórico.

    Devuelve ``(documento_exportable, ids_nuevos)``. Los IDs permiten al
    reporte Markdown saber exactamente qué se añadió en esta ejecución.
    """
    marca_tiempo = marca_tiempo or ahora_iso()
    previas = existente.get("oportunidades") or []
    if not isinstance(previas, list):
        previas = []

    ids = {o.get("id") for o in previas}
    nuevas_unicas, duplicadas = [], 0
    for op in nuevas:
        if op["id"] in ids:
            duplicadas += 1
            continue
        ids.add(op["id"])
        nuevas_unicas.append(op)

    # Histórico completo (más antiguas primero, ya que al final se ordena
    # por fecha en el JSON de salida para lectura humana).
    historico = previas + nuevas_unicas

    # Suelos de TODAS las entradas (sin duplicar): el mínimo real por set.
    suelos = calcular_suelo_historico(historico)

    # Aplica el contraste a TODAS las entradas (antiguas y nuevas), para que
    # cada oferta del JSON diga si bate su mínimo o se queda por encima.
    for op in historico:
        op["contraste_suelo"] = _contraste_suelo(op, suelos)

    # Migración: entradas guardadas antes de existir "plan_reventa"/"tipo"
    # no deben romper la tabla ni dejar el JSON con esquema mixto.
    for op in historico:
        if "plan_reventa" not in op:
            set_ = {"precio_estimado_reventa": (op.get("precios") or {}).get("estimado_reventa", 0),
                    "estado_eol": op.get("estado_eol", "Activo")}
            op["plan_reventa"] = estimar_reventa(set_)
        if "tipo" not in op:
            origen = (op.get("precios") or {}).get("origen", "")
            op["tipo"] = "chollo" if origen == "oferta_detectada" else "señal_vigilancia"
        if "margen_mensual_pct" not in op.get("financiero", {}):
            fin = op.setdefault("financiero", {})
            fin["margen_mensual_pct"] = round(
                fin.get("roi_pct", 0) / max(op["plan_reventa"]["plazo_reventa_meses"], 1), 2)
        # Blindaje del reporte: las tablas leen estos campos con formato numérico
        # directo, así que una entrada antigua incompleta rompería la impresión
        # (KeyError) y tumbaría el ciclo entero justo después de exportar.
        fin = op.setdefault("financiero", {})
        fin.setdefault("beneficio_neto", 0.0)
        fin.setdefault("roi_pct", 0.0)
        precios = op.setdefault("precios", {})
        precios.setdefault("compra", 0.0)
        op.setdefault("score_total", 0.0)

    histograma_temas: dict[str, int] = {}
    for op in historico:
        tema = op.get("tematica", "?")
        histograma_temas[tema] = histograma_temas.get(tema, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "app": {"name": "LegoSearch", "version": APP_VERSION},
        "generado_en": marca_tiempo,
        "resumen": {
            **resumen,
            "total_historicas": len(historico),
            "nuevas_en_esta_ejecucion": len(nuevas_unicas),
            "duplicadas_ignoradas": duplicadas,
            "temas": dict(sorted(histograma_temas.items(), key=lambda kv: -kv[1])),
        },
        "estado_feeds": estado_feeds,
        # Suelo histórico por set: el precio de compra más bajo visto hasta
        # hoy. Sirve de contraste real frente al PVP y a las estimaciones.
        "suelos_historicos": suelos,
        # Ranking global: lo más interesante primero (es lo que consume el bot).
        # Se reasigna el número para que el orden del array y "ranking" casen.
        "oportunidades": _ordenar_y_ranquear(historico),
    }, {op["id"] for op in nuevas_unicas}, suelos


def calcular_suelo_historico(oportunidades: list[dict]) -> dict[str, dict]:
    """Precio de compra más bajo visto para cada set, de todo el histórico.

    Es la referencia de suelo real del sistema: si hoy una oferta está a 320 €
    y en marzo se vio a 285 €, esa oferta no es excepcional aunque su ROI sea
    positivo frente al PVP.

    Solo cuentan los precios de ofertas reales (``oferta_detectada``); los
    ``precio_objetivo_compra`` son metas del usuario, no precios de mercado,
    y contaminarían el suelo con cifras que nunca se pagaron.
    """
    suelo: dict[str, dict] = {}
    for op in oportunidades:
        precios = op.get("precios") or {}
        if precios.get("origen") != "oferta_detectada":
            continue
        compra = precios.get("compra")
        if not compra or compra <= 0:
            continue

        ref = op.get("referencia")
        actual = suelo.get(ref)
        if actual is None or compra < actual["precio"]:
            suelo[ref] = {
                "precio": round(float(compra), 2),
                "fecha": op.get("detectada_en"),
                "fuente": (op.get("fuente") or {}).get("nombre"),
                "url": (op.get("fuente") or {}).get("url"),
            }
    return suelo


def _ordenar_y_ranquear(oportunidades: list[dict]) -> list[dict]:
    """Ordena por interés real, renumera ``ranking`` y depura el histórico.

    El histórico acumula entradas de ejecuciones anteriores. Si las reglas de
    clasificación cambian, esas entradas pueden dejar de ser válidas (por
    ejemplo, una ``señal_vigilancia`` creada cuando aún se permitían entradas
    sin precio). Aquí se descartan para que la tabla solo muestre lo que
    cumple los criterios actuales.

    Además deduplica por set: si hay varias entradas de la misma referencia,
    solo se conserva la de mayor interés.
    """
    filtros = cfg.FILTROS
    vigentes: list[dict] = []
    for op in oportunidades:
        tipo = op.get("tipo")
        if tipo == "señal_vigilancia" and filtros.get("descartar_sin_precio", False):
            continue
        # Una oferta debe seguir cumpliendo el umbral anti-MSRP actual.
        if tipo == "chollo":
            precios = op.get("precios") or {}
            compra = precios.get("compra")
            pvp = precios.get("oficial")
            if compra and pvp:
                maximo = pvp * (1.0 - float(filtros["descuento_minimo_chollo_pct"]) / 100.0)
                if compra > maximo:
                    continue
        vigentes.append(op)

    def clave(o: dict) -> float:
        fin = o.get("financiero") or {}
        return float(o.get("score_total") or 0) + float(fin.get("margen_mensual_pct") or 0) * 0.5

    ordenadas = sorted(vigentes, key=clave, reverse=True)

    # Nos quedamos con la primera aparición de cada referencia (la mejor).
    vistos: set[str] = set()
    unicas = []
    for op in ordenadas:
        ref = op.get("referencia")
        if ref in vistos:
            continue
        vistos.add(ref)
        unicas.append(op)

    for i, op in enumerate(unicas, start=1):
        op["ranking"] = i
    return unicas


def escribir_json_atomico(ruta: Path, datos: dict) -> None:
    """Escribe JSON a un temporal y lo reemplaza: nunca deja un JSON a medias."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp", prefix=ruta.stem)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_path, ruta)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def tabla_oportunidades(oportunidades: list[dict]) -> str:
    """Tabla Markdown ordenada de más a menos interesante.

    Columnas pensadas para decidir rápido si merece la pena comprar:
    ranking, set, compra, reventa con plazo, beneficio, ROI y margen mensual.
    """
    cabecera = (
        "| # | Tipo | Set | Temática | EoL | Compra | Venta (~plazo) | Beneficio | ROI | %/mes | Score |\n"
        "|---|------|-----|----------|-----|--------|----------------|-----------|-----|-------|-------|"
    )
    filas = [cabecera]
    for o in oportunidades:
        f, p = o["financiero"], o["precios"]
        pl = o.get("plan_reventa") or {}
        venta = pl.get("precio_venta_sugerido") or p.get("estimado_reventa") or 0.0
        plazo = pl.get("plazo_reventa_meses", "?")
        tipo = "🟢 chollo" if o.get("tipo") == "chollo" else "👁️ vigilancia"
        # Una reventa no verificada hace que el ROI sea orientativo, no real.
        venta_txt = f"{venta:.2f} € (~{plazo}m)"
        if not pl.get("valor_verificado", False):
            venta_txt += " ⚠"
        # Suelo histórico: marca las ofertas que igualan o baten el mínimo visto.
        min_hist = (o.get("contraste_suelo") or {}).get("precio_minimo_historico")
        if min_hist:
            marca = "🏆 " if (o.get("contraste_suelo") or {}).get("es_minimo_historico") else ""
            compra_txt = f"{marca}{p['compra']:.2f} €"
        else:
            compra_txt = f"{p['compra']:.2f} €"
        filas.append(
            f"| {o.get('ranking') or '-'} "
            f"| {tipo} "
            f"| **{o['referencia']}** {o['nombre'][:30]} "
            f"| {o['tematica'][:16]} "
            f"| {o['estado_eol']} "
            f"| {compra_txt} "
            f"| {venta_txt} "
            f"| **{f['beneficio_neto']:.2f} €** "
            f"| **{f['roi_pct']:.1f} %** "
            f"| {f.get('margen_mensual_pct', 0):.1f} % "
            f"| {o['score_total']:.0f} |"
        )
    return "\n".join(filas)


def imprimir_tabla(oportunidades: list[dict]) -> None:
    """Versión consola de la tabla (sin Markdown, legible en terminal)."""
    if not oportunidades:
        print("  (sin oportunidades)")
        return
    print(f"  {'#':>2} {'TIPO':<6} {'REF':<7} {'COMPRA':>9} {'VENTA':>9} {'BENEF':>9} {'ROI':>7} {'%/m':>6} {'SCORE':>5}  SET")
    for o in oportunidades:
        f, p = o["financiero"], o["precios"]
        pl = o.get("plan_reventa") or {}
        venta = pl.get("precio_venta_sugerido") or p.get("estimado_reventa") or 0.0
        tipo = "CHL" if o.get("tipo") == "chollo" else "VIG"
        print(
            f"  {o.get('ranking') or '-':>2} {tipo:<6} {o['referencia']:<7} "
            f"{p['compra']:>8.2f}€ {venta:>8.2f}€ "
            f"{f['beneficio_neto']:>8.2f}€ {f['roi_pct']:>6.1f}% "
            f"{f.get('margen_mensual_pct', 0):>5.1f}% {o['score_total']:>5.0f}  "
            f"{o['nombre'][:34]}"
        )


def escribir_csv(ruta: Path, oportunidades: list[dict],
                 calendario: list[dict] | None = None) -> None:
    """Exporta el ranking a CSV, listo para Excel / Google Sheets.

    Separador ``;`` y BOM UTF-8: es lo que Excel en español abre bien por
    defecto (con ``;`` porque la coma es el separador decimal).

    Si hay calendario EoL, se añade al final como segundo bloque separado
    por una línea en blanco.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    columnas = [
        ("Ranking", lambda o: o.get("ranking")),
        ("Tipo", lambda o: o.get("tipo")),
        ("Referencia", lambda o: o.get("referencia")),
        ("Nombre", lambda o: o.get("nombre")),
        ("Temática", lambda o: o.get("tematica")),
        ("Estado EoL", lambda o: o.get("estado_eol")),
        ("PVP €", lambda o: o["precios"].get("oficial")),
        ("Compra €", lambda o: o["precios"].get("compra")),
        ("Mín. histórico €", lambda o: (o.get("contraste_suelo") or {}).get("precio_minimo_historico")),
        ("¿Nuevo mínimo?", lambda o: "sí" if (o.get("contraste_suelo") or {}).get("es_minimo_historico") else "no"),
        ("Mejora vs mín. %", lambda o: (o.get("contraste_suelo") or {}).get("mejora_vs_minimo_pct")),
        ("Descuento %", lambda o: o["precios"].get("descuento_vs_pvp_pct")),
        ("Venta €", lambda o: (o.get("plan_reventa") or {}).get("precio_venta_sugerido")),
        ("Meses tenencia", lambda o: (o.get("plan_reventa") or {}).get("plazo_reventa_meses")),
        ("Beneficio €", lambda o: o["financiero"].get("beneficio_neto")),
        ("ROI %", lambda o: o["financiero"].get("roi_pct")),
        ("%/mes", lambda o: o["financiero"].get("margen_mensual_pct")),
        ("Score", lambda o: o.get("score_total")),
        ("Reventa verificada", lambda o: "sí" if (o.get("plan_reventa") or {}).get("valor_verificado") else "no"),
        ("Fuente", lambda o: (o.get("fuente") or {}).get("nombre")),
        ("URL", lambda o: (o.get("fuente") or {}).get("url")),
    ]
    with ruta.open("w", encoding="utf-8-sig", newline="") as fh:
        escritor = csv.writer(fh, delimiter=";")
        escritor.writerow([nombre for nombre, _ in columnas])
        for o in oportunidades:
            fila = []
            for _, fn in columnas:
                try:
                    valor = fn(o)
                except (KeyError, TypeError):
                    valor = ""
                # Coma decimal para Excel en español.
                if isinstance(valor, float):
                    valor = f"{valor:.2f}".replace(".", ",")
                fila.append("" if valor is None else valor)
            escritor.writerow(fila)

        # Segundo bloque: calendario de retiradas (opcional).
        if calendario:
            escritor.writerow([])
            escritor.writerow([])
            escritor.writerow(["CALENDARIO DE RETIRADAS (EoL)"])
            escritor.writerow(["Retirada", "Días", "Referencia", "Nombre",
                               "Temática", "PVP €", "Piezas", "Rating"])
            for s in calendario:
                escritor.writerow([
                    s.get("exit_date", ""),
                    s.get("dias_para_retirada", ""),
                    s.get("referencia", ""),
                    s.get("nombre", ""),
                    s.get("tematica", ""),
                    f"{s['pvp_eur']:.2f}".replace(".", ",") if s.get("pvp_eur") else "",
                    int(s["piezas"]) if s.get("piezas") else "",
                    f"{s['rating']:.1f}".replace(".", ",") if s.get("rating") else "",
                ])


def escribir_reporte_md(ruta: Path, datos: dict, nuevas: list[dict]) -> None:
    """Reporte legible para lectores humanos (no lo usa el agente).

    ``nuevas`` son exactamente las oportunidades añadidas en esta ejecución
    (más fiable que filtrar por fecha, que puede fallar si el proceso se
    reintenta en el mismo día).
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    r = datos["resumen"]
    todas = datos["oportunidades"]
    lineas = [
        f"# LegoSearch — Reporte {datos['generado_en']}",
        "",
        f"- Oportunidades válidas: **{r.get('validas', 0)}**",
        f"- Añadidas nuevas al histórico: **{len(nuevas)}**",
        f"- Duplicadas ignoradas: {r.get('duplicadas_ignoradas', 0)}",
        f"- Descartadas por filtros: {r.get('descartadas', 0)}",
        f"- Feeds OK: {sum(1 for f in datos['estado_feeds'] if f['ok'])}/{len(datos['estado_feeds'])}",
        f"- Sets vigilados: {r.get('sets_en_watchlist', 0)}",
        "",
        "## Ranking completo (más a menos interesante)",
        "",
    ]
    lineas.append(tabla_oportunidades(todas) if todas else "_Sin oportunidades._")
    lineas += [
        "",
        "**Tipos:** 🟢 *chollo* = precio real encontrado en una fuente · "
        "👁️ *vigilancia* = set interesante sin oferta activa (precio = objetivo de compra).",
        "",
        "**%/mes:** ROI repartido entre los meses de tenencia estimados. "
        "Sirve para comparar un giro rápido con una tenencia larga.",
        "",
        "**⚠:** el precio de reventa es una estimación de `config.py`, no una "
        "cotización de mercado (la API de Brickset no publica precios de reventa). "
        "Los importes de compra y PVP sí son oficiales.",
        "",
    ]
    if nuevas:
        lineas += ["## Detalle de las nuevas", ""]
    for o in nuevas:
        f = o["financiero"]
        lineas += [
            f"### [{o['referencia']}] {o['nombre']} — score {o['score_total']}",
            f"- Compra: **{f['precio_compra']} €** ({o['precios']['origen']}), "
            f"reventa estimada {o['precios']['estimado_reventa']} €",
            f"- Beneficio neto: **{f['beneficio_neto']} €** · ROI **{f['roi_pct']} %**",
            f"- Colección: {o['criterios_coleccionista']['detalle']}",
            f"- Fuente: {o['fuente']['nombre']} — {o['fuente']['titulo']}",
            f"- Enlace: {o['fuente']['url']}",
            "",
        ]

    # Calendario EoL: lo más accionable del reporte para un revendedor.
    calendario = datos.get("calendario_eol") or []
    if calendario:
        from calendario import tabla_calendario
        lineas += [
            "## Calendario de retiradas (EoL)",
            "",
            "_Sets que dejan de fabricarse en los próximos meses. "
            "Es la señal de compra: el valor sube tras la retirada._",
            "",
            tabla_calendario(calendario),
            "",
        ]

    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# MODO SIMULACIÓN (sin red) — útil para demos y CI
# ---------------------------------------------------------------------------


def entradas_simuladas() -> list[Entry]:
    """Datos de ejemplo con el mismo formato que produce el rastreo real."""
    ahora = datetime.now(timezone.utc)
    crudas = [
        {
            "feed": "Chollometro (LEGO)", "tipo": "ofertas", "valor_eur": 1.0,
            "titulo": "LEGO 10294 Titanic por 449,99 € en El Corte Inglés",
            "texto": "chollo lego 10294 titanic solo 449,99 € pvp 599,99 €. envio gratis.",
            "url": "https://example.com/deal-titanic", "dias": 1,
        },
        {
            "feed": "Brick Fanatics", "tipo": "eol", "valor_eur": 0.9,
            "titulo": "LEGO Star Wars 75192 Millennium Falcon retirement date confirmed",
            "texto": "lego star wars 75192 millennium falcon ucs retiring soon. retail 849.99 €",
            "url": "https://example.com/eol-falcon", "dias": 10,
        },
        {
            "feed": "Reddit r/legodeals", "tipo": "ofertas", "valor_eur": 1.0,
            "titulo": "[Deal] Hogwarts Castle 71043 at €289.90 (was €469.99)",
            "texto": "deal lego hogwarts castle 71043 at €289.90 was €469.99. worth grabbing?",
            "url": "https://example.com/deal-hogwarts", "dias": 3,
        },
        {
            "feed": "Brickset News", "tipo": "noticias", "valor_eur": 0.8,
            "titulo": "New Technic set 42115 review",
            "texto": "review of technic 42115 lamborghini sian. 379.99 € at launch, great build.",
            "url": "https://example.com/review", "dias": 20,
        },
        {
            "feed": "Reddit r/lego", "tipo": "noticias", "valor_eur": 0.75,
            "titulo": "I bought 10276 Colosseum for 600 € last year",
            "texto": "i bought 10276 colosseum for 600 € last year, now it retails 550.",
            "url": "https://example.com/mine", "dias": 5,
        },
    ]
    salida = []
    for c in crudas:
        salida.append(Entry(
            feed=c["feed"], tipo=c["tipo"], valor_eur=c["valor_eur"],
            titulo=c["titulo"], texto=c["texto"], url=c["url"],
            fecha=datetime.fromtimestamp(ahora.timestamp() - c["dias"] * 86400, tz=timezone.utc),
            referencias=extraer_referencias(c["texto"]),
            precios=extraer_precios(c["texto"]),
        ))
    return salida


# ---------------------------------------------------------------------------
# PIPELINE PRINCIPAL
# ---------------------------------------------------------------------------


def _calendario_si_activo(args: argparse.Namespace) -> list[dict]:
    """Construye el calendario EoL si hay key y está activado en config.

    No es crítico: cualquier fallo se registra y el escaneo continúa sin él.
    """
    conf = getattr(cfg, "CALENDARIO_EOL", {})
    if not conf.get("activo") or getattr(args, "sin_brickset", False):
        return []
    api_key = os.environ.get("BRICKSET_API_KEY")
    if not api_key:
        return []

    try:
        from calendario import construir_calendario
        return construir_calendario(
            api_key,
            temas=conf["temas"],
            años=conf["años"],
            meses_horizonte=conf["meses_horizonte"],
        )
    except Exception as exc:  # noqa: BLE001 — el calendario es opcional
        log.warning("Calendario EoL omitido (%s)", type(exc).__name__)
        return []


def ejecutar(args: argparse.Namespace) -> int:
    """Orquesta el flujo completo. Devuelve el código de salida."""
    inicio = time.time()
    marca_tiempo = ahora_iso()

    # Modo calendario: qué sets dejan de fabricarse y cuándo.
    if getattr(args, "eol", False):
        api_key = os.environ.get("BRICKSET_API_KEY")
        if not api_key:
            log.error("--eol requiere BRICKSET_API_KEY (exporta el .env antes).")
            return 2
        from calendario import construir_calendario, tabla_calendario

        conf = cfg.CALENDARIO_EOL
        sets = construir_calendario(
            api_key,
            temas=conf["temas"],
            años=conf["años"],
            meses_horizonte=conf["meses_horizonte"],
        )
        if args.top:
            # Con --top mostramos los más caros: el interés para revender
            # correlaciona con el PVP, no con la inminencia de la retirada.
            sets = sorted(
                [s for s in sets if s.get("pvp_eur")],
                key=lambda s: -s["pvp_eur"],
            )[: args.top]
        print(tabla_calendario(sets))
        return 0

    # Modo lectura: mostrar el ranking ya publicado, sin rastrear nada.
    if getattr(args, "tabla", False):
        ruta = Path(args.output) if args.output else Path(cfg.OUTPUT_FILE)
        datos = cargar_historico(ruta)
        if not datos:
            log.error("No hay %s. Ejecuta antes sin --tabla.", ruta.name)
            return 2
        opps = datos.get("oportunidades", [])
        if args.top:
            opps = opps[: args.top]
        print(tabla_oportunidades(opps))

        # --tabla --csv RUTA: exporta sin volver a rastrear feeds.
        if getattr(args, "csv", None):
            escribir_csv(Path(args.csv), opps, datos.get("calendario_eol") or [])
            log.info("CSV exportado: %s (%d filas)", args.csv, len(opps))
        return 0

    log.info("LegoSearch v%s (schema %s)", APP_VERSION, SCHEMA_VERSION)

    watchlist = cargar_watchlist()
    if not watchlist:
        log.error("La watchlist está vacía. Añade sets en config.py.")
        return 2

    # Enriquecimiento opcional con precios reales de Brickset. Sin
    # BRICKSET_API_KEY se omite y todo sigue igual que antes.
    if not args.simulate and not getattr(args, "sin_brickset", False):
        from brickset import BricksetClient
        cliente_brickset = BricksetClient.desde_entorno()
        if cliente_brickset.activo:
            cliente_brickset.enriquecer_watchlist(watchlist)
        else:
            log.debug("BRICKSET_API_KEY no definida: se usan los precios de config.py")

    # --- 1 + 2: rastreo y extracción -----------------------------------
    if args.simulate:
        log.info("Modo simulación: sin acceso a red.")
        entradas, estado_feeds = entradas_simuladas(), [{"nombre": "simulado", "ok": True,
                                                        "entradas": 5}]
    else:
        entradas, estado_feeds = rastrear_feeds(
            seleccion=args.only_feeds,
            limite_override=args.max_entradas,
        )
    log.info("Entradas procesadas: %d", len(entradas))

    # --- 3 + 4: cruce, filtro dual y motor financiero ----------------
    validas, descartadas = evaluar_oportunidades(entradas, watchlist)
    log.info("Oportunidades válidas: %d · descartadas: %d", len(validas), len(descartadas))
    if args.verbose:
        for d in descartadas:
            log.debug("  ✗ [%s] %s — %s", d["referencia"], d["set"][:28], d["motivo"])

    # ``validas`` ya viene ordenada por score descendente: el índice es el
    # ranking que verá el consumidor (1 = más interesante).
    nuevas = [serializar_candidate(c, ranking=i) for i, c in enumerate(validas, start=1)]
    resumen = {
        "sets_en_watchlist": len(watchlist),
        "entradas_analizadas": len(entradas),
        "validas": len(validas),
        "descartadas": len(descartadas),
        "descartadas_detalle": descartadas[:200],
        "duracion_segundos": round(time.time() - inicio, 2),
    }

    # --- 5: exportación -----------------------------------------------
    if args.dry_run:
        log.info("--dry-run: %d oportunidades encontradas, no se escribe nada.", len(nuevas))
        imprimir_tabla(nuevas[: args.top] if args.top else nuevas)
        return 0

    # Prioridad CLI > variable de entorno > config. La variable permite montar
    # un volumen persistente en el contenedor sin tocar el comando de arranque.
    salida = Path(args.output) if args.output else Path(
        os.environ.get("LEGOSEARCH_OUTPUT") or cfg.OUTPUT_FILE)
    historico = cargar_historico(salida)
    datos, ids_nuevos, _suelos = actualizar_historico(historico, nuevas, estado_feeds,
                                                      resumen, marca_tiempo)

    # Calendario EoL: se añade al JSON para que el bot pueda avisar de las
    # retiradas próximas sin tener que consultar Brickset por su cuenta.
    calendario = _calendario_si_activo(args)
    if calendario:
        datos["calendario_eol"] = calendario

    escribir_json_atomico(salida, datos)
    log.info("Exportado: %s (%d nuevas, %d totales)", salida,
             datos["resumen"]["nuevas_en_esta_ejecucion"],
             datos["resumen"]["total_historicas"])
    log.info("Ranking (todo el histórico, más a menos interesante):")
    imprimir_tabla(datos["oportunidades"][: args.top] if args.top else datos["oportunidades"])

    if not args.no_report:
        ruta_md = Path(cfg.BASE_DIR) / "reports" / f"reporte_{date.today().isoformat()}.md"
        nuevas_exportadas = [o for o in datos["oportunidades"] if o["id"] in ids_nuevos]
        escribir_reporte_md(ruta_md, datos, nuevas_exportadas)
        log.info("Reporte: %s", ruta_md)

        # CSV equivalente, para abrir en Excel sin pasos intermedios.
        ruta_csv = Path(cfg.BASE_DIR) / "reports" / f"oportunidades_{date.today().isoformat()}.csv"
        escribir_csv(ruta_csv, datos["oportunidades"], datos.get("calendario_eol") or [])
        log.info("CSV: %s", ruta_csv)
    else:
        ruta_csv = None

    # Publicación en la rama runtime-data: es de donde lee el superagente.
    # Opcional (sin GITHUB_PUBLISH_TOKEN no hace nada) y nunca letal: si falla,
    # el JSON ya está guardado en el volumen y el ciclo se considera correcto.
    if not getattr(args, "no_publish", False):
        _publicar_en_github(salida, ruta_csv)

    return 0


def _publicar_en_github(json_path: Path, csv_path: Path | None) -> None:
    """Empuja el resultado a runtime-data sin propagar errores al ciclo."""
    try:
        from publicacion import configuracion, publicar
    except ImportError as exc:  # pragma: no cover - instalación incompleta
        log.warning("Módulo de publicación no disponible (%s)", exc)
        return

    if configuracion() is None:
        log.debug("GITHUB_PUBLISH_TOKEN no definido: no se publica en GitHub.")
        return

    try:
        ok = publicar(json_path, csv_path)
    except Exception as exc:  # noqa: BLE001 — nunca tumbar el worker
        log.warning("Publicación en GitHub falló de forma inesperada (%s)", exc)
        return

    if not ok:
        log.warning("El escaneo NO se publicó en runtime-data; queda en el volumen.")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LegoSearch — monitor lean de oportunidades de inversión en LEGO.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--simulate", action="store_true",
                        help="Ejecuta con datos de ejemplo, sin red.")
    parser.add_argument("--once", action="store_true",
                        help="Ejecución única y salida (útil con cron externo). "
                             "Es el comportamiento por defecto; anula LOOP_MODE.")
    parser.add_argument("--dry-run", action="store_true",
                        help="No escribe ficheros; solo muestra resultados.")
    parser.add_argument("--output", default=None, help="Ruta alternativa del JSON de salida.")
    parser.add_argument("--no-report", action="store_true", help="No genera el reporte Markdown.")
    parser.add_argument("--no-publish", action="store_true",
                        help="No publica el resultado en la rama runtime-data de GitHub.")
    parser.add_argument("--only-feeds", nargs="+", metavar="TEXTO", default=None,
                        help="Filtra fuentes cuyo nombre/URL contenga alguno de estos textos.")
    parser.add_argument("--max-entradas", type=int, default=None,
                        help="Entradas máximas por feed.")
    parser.add_argument("--top", type=int, default=None, metavar="N",
                        help="Muestra solo las N mejores oportunidades en la tabla.")
    parser.add_argument("--tabla", action="store_true",
                        help="Muestra el ranking del JSON existente y sale (sin red).")
    parser.add_argument("--sin-brickset", action="store_true",
                        help="No consulta la API de Brickset aunque haya API key.")
    parser.add_argument("--eol", action="store_true",
                        help="Muestra el calendario de retiradas y sale (requiere Brickset).")
    parser.add_argument("--csv", metavar="RUTA", default=None,
                        help="Exporta además el ranking a CSV (usar con --tabla).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log detallado.")
    parser.add_argument("--loop", action="store_true",
                        help="Modo continuo (worker): repite el escaneo cada --interval. "
                             "Pensado para Docker/Railway. También se activa con LOOP_MODE=1.")
    parser.add_argument("--interval", type=float, default=None, metavar="MIN",
                        help="Minutos entre escaneos en modo --loop "
                             "(por defecto: LEGO_REVISION_MINUTOS, "
                             "SCAN_INTERVAL_MINUTES o 360).")
    return parser.parse_args(argv)


def _segundos_intervalo(args: argparse.Namespace) -> int:
    """Resuelve el intervalo entre escaneos.

    Prioridad: ``--interval`` (CLI) > ``LEGO_REVISION_MINUTOS`` >
    ``SCAN_INTERVAL_MINUTES`` > 360 min (6 h).

    ``LEGO_REVISION_MINUTOS`` es el nombre canónico; ``SCAN_INTERVAL_MINUTES``
    se mantiene para no romper despliegues ya configurados.
    """
    if args.interval is not None and args.interval > 0:
        return int(args.interval * 60)

    for nombre in ("LEGO_REVISION_MINUTOS", "SCAN_INTERVAL_MINUTES"):
        env = os.environ.get(nombre)
        if not env or not env.strip():
            continue
        try:
            minutos = float(env)
            if minutos > 0:
                return int(minutos * 60)
            log.warning("%s=%r debe ser mayor que 0; se ignora.", nombre, env)
        except ValueError:
            log.warning("%s=%r no es un número; se ignora.", nombre, env)

    return 360 * 60


def modo_loop(args: argparse.Namespace) -> int:
    """Ejecuta escaneos indefinidamente, con espera entre uno y otro.

    Diseñado para un contenedor (Railway, Docker, Fly.io...). Características
    clave para no morir ni colgarse en producción:

    - Un fallo en un ciclo se registra y se sigue; el worker no se cae.
    - SIGTERM/SIGINT despiertan la espera de inmediato y salen con código 0
      para que el orquestador no lo cuente como crash en bucle.
    - Un jitter de hasta el 5 % evita que varios contenedores ataquen las
      fuentes exactamente a la misma hora.
    """
    intervalo = _segundos_intervalo(args)
    parada = threading.Event()

    def _apagar(signum: int, _frame: object) -> None:
        log.info("Señal %s recibida: terminando tras el ciclo actual.", signum)
        parada.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _apagar)
        except (ValueError, OSError):  # fuera del hilo principal o no soportada
            pass

    log.info("Worker iniciado · intervalo %d min (%.1f h). Ctrl+C o SIGTERM para parar.",
             intervalo // 60, intervalo / 3600)
    ciclo = 0
    while not parada.is_set():
        ciclo += 1
        inicio = time.monotonic()
        log.info("── Ciclo #%d ──", ciclo)
        try:
            codigo = ejecutar(args)
            if codigo != 0:
                log.warning("Ciclo #%d terminó con código %d.", ciclo, codigo)
        except (requests.RequestException, TimeoutError, OSError) as exc:
            # Red caída, DNS, timeout, TLS... lo normal en un contenedor. No es
            # un bug: se avisa sin traceback y se reintenta en la próxima ronda.
            log.warning("Ciclo #%d falló por red (%s: %s); reintento más tarde.",
                        ciclo, type(exc).__name__, exc)
        except Exception:  # noqa: BLE001 — un ciclo roto no debe tumbar el worker
            log.exception("Ciclo #%d falló; continúo con el siguiente.", ciclo)

        if parada.is_set():
            break
        jitter = random.uniform(0, intervalo * 0.05)
        espera = max(5.0, intervalo + jitter - (time.monotonic() - inicio))
        log.info("Próximo escaneo en %.1f min (o SIGTERM para salir).", espera / 60)
        # ``Event.wait`` vuelve al instante si llega una señal.
        if parada.wait(espera):
            break

    log.info("Worker detenido tras %d ciclo(s).", ciclo)
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    # En Railway/Docker es más cómodo activar el worker por variable de entorno
    # que cambiar el comando de arranque. ``--once`` tiene prioridad: permite
    # lanzar una pasada suelta dentro de una imagen configurada como worker.
    if args.once:
        args.loop = False
    elif os.environ.get("LOOP_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        args.loop = True
    try:
        if args.loop:
            return modo_loop(args)
        return ejecutar(args)
    except KeyboardInterrupt:
        log.warning("Interrumpido por el usuario.")
        return 130
    except Exception:  # noqa: BLE001 — último cortafuegos para no romper el cron
        log.exception("Error inesperado")
        return 1


if __name__ == "__main__":
    sys.exit(main())
