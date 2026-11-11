"""Integración con la API de Brickset (PVP oficial y fechas de retirada).

Qué da y qué no
--------------
Verificado contra la API v3 real, **no** devuelve precios de mercado
(BrickLink/Amazon vienen vacíos o no existen). Lo que sí aporta, y es mucho:

- ``LEGOCom``: precio de venta oficial de LEGO.com **por país**. ``DE`` es el
  mejor proxy del PVP europeo en euros.
- ``exitDate``: fecha real de retirada del catálogo.
- ``pieces``, ``minifigs``, ``theme``, ``subtheme``, ``rating``: metadatos
  para completar la watchlist sin teclearlos a mano.

Límite conocido
---------------
El valor de reventa **sigue siendo una estimación** de ``config.py``. Brickset
no lo publica por API. La alternativa real es la API de BrickLink (requiere
OAuth propio), fuera del alcance de esta integración.

Detalle de la API
-----------------
``setNumber`` **exige el sufijo ``-1``** (``"75192-1"``); sin él devuelve
``success`` con 0 sets, un fallo silencioso fácil de no detectar.

Uso
---
    from brickset import BricksetClient

    cliente = BricksetClient.desde_entorno()
    datos = cliente.datos("75192")
    # -> {"pvp_eur": 849.99, "exit_date": "2026-12-31", ...} o None

Sin key, todos los métodos devuelven ``None``/``0`` y el motor sigue igual:
el enriquecimiento es opcional por diseño.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

API_URL = "https://brickset.com/api/v3.asmx"

# Países de LEGOCom en orden de preferencia: DE es el que mejor refleja el
# PVP español en euros. Los demás son respaldo si falta.
PAISES_PREFERIDOS = ("DE", "ES", "FR", "IT", "NL", "UK", "US", "CA")


class BricksetClient:
    """Cliente mínimo de la API v3 de Brickset, con caché en memoria."""

    def __init__(self, api_key: str | None, *, timeout: int = 20) -> None:
        self.api_key = (api_key or "").strip() or None
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any] | None] = {}

    @classmethod
    def desde_entorno(cls) -> "BricksetClient":
        """Crea el cliente leyendo ``BRICKSET_API_KEY`` del entorno."""
        return cls(os.environ.get("BRICKSET_API_KEY"))

    @property
    def activo(self) -> bool:
        """True si hay key configurada."""
        return self.api_key is not None

    def datos(self, referencia: str) -> dict[str, Any] | None:
        """Datos del set según Brickset, o ``None`` si no hay nada útil."""
        if not self.activo:
            return None
        if referencia in self._cache:
            return self._cache[referencia]

        # Sin el sufijo "-1" la API responde success con 0 sets.
        numero = referencia if "-" in referencia else f"{referencia}-1"
        try:
            respuesta = requests.get(
                f"{API_URL}/getSets",
                params={
                    "apiKey": self.api_key,
                    "userHash": "",
                    "params": f'{{"setNumber":"{numero}"}}',
                },
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
        except (requests.RequestException, ValueError) as exc:
            # Un fallo de Brickset nunca debe tumbar el escaneo.
            log.warning("Brickset no respondió para %s (%s)", referencia, type(exc).__name__)
            self._cache[referencia] = None
            return None

        if cuerpo.get("status") != "success":
            log.warning("Brickset: %s para %s", cuerpo.get("message", "error"), referencia)
            self._cache[referencia] = None
            return None

        sets = cuerpo.get("sets") or []
        if not sets:
            self._cache[referencia] = None
            return None

        datos = _normalizar(sets[0])
        self._cache[referencia] = datos
        return datos

    def enriquecer_watchlist(self, watchlist: dict[str, dict]) -> dict[str, int]:
        """Completa la watchlist con datos oficiales, in situ.

        Sobrescribe ``precio_oficial`` cuando Brickset tiene un PVP europeo
        (es más fiable que el valor manual) y ajusta ``estado_eol`` según
        ``exitDate``. **No** toca ``precio_estimado_reventa``: eso no lo
        publica la API.

        Devuelve un recuento para el log resumen.
        """
        cuenta = {"consultados": 0, "con_pvp": 0, "eol_corregido": 0, "sin_datos": 0}
        if not self.activo:
            return cuenta

        hoy = _hoy_iso()
        for ref, set_ in watchlist.items():
            cuenta["consultados"] += 1
            datos = self.datos(ref)
            if not datos:
                cuenta["sin_datos"] += 1
                continue

            set_["brickset"] = datos

            pvp = datos.get("pvp_eur")
            if pvp:
                previo = set_.get("precio_oficial")
                if previo and abs(previo - pvp) > 0.5:
                    log.info(
                        "  PVP corregido %s: %.2f € -> %.2f € (Brickset)",
                        ref, previo, pvp,
                    )
                set_["precio_oficial"] = pvp
                cuenta["con_pvp"] += 1

            estado_previo = set_.get("estado_eol")
            estado = _estado_desde_fecha(datos.get("exit_date"), hoy)
            if estado and estado != estado_previo:
                log.info("  Estado corregido %s: %s -> %s (exitDate %s)",
                         ref, estado_previo, estado, datos.get("exit_date"))
                set_["estado_eol"] = estado
                cuenta["eol_corregido"] += 1

        log.info(
            "Brickset: %d/%d sets con PVP oficial, %d estados corregidos, %d sin datos",
            cuenta["con_pvp"], cuenta["consultados"],
            cuenta["eol_corregido"], cuenta["sin_datos"],
        )
        return cuenta


def _normalizar(set_: dict) -> dict[str, Any]:
    """Extrae los campos útiles del set tal como los devuelve la API."""
    return {
        "nombre": set_.get("name"),
        "año": set_.get("year"),
        "tematica": set_.get("theme"),
        "piezas": set_.get("pieces"),
        "minifigs": set_.get("minifigs"),
        "pvp_eur": _pvp_europeo(set_.get("LEGOCom") or {}),
        "exit_date": _fecha(set_.get("exitDate")),
        "launch_date": _fecha(set_.get("launchDate")),
        "rating": set_.get("rating"),
    }


def _pvp_europeo(lego_com: dict) -> float | None:
    """PVP en euros, priorizando países de la eurozona.

    ``UK`` usa libras y ``US`` dólares: solo se usan como último recurso, y
    en ese caso el valor no es comparable con euros. Si no hay país europeo
    se devuelve ``None`` en lugar de un número engañoso.
    """
    for pais in PAISES_PREFERIDOS:
        bloque = lego_com.get(pais)
        if not isinstance(bloque, dict):
            continue
        precio = _num(bloque.get("retailPrice"))
        if precio and pais in ("DE", "ES", "FR", "IT", "NL"):
            return precio
    return None


def _estado_desde_fecha(exit_date: str | None, hoy: str) -> str | None:
    """Traduce ``exitDate`` a nuestro ``estado_eol``.

    - Fecha pasada  -> "Retirado"
    - Este año      -> "Próximo EoL"
    - Aún lejos     -> "Activo"
    """
    if not exit_date:
        return None
    if exit_date < hoy:
        return "Retirado"
    if exit_date[:4] == hoy[:4]:
        return "Próximo EoL"
    return "Activo"


def _fecha(valor: Any) -> str | None:
    """Recorta un ISO de Brickset ("2026-12-31T00:00:00Z") a "2026-12-31"."""
    if not isinstance(valor, str):
        return None
    encontrado = re.match(r"(\d{4}-\d{2}-\d{2})", valor)
    return encontrado.group(1) if encontrado else None


def _hoy_iso() -> str:
    """Fecha de hoy en ISO, sin depender del reloj del sistema de tests."""
    from datetime import date
    return date.today().isoformat()


def _num(valor: Any) -> float | None:
    """Convierte a float positivo; cualquier otra cosa -> None."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None
