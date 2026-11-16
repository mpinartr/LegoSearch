"""Fase 5 — Ramas de descarte de ``evaluar_oportunidades``.

Cada test fuerza un único filtro para comprobar que el descarte se registra
con el motivo correcto y que el pipeline sigue con el resto.
"""

from __future__ import annotations

from datetime import datetime, timezone

import main


def _ahora():
    return datetime.now(timezone.utc)


def _oferta(make_entry):
    # Fecha reciente: la vigencia no debe ser el motivo del descarte.
    return make_entry("solo 500 € lego 75192 falcon", tipo="ofertas",
                      fecha=_ahora())


def test_ignora_entrada_sin_contexto_lego(make_entry, watchlist):
    entry = make_entry("nintendo switch 75192 oferta 200 €", tipo="ofertas")
    validas, descartadas = main.evaluar_oportunidades([entry], watchlist)
    assert validas == []
    assert descartadas == []


def test_ignora_entrada_sin_referencias(make_entry, watchlist):
    entry = make_entry("lego oferta 200 €", tipo="ofertas")
    validas, descartadas = main.evaluar_oportunidades([entry], watchlist)
    assert validas == []
    assert descartadas == []


def test_descarta_por_estado_eol(monkeypatch, make_entry, watchlist):
    monkeypatch.setitem(main.cfg.FILTROS, "solo_estados_retirada", True)
    entry = make_entry("solo 400 € lego 10294 titanic", tipo="ofertas")
    _, descartadas = main.evaluar_oportunidades([entry], watchlist)
    assert len(descartadas) == 1
    assert descartadas[0]["motivo"].startswith("estado Activo")


def test_descarta_por_coleccionista(monkeypatch, make_entry, watchlist):
    monkeypatch.setitem(main.cfg.FILTROS, "score_minimo_coleccionista", 4)
    _, descartadas = main.evaluar_oportunidades([_oferta(make_entry)], watchlist)
    assert "coleccionista 3/3" in descartadas[0]["motivo"]


def test_descarta_por_antiguedad(make_entry, watchlist):
    entry = make_entry(
        "solo 500 € lego 75192 falcon", tipo="ofertas",
        fecha=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    _, descartadas = main.evaluar_oportunidades([entry], watchlist)
    assert descartadas[0]["motivo"].startswith("entrada de hace")


def test_descarta_sin_precio_real(make_entry, watchlist):
    entry = make_entry("lego 75192 falcon noticia", tipo="noticias", fecha=_ahora())
    _, descartadas = main.evaluar_oportunidades([entry], watchlist)
    assert "sin oferta real" in descartadas[0]["motivo"]


def test_descarta_sin_precio_ni_senal_en_modo_permisivo(monkeypatch, make_entry,
                                                        watchlist):
    monkeypatch.setitem(main.cfg.FILTROS, "descartar_sin_precio", False)
    entry = make_entry("lego 75192 falcon noticia", tipo="noticias", fecha=_ahora())
    _, descartadas = main.evaluar_oportunidades([entry], watchlist)
    assert "sin precio detectado ni señal de oferta" in descartadas[0]["motivo"]


def test_descarta_por_roi(monkeypatch, make_entry, watchlist):
    monkeypatch.setitem(main.cfg.FINANZAS, "roi_minimo_pct", 200.0)
    _, descartadas = main.evaluar_oportunidades([_oferta(make_entry)], watchlist)
    assert descartadas[0]["motivo"].startswith("ROI")


def test_descarta_por_beneficio(monkeypatch, make_entry, watchlist):
    monkeypatch.setitem(main.cfg.FINANZAS, "roi_minimo_pct", 0.0)
    monkeypatch.setitem(main.cfg.FINANZAS, "beneficio_neto_minimo_eur", 100000.0)
    _, descartadas = main.evaluar_oportunidades([_oferta(make_entry)], watchlist)
    assert "beneficio" in descartadas[0]["motivo"]


def test_descarta_por_confianza(monkeypatch, make_entry, watchlist):
    monkeypatch.setitem(main.cfg.FILTROS, "confianza_minima_cruce", 101)
    _, descartadas = main.evaluar_oportunidades([_oferta(make_entry)], watchlist)
    assert "confianza de cruce" in descartadas[0]["motivo"]


def test_descarta_por_score_total(monkeypatch, make_entry, watchlist):
    monkeypatch.setitem(main.cfg.FILTROS, "score_total_minimo", 200.0)
    _, descartadas = main.evaluar_oportunidades([_oferta(make_entry)], watchlist)
    assert "score total" in descartadas[0]["motivo"]
