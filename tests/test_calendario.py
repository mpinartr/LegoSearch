"""Fase 1 — Calendario de retiradas (EoL)."""

from __future__ import annotations

import responses
from freezegun import freeze_time

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


# ---------------------------------------------------------------------------
# Fase C — presupuesto de consultas
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


SET_EOL = {
    "number": "75192", "name": "Falcon", "theme": "Star Wars",
    "exitDate": "2026-12-31T00:00:00Z",
    "LEGOCom": {"DE": {"retailPrice": "849.99"}},
    "pieces": 1, "minifigs": 1, "rating": 4.8,
}


def _fake_get(llamadas):
    def _get(url, params=None, timeout=None, headers=None):
        llamadas.append(params)
        return FakeResp({"status": "success", "sets": [SET_EOL]})
    return _get


@freeze_time("2026-11-16")
def test_construir_calendario_respeta_presupuesto():
    llamadas = []
    sets = calendario.construir_calendario(
        "k", temas=["A", "B"], años=["2023", "2024"],
        get=_fake_get(llamadas), max_consultas=3,
    )
    assert len(llamadas) == 3
    assert len(sets) == 1  # deduplicado por referencia


@freeze_time("2026-11-16")
def test_construir_calendario_sin_limite_consulta_todo():
    llamadas = []
    calendario.construir_calendario(
        "k", temas=["A", "B"], años=["2023", "2024"], get=_fake_get(llamadas),
    )
    assert len(llamadas) == 4


def test_config_calendario_cabe_en_su_presupuesto():
    import config as cfg

    conf = cfg.CALENDARIO_EOL
    assert conf["max_consultas"] >= len(conf["temas"]) * len(conf["años"])


def test_calendario_si_activo_pasa_el_presupuesto(monkeypatch):
    import config as cfg
    import main

    capturado = {}

    def fake(*args, **kwargs):
        capturado.update(kwargs)
        return []

    monkeypatch.setattr(calendario, "construir_calendario", fake)
    monkeypatch.setenv("BRICKSET_API_KEY", "k")

    main._calendario_si_activo(main.parse_args([]))
    assert capturado["max_consultas"] == cfg.CALENDARIO_EOL.get("max_consultas")
