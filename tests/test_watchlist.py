"""Fase 1 — Watchlist: validación y fusión de fuentes."""

from __future__ import annotations

import json

import main


SET_VALIDO = {
    "nombre": "Millennium Falcon UCS",
    "tematica": "Star Wars UCS",
    "precio_oficial": 849.99,
    "precio_objetivo_compra": 595.00,
    "precio_estimado_reventa": 1150.00,
    "estado_eol": "Próximo EoL",
    "piezas": 7541,
    "minifiguras_exclusivas": True,
    "publico_adulto_afol": True,
    "alias": ["UCS Falcon"],
    "notas": "nota",
}


# ---------------------------------------------------------------------------
# _validar_set
# ---------------------------------------------------------------------------

def test_validar_set_completo_normaliza_tipos():
    set_ = main._validar_set("75192", dict(SET_VALIDO))
    assert set_ is not None
    assert set_["precio_oficial"] == 849.99
    assert set_["piezas"] == 7541
    assert set_["alias"] == ["UCS Falcon"]


def test_validar_set_faltan_campos_devuelve_none():
    incompleto = {"nombre": "solo nombre"}
    assert main._validar_set("75192", incompleto) is None


def test_validar_set_referencia_invalida():
    assert main._validar_set("abc", dict(SET_VALIDO)) is None
    assert main._validar_set("12", dict(SET_VALIDO)) is None


def test_validar_set_precio_no_numerico_o_no_positivo():
    no_num = {**SET_VALIDO, "precio_oficial": "mucho"}
    assert main._validar_set("75192", no_num) is None
    cero = {**SET_VALIDO, "precio_oficial": 0}
    assert main._validar_set("75192", cero) is None


def test_validar_set_opcionales_tienen_defaults():
    minimo = {k: SET_VALIDO[k] for k in (
        "nombre", "tematica", "precio_oficial", "precio_objetivo_compra",
        "precio_estimado_reventa", "estado_eol",
    )}
    set_ = main._validar_set("75192", minimo)
    assert set_["piezas"] == 0
    assert set_["alias"] == []
    assert set_["notas"] == ""
    assert set_["minifiguras_exclusivas"] is False


# ---------------------------------------------------------------------------
# cargar_watchlist
# ---------------------------------------------------------------------------

def test_cargar_watchlist_solo_config(monkeypatch, tmp_path):
    monkeypatch.setattr(main.cfg, "WATCHLIST", {"75192": dict(SET_VALIDO)})
    monkeypatch.setattr(main.cfg, "WATCHLIST_JSON", str(tmp_path / "no-existe.json"))
    assert set(main.cargar_watchlist()) == {"75192"}


def test_cargar_watchlist_json_tiene_prioridad(monkeypatch, tmp_path):
    monkeypatch.setattr(main.cfg, "WATCHLIST", {"75192": dict(SET_VALIDO)})
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(
        json.dumps({"75192": {**SET_VALIDO, "precio_oficial": 700.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main.cfg, "WATCHLIST_JSON", str(ruta))
    assert main.cargar_watchlist()["75192"]["precio_oficial"] == 700.0


def test_cargar_watchlist_ignora_json_corrupto(monkeypatch, tmp_path):
    monkeypatch.setattr(main.cfg, "WATCHLIST", {"75192": dict(SET_VALIDO)})
    ruta = tmp_path / "watchlist.json"
    ruta.write_text("{ esto no es json", encoding="utf-8")
    monkeypatch.setattr(main.cfg, "WATCHLIST_JSON", str(ruta))
    # No lanza: conserva la watchlist de config.py.
    assert set(main.cargar_watchlist()) == {"75192"}
