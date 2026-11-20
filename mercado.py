"""Integración con la API de BrickLink (precio de reventa real y liquidez).

Qué aporta
----------
BrickLink es el mercado de segunda mano de LEGO. Su *Price Guide* devuelve
precios de ventas **realizadas** y volumen, justo lo que faltaba para que el
ROI deje de ser una estimación:

- ``precio_venta`` (``guide_type=sold``): precio medio/mín/máx de ventas
  realizadas y **unidades vendidas** (liquidez).
- ``precio_compra`` (``guide_type=stock``): precio mínimo de los anuncios
  actualmente a la venta.

Autenticación
-------------
OAuth 1.0a (HMAC-SHA1). Requiere cuatro valores, gratuitos tras registrar la
app en BrickLink:

    BRICKLINK_CONSUMER_KEY
    BRICKLINK_CONSUMER_SECRET
    BRICKLINK_TOKEN
    BRICKLINK_TOKEN_SECRET

Sin ellos, el cliente queda inactivo y todos los métodos devuelven ``None``:
el escaneo sigue funcionando igual (degradado, nunca roto).

Divisa
------
La Price Guide no devuelve la divisa: los importes vienen en la divisa
configurada en la cuenta de BrickLink. Configura la cuenta en **EUR** para que
sean comparables con el PVP de ``config.py``.

Uso
---
    from mercado import BrickLinkClient

    cliente = BrickLinkClient.desde_entorno()
    datos = cliente.precio_venta("75192")
    # -> {"precio_medio": 1120.0, "unidades": 18, ...} o None
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse

import requests

log = logging.getLogger(__name__)

API_URL = "https://api.bricklink.com/api/store/v1"


# ---------------------------------------------------------------------------
# OAuth 1.0a (HMAC-SHA1)
# ---------------------------------------------------------------------------

def _escape(valor: Any) -> str:
    """Percent-encoding OAuth (RFC 3986): solo deja los caracteres no reservados."""
    return quote(str(valor), safe="-._~")


def _firma_oauth(metodo: str, url: str, params: dict | None, *,
                 consumer_key: str, consumer_secret: str,
                 token: str, token_secret: str,
                 timestamp: int | str | None = None,
                 nonce: str | None = None) -> dict[str, str]:
    """Construye los parámetros OAuth incluida la firma HMAC-SHA1.

    ``timestamp`` y ``nonce`` se pueden inyectar para tests deterministas.
    Devuelve un dict con todos los ``oauth_*`` (incluida ``oauth_signature``).
    """
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_token": token,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(timestamp if timestamp is not None else int(time.time())),
        "oauth_nonce": nonce or _nonce_aleatorio(),
        "oauth_version": "1.0",
    }

    # URL base sin query: los parámetros van en la cadena normalizada.
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    # Todos los parámetros: query de la URL + params + oauth.
    todos: dict[str, str] = {}
    for clave, valor in parse_qsl(parsed.query, keep_blank_values=True):
        todos[clave] = valor
    for clave, valor in (params or {}).items():
        todos[str(clave)] = str(valor)
    todos.update(oauth)

    normalizado = "&".join(
        f"{_escape(clave)}={_escape(todos[clave])}" for clave in sorted(todos)
    )
    base = "&".join((metodo.upper(), _escape(base_url), _escape(normalizado)))

    clave_firma = f"{_escape(consumer_secret)}&{_escape(token_secret)}"
    digest = hmac.new(
        clave_firma.encode("utf-8"), base.encode("utf-8"), hashlib.sha1,
    ).digest()
    firma = base64.b64encode(digest).decode("ascii")

    oauth["oauth_signature"] = firma
    return oauth


def _nonce_aleatorio() -> str:
    return os.urandom(16).hex()


def _cabecera_oauth(oauth: dict[str, str]) -> str:
    """Serializa los parámetros OAuth en la cabecera ``Authorization``."""
    partes = ", ".join(f'{clave}="{_escape(valor)}"' for clave, valor in oauth.items())
    return f"OAuth {partes}"


# ---------------------------------------------------------------------------
# Normalización de la respuesta
# ---------------------------------------------------------------------------

def _num(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero >= 0 else None


def _entero(valor: Any) -> int:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return 0


def _normalizar_precio(datos: Any, referencia: str, guia: str) -> dict[str, Any] | None:
    """Convierte el bloque ``data`` de BrickLink en un dict estable."""
    if isinstance(datos, list):
        datos = datos[0] if datos else None
    if not isinstance(datos, dict):
        return None

    medio = _num(datos.get("avg_price"))
    minimo = _num(datos.get("min_price"))
    if medio is None and minimo is None:
        return None

    return {
        "referencia": referencia,
        "guia": guia,  # "sold" (venta) | "stock" (compra)
        "precio_medio": medio,
        "precio_min": minimo,
        "precio_max": _num(datos.get("max_price")),
        "precio_medio_unidad": _num(datos.get("qty_avg_price")),
        "unidades": _entero(datos.get("total_quantity")),
        "lotes": _entero(datos.get("unit_quantity")),
    }


def _numero_bricklink(referencia: str) -> str:
    """BrickLink usa el sufijo de variante: ``75192`` -> ``75192-1``."""
    return referencia if "-" in referencia else f"{referencia}-1"


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

class BrickLinkClient:
    """Cliente mínimo de la API de BrickLink, con caché en memoria."""

    def __init__(self, consumer_key: str | None, consumer_secret: str | None,
                 token: str | None, token_secret: str | None, *,
                 timeout: int = 20, get=None) -> None:
        self.consumer_key = (consumer_key or "").strip()
        self.consumer_secret = (consumer_secret or "").strip()
        self.token = (token or "").strip()
        self.token_secret = (token_secret or "").strip()
        self.timeout = timeout
        # Seam de test: sustituye la llamada HTTP sin tocar la red.
        self._get = get or requests.get
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    @classmethod
    def desde_entorno(cls) -> "BrickLinkClient":
        """Crea el cliente leyendo las cuatro variables de entorno."""
        return cls(
            os.environ.get("BRICKLINK_CONSUMER_KEY"),
            os.environ.get("BRICKLINK_CONSUMER_SECRET"),
            os.environ.get("BRICKLINK_TOKEN"),
            os.environ.get("BRICKLINK_TOKEN_SECRET"),
        )

    @property
    def activo(self) -> bool:
        """True solo si están las cuatro credenciales."""
        return all((
            self.consumer_key,
            self.consumer_secret,
            self.token,
            self.token_secret,
        ))

    def precio_venta(self, referencia: str, *, nuevo: bool = True) -> dict[str, Any] | None:
        """Precios de ventas realizadas + liquidez, o ``None``."""
        return self._precio(referencia, "sold", nuevo)

    def precio_compra(self, referencia: str, *, nuevo: bool = True) -> dict[str, Any] | None:
        """Precio mínimo de los anuncios actuales, o ``None``."""
        return self._precio(referencia, "stock", nuevo)

    # -- internos ---------------------------------------------------------

    def _precio(self, referencia: str, guia: str, nuevo: bool) -> dict[str, Any] | None:
        if not self.activo:
            return None

        clave = (referencia, guia)
        if clave in self._cache:
            return self._cache[clave]

        numero = _numero_bricklink(referencia)
        url = f"{API_URL}/items/SET/{numero}/price"
        params = {
            "guide_type": guia,
            "new_or_used": "N" if nuevo else "U",
        }
        datos = self._peticion(url, params)
        normalizado = _normalizar_precio(datos, referencia, guia)
        self._cache[clave] = normalizado
        return normalizado

    def _peticion(self, url: str, params: dict[str, str]) -> Any:
        oauth = _firma_oauth(
            "GET", url, params,
            consumer_key=self.consumer_key,
            consumer_secret=self.consumer_secret,
            token=self.token,
            token_secret=self.token_secret,
        )
        cabeceras = {
            "Authorization": _cabecera_oauth(oauth),
            "Accept": "application/json",
        }
        try:
            respuesta = self._get(url, params=params, headers=cabeceras,
                                  timeout=self.timeout)
            respuesta.raise_for_status()
            cuerpo = respuesta.json()
        except (requests.RequestException, ValueError) as exc:
            # Un fallo de BrickLink nunca debe tumbar el escaneo.
            log.warning("BrickLink no respondió (%s)", type(exc).__name__)
            return None

        meta = cuerpo.get("meta") or {}
        codigo = meta.get("code")
        if codigo is not None and codigo != 200:
            log.warning("BrickLink: %s", meta.get("message", "error"))
            return None
        return cuerpo.get("data")


# ---------------------------------------------------------------------------
# Enriquecimiento de la watchlist
# ---------------------------------------------------------------------------

def enriquecer_watchlist(watchlist: dict[str, dict],
                         cliente: BrickLinkClient, *,
                         nuevo: bool = True,
                         incluir_compra: bool = True) -> dict[str, int]:
    """Sustituye los precios estimados por los reales de BrickLink.

    Para cada set con datos de ventas realizadas:
      - ``precio_estimado_reventa`` = precio medio real (``avg_price``).
      - ``reventa_verificada`` = True (hace que el ROI deje de ser orientativo).
      - ``bricklink`` = detalle de ventas (incluye liquidez).
      - ``bricklink_compra`` = precio de compra actual (``stock``), si se pide.
        Permite saber si hoy se puede comprar por debajo del mercado.

    ``incluir_compra`` duplica las peticiones por set (ventas + stock); se puede
    desactivar desde ``config.MERCADO``.

    Devuelve un recuento para el log resumen. Sin datos, no toca el set.
    """
    cuenta = {"consultados": 0, "con_reventa": 0, "con_compra": 0, "sin_datos": 0}
    if not cliente.activo:
        return cuenta

    for ref, set_ in watchlist.items():
        cuenta["consultados"] += 1
        venta = cliente.precio_venta(ref, nuevo=nuevo)
        if not venta or not venta.get("precio_medio"):
            cuenta["sin_datos"] += 1
            continue

        set_["bricklink"] = venta
        set_["precio_estimado_reventa"] = round(float(venta["precio_medio"]), 2)
        set_["reventa_verificada"] = True
        cuenta["con_reventa"] += 1

        if incluir_compra:
            compra = cliente.precio_compra(ref, nuevo=nuevo)
            if compra and (compra.get("precio_min") or compra.get("precio_medio")):
                set_["bricklink_compra"] = compra
                cuenta["con_compra"] += 1

    log.info(
        "BrickLink: %d/%d sets con reventa real, %d con precio de compra, %d sin datos",
        cuenta["con_reventa"], cuenta["consultados"],
        cuenta["con_compra"], cuenta["sin_datos"],
    )
    return cuenta
