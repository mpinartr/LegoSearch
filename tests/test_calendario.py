"""Fase 1 — Calendario de retiradas (EoL)."""

from __future__ import annotations

import responses

import calendario


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------

def test_normalizar_eol_dentro_del_horizonte():
    set_ = calendario._normalizar_eol(
        {
            "number": "75192", "name": "Falcon", "theme": "Star Wars",
            "subtheme": "UCS", "year": 2017,
            "exitDate": "2026-12-31T00:00:00Z",
            "LEGOCom": {"DE": {"retailPrice": "849.99"}},
            "pieces": 7541, "minifigs": 8, "rating": 4.8,
        },
        "2026-11-16", "2028-11-16",
    )
    assert set_["referencia"] == "75192"
    assert set_["exit_date"] == "2026-12-31"
    assert set_["dias_para_retirada"] == 45
    assert set_["pvp_eur"] == 849.99


def test_normalizar_eol_fuera_de_horizonte_o_sin_fecha():
    pasado = {"number": "1", "exitDate": "2020-01-01"}
    assert calendario._normalizar_eol(pasado, "2026-11-16", "2028-11-16") is None
    assert calendario._normalizar_eol({"number": "1", "exitDate": ""},
                                      "2026-11-16", "2028-11-16") is None


def test_tabla_calendario_vacia():
    assert "Sin retiradas" in calendario.tabla_calendario([])


def test_tabla_calendario_con_una_fila():
    tabla = calendario.tabla_calendario([{
        "exit_date": "2026-12-31", "dias_para_retirada": 45,
        "referencia": "75192", "nombre": "Falcon", "tematica": "Star Wars",
        "pvp_eur": 849.99, "piezas": 7541, "rating": 4.8,
    }])
    assert "75192" in tabla
    assert "45d" in tabla
    assert "849.99 €" in tabla


# ---------------------------------------------------------------------------
# construir_calendario / _consultar
# ---------------------------------------------------------------------------

def test_calendario_sin_key_devuelve_vacio():
    assert calendario.construir_calendario(
        None, temas=["Star Wars"], años=["2024"]) == []


@responses.activate
def test_consultar_tolera_error_http():
    responses.add(responses.GET, f"{calendario.API_URL}/getSets", status=500)
    assert calendario._consultar("key", "Star Wars", "2024", timeout=5) == []


@responses.activate
def test_consultar_devuelve_sets_del_cuerpo():
    responses.add(
        responses.GET, f"{calendario.API_URL}/getSets",
        json={"status": "success", "sets": [{"number": "75192"}]},
    )
    assert calendario._consultar("key", "Star Wars", "2024", timeout=5) == [
        {"number": "75192"}
    ]
