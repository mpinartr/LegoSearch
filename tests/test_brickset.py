"""Fase 1 — Integración con Brickset (helpers puros + cliente con HTTP simulado)."""

from __future__ import annotations

from freezegun import freeze_time

import responses

import brickset


SET_API = {
    "name": "Millennium Falcon UCS",
    "year": 2017,
    "theme": "Star Wars",
    "pieces": 7541,
    "minifigs": 8,
    "LEGOCom": {"DE": {"retailPrice": "849.99"}},
    "exitDate": "2026-12-31T00:00:00Z",
    "launchDate": "2017-09-01T00:00:00Z",
    "rating": 4.8,
}


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------

def test_pvp_europeo_prefiere_eurozona():
    assert brickset._pvp_europeo({"DE": {"retailPrice": "849.99"}}) == 849.99
    assert brickset._pvp_europeo({
        "US": {"retailPrice": "849.99"}, "ES": {"retailPrice": "800"},
    }) == 800.0


def test_pvp_europeo_solo_no_euro_devuelve_none():
    assert brickset._pvp_europeo({"UK": {"retailPrice": "699.99"}}) is None
    assert brickset._pvp_europeo({}) is None


def test_pvp_europeo_ignora_precio_invalido_y_usa_el_siguiente():
    assert brickset._pvp_europeo({
        "DE": {"retailPrice": "abc"}, "ES": {"retailPrice": "800"},
    }) == 800.0


def test_fecha_recorta_iso():
    assert brickset._fecha("2026-12-31T00:00:00Z") == "2026-12-31"
    assert brickset._fecha(None) is None
    assert brickset._fecha(123) is None


@freeze_time("2026-11-16")
def test_estado_desde_fecha():
    assert brickset._estado_desde_fecha("2025-01-01", "2026-11-16") == "Retirado"
    assert brickset._estado_desde_fecha("2026-12-31", "2026-11-16") == "Próximo EoL"
    assert brickset._estado_desde_fecha("2028-01-01", "2026-11-16") == "Activo"
    assert brickset._estado_desde_fecha(None, "2026-11-16") is None


def test_num_convierte_solo_positivos():
    assert brickset._num("10") == 10.0
    assert brickset._num("-5") is None
    assert brickset._num("x") is None


def test_normalizar_extrae_campos_utiles():
    datos = brickset._normalizar(SET_API)
    assert datos["nombre"] == "Millennium Falcon UCS"
    assert datos["pvp_eur"] == 849.99
    assert datos["exit_date"] == "2026-12-31"
    assert datos["launch_date"] == "2017-09-01"


# ---------------------------------------------------------------------------
# BricksetClient
# ---------------------------------------------------------------------------

def test_cliente_sin_key_es_inactivo_y_no_consulta():
    cliente = brickset.BricksetClient(None)
    assert cliente.activo is False
    assert cliente.datos("75192") is None
    assert brickset.BricksetClient("").activo is False


@responses.activate
def test_cliente_consulta_normaliza_y_cachea():
    responses.add(
        responses.GET, f"{brickset.API_URL}/getSets",
        json={"status": "success", "sets": [SET_API]},
    )
    cliente = brickset.BricksetClient("key")
    datos = cliente.datos("75192")
    assert datos["pvp_eur"] == 849.99
    assert len(responses.calls) == 1

    # Segunda llamada: sale de caché, no hay nueva petición.
    cliente.datos("75192")
    assert len(responses.calls) == 1


@responses.activate
def test_cliente_tolera_error_http():
    responses.add(responses.GET, f"{brickset.API_URL}/getSets", status=500)
    cliente = brickset.BricksetClient("key")
    assert cliente.datos("75192") is None


@responses.activate
def test_cliente_tolera_status_no_success():
    responses.add(
        responses.GET, f"{brickset.API_URL}/getSets",
        json={"status": "error", "message": "invalid key"},
    )
    assert brickset.BricksetClient("key").datos("75192") is None


@responses.activate
@freeze_time("2026-11-16")
def test_enriquecer_watchlist_corrige_pvp_y_estado():
    responses.add(
        responses.GET, f"{brickset.API_URL}/getSets",
        json={"status": "success", "sets": [SET_API]},
    )
    watchlist = {"75192": {"precio_oficial": 800.0, "estado_eol": "Activo"}}
    cuenta = brickset.BricksetClient("key").enriquecer_watchlist(watchlist)

    assert watchlist["75192"]["precio_oficial"] == 849.99
    assert watchlist["75192"]["estado_eol"] == "Próximo EoL"
    assert watchlist["75192"]["brickset"]["exit_date"] == "2026-12-31"
    assert cuenta["con_pvp"] == 1
    assert cuenta["eol_corregido"] == 1
