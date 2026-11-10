"""Integración con la API de Brickset (precios oficiales y de mercado).

Por qué existe
--------------
Los feeds RSS son buenos para *detectar* sets (lanzamientos, filtraciones,
ofertas sueltas), pero no publican precios de forma sistemática. Brickset sí:
su API devuelve, por cada set, el PVP oficial, el precio de venta de LEGO.com,
el de Amazon y el valor de mercado de BrickLink (nuevo y usado).

Con eso el motor deja de depender de "que alguien mencione un precio" y puede
calcular el descuento real de cualquier set de la watchlist.

Uso
---
    from brickset import BricksetClient

    cliente = BricksetClient(api_key)      # None => cliente "inactivo"
    datos = cliente.precios("75192")
    # -> {"pvp_oficial": 849.99, "precio_mercado_nuevo": ..., ...} o None

La API requiere una key gratuita (brickset.com/tools/webservices/requestkey).
Sin key, todas las funciones devuelven ``None`` y el motor sigue funcionando
como antes: el enriquecimiento es opcional por diseño.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

API_URL = "https://brickset.com/api/v3.asmx"


class BricksetClient:
    """Cliente mínimo de la API v3 de Brickset, con caché en memoria."""

    def __init__(self, api_key: str | None, *, timeout: int = 15,
                 user_hash: str | None = None) -> None:
        self.api_key = (api_key or "").strip() or None
        self.user_hash = (user_hash or "").strip() or None
        self.timeout = timeout
        self._cache: dict[str, dict[str, Any] | None] = {}

    @classmethod
    def desde_entorno(cls) -> "BricksetClient":
        """Crea el cliente leyendo ``BRICKSET_API_KEY`` del entorno."""
        return cls(
            os.environ.get("BRICKSET_API_KEY"),
            user_hash=os.environ.get("BRICKSET_USER_HASH"),
        )

    @property
    def activo(self) -> bool:
        """True si hay key configurada."""
        return self.api_key is not None

    def precios(self, referencia: str) -> dict[str, Any] | None:
        """Precios conocidos de un set, o ``None`` si no hay datos.

        Claves posibles: ``pvp_oficial``, ``precio_lego``, ``precio_amazon``,
        ``precio_mercado_nuevo``, ``precio_mercado_usado``. Cualquiera puede
        faltar: Brickset solo publica lo que conoce.
        """
        if not self.activo:
            return None
        if referencia in self._cache:
            return self._cache[referencia]

        try:
            respuesta = requests.get(
                f"{API_URL}/getSets",
                params={
                    "apiKey": self.api_key,
                    "userHash": self.user_hash or "",
                    "params": f'{{"setNumber":"{referencia}"}}',
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

        datos = _extraer_precios(sets[0])
        self._cache[referencia] = datos
        return datos

    def enriquecer_watchlist(self, watchlist: dict[str, dict]) -> int:
        """Completa la watchlist con los datos de Brickset, in situ.

        Rellena ``precio_oficial`` y ``precio_estimado_reventa`` cuando faltan
        o son 0, y guarda el detalle en ``brickset``. Devuelve cuántos sets
        se enriquecieron.
        """
        if not self.activo:
            return 0

        enriquecidos = 0
        for ref, set_ in watchlist.items():
            datos = self.precios(ref)
            if not datos:
                continue

            if not set_.get("precio_oficial") and datos.get("pvp_oficial"):
                set_["precio_oficial"] = datos["pvp_oficial"]

            # Valor de reventa: preferimos mercado real (BrickLink nuevo) al
            # estimado manual de la watchlist. Si no hay, respetamos el manual.
            mercado = datos.get("precio_mercado_nuevo")
            if mercado and mercado > 0:
                set_["precio_estimado_reventa"] = round(mercado, 2)

            set_["brickset"] = datos
            enriquecidos += 1

        if enriquecidos:
            log.info("Brickset: %d sets enriquecidos con precios reales", enriquecidos)
        return enriquecidos


def _extraer_precios(set_: dict) -> dict[str, Any]:
    """Normaliza el bloque de precios de Brickset a nuestras claves."""
    lego = set_.get("LEGOCom") or {}
    amazon = set_.get("Amazon") or {}
    bricklink = set_.get("BrickLink") or {}
    return {
        "nombre": set_.get("name"),
        "año": set_.get("year"),
        "pvp_oficial": _num(set_.get("price", {}).get("RRP")),
        "precio_lego": _num((lego.get("retailPrice") or {}).get("amount")),
        "precio_amazon": _num((amazon.get("retailPrice") or {}).get("amount")),
        "precio_mercado_nuevo": _num((bricklink.get("new") or {}).get("amount")),
        "precio_mercado_usado": _num((bricklink.get("used") or {}).get("amount")),
        "fuente_brickset": True,
    }


def _num(valor: Any) -> float | None:
    """Convierte a float positivo; cualquier otra cosa -> None."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None
