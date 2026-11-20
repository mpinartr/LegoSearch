"""Fase 1 — Serialización, plan de reventa, huellas y suelos históricos."""

from __future__ import annotations

from freezegun import freeze_time

import main


# ---------------------------------------------------------------------------
# normalize_url / referencia_huella
# ---------------------------------------------------------------------------

def test_normalize_url_quita_query_fragmento_y_barra_final():
    assert main.normalize_url("HTTPS://X/Y/?a=1#z") == "https://x/y"
    assert main.normalize_url("https://x/y/") == "https://x/y"


def test_huella_es_estable_y_sensible_al_precio(candidate):
    h1 = main.referencia_huella(candidate)
    h2 = main.referencia_huella(candidate)
    assert h1 == h2
    assert len(h1) == 16

    candidate.precio_compra = 400.0
    assert main.referencia_huella(candidate) != h1


# ---------------------------------------------------------------------------
# estimar_reventa
# ---------------------------------------------------------------------------

def test_estimar_reventa_aplica_descuento_del_5por_ciento():
    plan = main.estimar_reventa({
        "precio_estimado_reventa": 1000.0,
        "estado_eol": "Activo",
        "reventa_verificada": False,
    })
    assert plan["precio_venta_sugerido"] == 950.0
    assert plan["valor_verificado"] is False


def test_estimar_reventa_plazo_por_estado():
    esperado = {"Retirado": 1, "EoL": 1, "Próximo EoL": 6, "Activo": 18}
    for estado, plazo in esperado.items():
        plan = main.estimar_reventa({
            "precio_estimado_reventa": 100.0, "estado_eol": estado,
        })
        assert plan["plazo_reventa_meses"] == plazo


def test_estimar_reventa_estado_desconocido_usa_12_meses():
    plan = main.estimar_reventa({
        "precio_estimado_reventa": 100.0, "estado_eol": "raro",
    })
    assert plan["plazo_reventa_meses"] == 12


def test_estimar_reventa_marca_verificada_si_el_set_lo_indica():
    plan = main.estimar_reventa({
        "precio_estimado_reventa": 100.0, "estado_eol": "Activo",
        "reventa_verificada": True,
    })
    assert plan["valor_verificado"] is True


# ---------------------------------------------------------------------------
# serializar_candidate
# ---------------------------------------------------------------------------

@freeze_time("2026-11-16T10:00:00+00:00")
def test_serializar_candidate_estructura_y_valores(candidate):
    op = main.serializar_candidate(candidate, ranking=1)

    assert op["id"] == main.referencia_huella(candidate)
    assert op["schema_version"] == main.SCHEMA_VERSION
    assert op["ranking"] == 1
    assert op["tipo"] == "chollo"
    assert op["referencia"] == "75192"
    assert op["detectada_en"] == "2026-11-16T10:00:00+00:00"

    # Precios y descuento frente al PVP.
    assert op["precios"]["compra"] == 500.0
    assert op["precios"]["origen"] == "oferta_detectada"
    assert op["precios"]["descuento_vs_pvp_pct"] == 41.18
    assert op["precios"]["excede_objetivo"] is False

    # Financiero y margen mensual (Próximo EoL -> 6 meses).
    assert op["financiero"]["roi_pct"] == 71.0
    assert op["financiero"]["margen_mensual_pct"] == 11.83

    # Plan de reventa y criterios.
    assert op["plan_reventa"]["plazo_reventa_meses"] == 6
    assert op["criterios_coleccionista"]["score"] == 3

    # Contrato mínimo para el superagente.
    for clave in ("id", "schema_version", "ranking", "tipo", "precios",
                  "financiero", "plan_reventa", "fuente"):
        assert clave in op


@freeze_time("2026-11-16T10:00:00+00:00")
def test_serializar_mercado_stock_es_chollo(candidate):
    candidate.origen_precio = "mercado_stock"
    candidate.precio_detectado = main.PriceMatch(600.0, "stock BrickLink", 0, "oferta")
    op = main.serializar_candidate(candidate, ranking=1)
    assert op["tipo"] == "chollo"
    assert op["precios"]["origen"] == "mercado_stock"


@freeze_time("2026-11-16T10:00:00+00:00")
def test_serializar_sin_precio_detectado_es_senal_vigilancia(candidate):
    candidate.precio_detectado = None
    candidate.origen_precio = "precio_objetivo_compra"
    op = main.serializar_candidate(candidate)
    assert op["tipo"] == "señal_vigilancia"
    assert op["precios"]["detectado"] is None
    assert op["precios"]["detectado_texto"] is None


# ---------------------------------------------------------------------------
# calcular_suelo_historico
# ---------------------------------------------------------------------------

def _op(ref, compra, origen, detectada="d", fuente="A", url="u"):
    return {
        "referencia": ref,
        "precios": {"compra": compra, "origen": origen},
        "detectada_en": detectada,
        "fuente": {"nombre": fuente, "url": url},
    }


def test_suelo_historico_toma_el_minimo_de_ofertas_reales():
    ops = [
        _op("75192", 500.0, "oferta_detectada", "d1", "A", "u1"),
        _op("75192", 480.0, "oferta_detectada", "d2", "B", "u2"),
        _op("75192", 100.0, "precio_objetivo_compra", "d3", "C", "u3"),
    ]
    suelo = main.calcular_suelo_historico(ops)
    assert suelo["75192"]["precio"] == 480.0
    assert suelo["75192"]["fuente"] == "B"


def test_suelo_historico_ignora_objetivos_y_vacios():
    ops = [_op("75192", 100.0, "precio_objetivo_compra")]
    assert main.calcular_suelo_historico(ops) == {}


# ---------------------------------------------------------------------------
# _ordenar_y_ranquear
# ---------------------------------------------------------------------------

def test_ordenar_ranquea_y_deduplica_por_set():
    ops = [
        {"referencia": "75192", "tipo": "chollo", "score_total": 80.0,
         "precios": {"compra": 500.0, "oficial": 850.0},
         "financiero": {"margen_mensual_pct": 2.0}},
        {"referencia": "75192", "tipo": "chollo", "score_total": 70.0,
         "precios": {"compra": 480.0, "oficial": 850.0},
         "financiero": {"margen_mensual_pct": 1.0}},
    ]
    resultado = main._ordenar_y_ranquear(ops)
    assert len(resultado) == 1
    assert resultado[0]["score_total"] == 80.0
    assert resultado[0]["ranking"] == 1


def test_ordenar_descarta_senal_vigilancia_si_config_lo_pide():
    ops = [{
        "referencia": "10294", "tipo": "señal_vigilancia", "score_total": 99.0,
        "precios": {"compra": 700.0, "oficial": 680.0},
        "financiero": {"margen_mensual_pct": 0.0},
    }]
    assert main._ordenar_y_ranquear(ops) == []


def test_ordenar_descarta_chollo_que_ya_no_supera_anti_msrp():
    ops = [{
        "referencia": "75192", "tipo": "chollo", "score_total": 90.0,
        "precios": {"compra": 840.0, "oficial": 850.0},
        "financiero": {"margen_mensual_pct": 1.0},
    }]
    assert main._ordenar_y_ranquear(ops) == []
