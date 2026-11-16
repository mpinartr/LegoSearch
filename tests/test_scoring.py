"""Fase 1 — Cruce, confianza, selección de precio y scoring."""

from __future__ import annotations

import pytest

import main


# ---------------------------------------------------------------------------
# detectar_referencias_meta
# ---------------------------------------------------------------------------

def test_detectar_por_referencia_explicita(watchlist, make_entry):
    entry = make_entry("lego 75192 falcon oferta")
    assert main.detectar_referencias_meta(entry, watchlist) == {"75192"}


def test_detectar_por_alias_en_titulo(watchlist, make_entry):
    entry = make_entry("lego ucs falcon en oferta")
    assert main.detectar_referencias_meta(entry, watchlist) == {"75192"}


def test_no_detecta_sin_coincidencia(watchlist, make_entry):
    entry = make_entry("lego castillo de otra cosa")
    assert main.detectar_referencias_meta(entry, watchlist) == set()


# ---------------------------------------------------------------------------
# confianza_cruce
# ---------------------------------------------------------------------------

def test_confianza_maxima_si_referencia_en_titulo(watchlist, make_entry):
    entry = make_entry("lego 75192 oferta", titulo="LEGO 75192 oferta", tipo="ofertas")
    assert main.confianza_cruce(entry, "75192", watchlist["75192"]) == 100


def test_confianza_baja_si_solo_coincide_nombre(watchlist, make_entry):
    entry = make_entry("lego falcon", titulo="lego falcon", tipo="noticias")
    assert main.confianza_cruce(entry, "75192", watchlist["75192"]) == 50


# ---------------------------------------------------------------------------
# elegir_precio_compra
# ---------------------------------------------------------------------------

def test_elegir_precio_oferta_detectada(watchlist, make_entry):
    entry = make_entry("solo 500 €", tipo="ofertas")
    precio, match, origen = main.elegir_precio_compra(entry, watchlist["75192"])
    assert precio == 500.0
    assert match is not None and match.etiqueta == "oferta"
    assert origen == "oferta_detectada"


def test_precio_cercano_al_pvp_no_es_oferta(watchlist, make_entry):
    # 820 € > 95 % del PVP (807,49 €): no supera el umbral anti-MSRP.
    entry = make_entry("solo 820 €", tipo="ofertas")
    precio, match, origen = main.elegir_precio_compra(entry, watchlist["75192"])
    assert precio == watchlist["75192"]["precio_objetivo_compra"]
    assert match is None
    assert origen == "precio_objetivo_compra"


def test_elegir_precio_derivado_de_porcentaje(watchlist, make_entry):
    entry = make_entry("30% off", tipo="ofertas")
    precio, match, origen = main.elegir_precio_compra(entry, watchlist["75192"])
    assert precio == round(849.99 * 0.70, 2)
    assert origen == "descuento_porcentual"
    assert match is not None and match.texto_original == "30% off"


def test_sin_precio_cae_al_objetivo(watchlist, make_entry):
    entry = make_entry("lego falcon noticia", tipo="noticias")
    precio, match, origen = main.elegir_precio_compra(entry, watchlist["75192"])
    assert precio == 595.0
    assert match is None
    assert origen == "precio_objetivo_compra"


# ---------------------------------------------------------------------------
# calcular_score
# ---------------------------------------------------------------------------

def test_score_maximo_puede_superar_100_por_bonus(watchlist, make_entry):
    entry = make_entry("solo 500 €", tipo="ofertas")
    score = main.calcular_score(
        score_col=3, confianza=100, valor_fuente=1.0, roi_pct=150.0,
        match=main.PriceMatch(500.0, "500 €", 0, "oferta"),
        precio_compra=500.0, set_=watchlist["75192"], edad=1.0,
        limite_dias=45, score_retirada=2,
    )
    assert score == 104.8


def test_score_cero_en_el_peor_caso(watchlist):
    score = main.calcular_score(
        score_col=0, confianza=0, valor_fuente=0.0, roi_pct=0.0,
        match=None, precio_compra=1000.0, set_=watchlist["75192"],
        edad=45.0, limite_dias=45, score_retirada=0,
    )
    assert score == 0.0


def test_bonus_objetivo_solo_si_compra_por_debajo(watchlist, make_entry):
    base = dict(score_col=1, confianza=50, valor_fuente=0.5, roi_pct=40.0,
                set_=watchlist["75192"], edad=5.0, limite_dias=45,
                score_retirada=1)
    con_bonus = main.calcular_score(
        **base, match=main.PriceMatch(500.0, "500", 0, "oferta"), precio_compra=500.0)
    sin_bonus = main.calcular_score(
        **base, match=main.PriceMatch(600.0, "600", 0, "oferta"), precio_compra=600.0)
    assert con_bonus - sin_bonus == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# evaluar_oportunidades (integración de la lógica pura)
# ---------------------------------------------------------------------------

def test_evaluar_oportunidades_end_to_end(watchlist, entradas):
    validas, descartadas = main.evaluar_oportunidades(entradas, watchlist)
    assert isinstance(validas, list) and isinstance(descartadas, list)
    # El ejemplo del Titanic (10294) es una oferta real y debe entrar.
    assert any(c.referencia == "10294" for c in validas)
    # Las válidas salen ordenadas por score descendente.
    scores = [c.score_total for c in validas]
    assert scores == sorted(scores, reverse=True)
