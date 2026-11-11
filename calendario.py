"""Calendario de retiradas (EoL) a partir de la API de Brickset.

Por qué es útil
--------------
Un set que deja de fabricarse gana valor en el mercado de segunda mano. Si
sabes *cuándo* se retira, sabes *cuándo* comprar: antes del EoL, con descuento,
y vender después. Brickset publica esa fecha en ``exitDate``.

Cómo consulta
-------------
La API no permite filtrar por ``exitDate``, así que se pide por
`tema + año de lanzamiento` y se filtra en local. Cada combinación es una
petición; con los 10 temas y 3 años de ``CALENDARIO_EOL`` son ~30 llamadas.

Límite conocido
---------------
La cobertura de ``exitDate`` es del ~50-60 % según tema: muchos sets no la
tienen publicada. El calendario es una guía, no un censo completo.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import requests

from brickset import API_URL, _num, _pvp_europeo

log = logging.getLogger(__name__)


def construir_calendario(api_key: str | None, *, temas: list[str],
                         años: list[str], meses_horizonte: int = 24,
                         timeout: int = 30) -> list[dict[str, Any]]:
    """Devuelve los sets que se retiran dentro del horizonte, ya ordenados.

    Orden: primero los que se retiran antes (más urgentes para decidir).
    """
    if not api_key:
        return []

    hoy = date.today()
    limite = (hoy + timedelta(days=meses_horizonte * 31)).isoformat()
    resultados: dict[str, dict[str, Any]] = {}
    consultas = 0

    for tema in temas:
        for año in años:
            sets = _consultar(api_key, tema, año, timeout)
            consultas += 1
            for s in sets:
                set_ = _normalizar_eol(s, hoy.isoformat(), limite)
                if set_:
                    resultados[set_["referencia"]] = set_

    log.info(
        "Calendario EoL: %d sets retiran en %d meses (%d consultas a Brickset)",
        len(resultados), meses_horizonte, consultas,
    )
    return sorted(
        resultados.values(),
        key=lambda x: (x["exit_date"], -(x["pvp_eur"] or 0)),
    )


def _consultar(api_key: str, tema: str, año: str, timeout: int) -> list[dict]:
    """Pide los sets de un tema y año. Un fallo puntual no detiene el resto."""
    try:
        respuesta = requests.get(
            f"{API_URL}/getSets",
            params={
                "apiKey": api_key,
                "userHash": "",
                "params": f'{{"theme":"{tema}","year":"{año}","pageSize":"500"}}',
            },
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        respuesta.raise_for_status()
        cuerpo = respuesta.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("Calendario: fallo consultando %s %s (%s)", tema, año, type(exc).__name__)
        return []

    if cuerpo.get("status") != "success":
        return []
    return cuerpo.get("sets") or []


def _normalizar_eol(set_: dict, hoy: str, limite: str) -> dict[str, Any] | None:
    """Filtra y normaliza un set. ``None`` si no interesa para el calendario."""
    exit_date = (set_.get("exitDate") or "")[:10]
    if not exit_date:
        return None
    # Solo retiradas futuras dentro del horizonte: lo pasado ya no es acción.
    if not (hoy <= exit_date <= limite):
        return None

    return {
        "referencia": set_.get("number"),
        "nombre": set_.get("name"),
        "tematica": set_.get("theme"),
        "subtematica": set_.get("subtheme"),
        "año": set_.get("year"),
        "exit_date": exit_date,
        "dias_para_retirada": (date.fromisoformat(exit_date) - date.fromisoformat(hoy)).days,
        "pvp_eur": _pvp_europeo(set_.get("LEGOCom") or {}),
        "piezas": _num(set_.get("pieces")),
        "minifigs": _num(set_.get("minifigs")),
        "rating": set_.get("rating"),
    }


def tabla_calendario(sets: list[dict]) -> str:
    """Tabla Markdown del calendario, agrupada por mes de retirada."""
    if not sets:
        return "_Sin retiradas previstas en el horizonte configurado._"

    lineas = [
        "| Retirada | En | Set | Temática | PVP | Piezas | Rating |",
        "|----------|-----|-----|----------|-----|--------|--------|",
    ]
    for s in sets:
        pvp = f"{s['pvp_eur']:.2f} €" if s.get("pvp_eur") else "—"
        piezas = f"{int(s['piezas'])}" if s.get("piezas") else "—"
        rating = f"{s['rating']:.1f}" if s.get("rating") else "—"
        lineas.append(
            f"| {s['exit_date']} "
            f"| {s['dias_para_retirada']}d "
            f"| **{s['referencia']}** {s['nombre'][:32]} "
            f"| {s['tematica'][:16]} "
            f"| {pvp} "
            f"| {piezas} "
            f"| {rating} |"
        )
    return "\n".join(lineas)
