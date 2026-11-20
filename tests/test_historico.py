"""Fase 1 — Histórico: fusión, deduplicación, migración y contraste de suelo."""

from __future__ import annotations

import json

import main


def _op(id_, ref="75192", compra=500.0, origen="oferta_detectada", score=80.0):
    """Oportunidad con la forma que produce ``serializar_candidate``."""
    return {
        "id": id_,
        "referencia": ref,
        "nombre": "N",
        "tematica": "T",
        "estado_eol": "Activo",
        "score_total": score,
        "precios": {
            "oficial": 1000.0, "objetivo_compra": 700.0,
            "estimado_reventa": 1200.0, "compra": compra, "origen": origen,
        },
        "financiero": {
            "precio_compra": compra, "gastos_plataforma": 0.0,
            "gastos_envio": 0.0, "coste_total": compra, "ingresos_netos": 1200.0,
            "beneficio_neto": 1200.0 - compra, "roi_pct": 10.0,
            "margen_mensual_pct": 1.0,
        },
        "plan_reventa": {
            "precio_venta_sugerido": 1140.0, "plazo_reventa_meses": 18,
            "tipo_operacion": "tenencia", "valor_verificado": False,
            "horizonte": "x",
        },
        "fuente": {
            "nombre": "A", "tipo": "ofertas", "titulo": "t", "url": "u",
            "fecha": None, "antiguedad_dias": None,
        },
        "detectada_en": "2026-11-16T00:00:00+00:00",
        "tipo": "chollo",
    }


# ---------------------------------------------------------------------------
# actualizar_historico
# ---------------------------------------------------------------------------

def test_anade_nueva_y_actualiza_resumen():
    doc, nuevos, suelos = main.actualizar_historico(
        {}, [_op("a")], [{"nombre": "A", "ok": True, "entradas": 1}],
        {"validas": 1}, marca_tiempo="2026-11-16T00:00:00+00:00",
    )
    assert nuevos == {"a"}
    assert doc["resumen"]["total_historicas"] == 1
    assert doc["resumen"]["nuevas_en_esta_ejecucion"] == 1
    assert suelos["75192"]["precio"] == 500.0
    assert doc["schema_version"] == main.SCHEMA_VERSION
    assert doc["generado_en"] == "2026-11-16T00:00:00+00:00"


def test_ignora_duplicados_por_id():
    doc1, _, _ = main.actualizar_historico(
        {}, [_op("a")], [], {"validas": 1},
        marca_tiempo="2026-11-16T00:00:00+00:00",
    )
    doc2, nuevos2, _ = main.actualizar_historico(
        doc1, [_op("a")], [], {"validas": 1},
        marca_tiempo="2026-11-16T01:00:00+00:00",
    )
    assert nuevos2 == set()
    assert doc2["resumen"]["duplicadas_ignoradas"] == 1
    assert doc2["resumen"]["total_historicas"] == 1


def test_migra_entrada_antigua_sin_campos_nuevos():
    legacy = {
        "id": "old", "referencia": "75192", "nombre": "N", "tematica": "T",
        "estado_eol": "Activo", "score_total": 50.0,
        "precios": {"origen": "oferta_detectada", "estimado_reventa": 1000.0,
                    "compra": 700.0},
        "financiero": {"roi_pct": 30.0},
    }
    doc, _, _ = main.actualizar_historico(
        {}, [legacy], [], {}, marca_tiempo="2026-11-16T00:00:00+00:00",
    )
    op = doc["oportunidades"][0]
    assert "plan_reventa" in op
    assert op["tipo"] == "chollo"
    # Activo -> 18 meses -> 30 / 18 = 1.67
    assert op["financiero"]["margen_mensual_pct"] == 1.67
    assert op["financiero"]["beneficio_neto"] == 0.0
    assert op["precios"]["compra"] == 700.0


def test_calcula_temas_en_el_resumen():
    doc, _, _ = main.actualizar_historico(
        {}, [_op("a"), _op("b", ref="10294")], [], {},
        marca_tiempo="2026-11-16T00:00:00+00:00",
    )
    assert doc["resumen"]["temas"] == {"T": 2}


# ---------------------------------------------------------------------------
# _contraste_suelo
# ---------------------------------------------------------------------------

def test_contraste_nuevo_minimo_historico():
    previo = {"75192": {"precio": 480.0}}
    c = main._contraste_suelo(
        {"referencia": "75192", "precios": {"compra": 450.0, "origen": "oferta_detectada"}},
        previo,
    )
    assert c["es_minimo_historico"] is True
    assert c["mejora_vs_minimo_pct"] == 6.25


def test_contraste_por_encima_del_minimo():
    previo = {"75192": {"precio": 480.0}}
    c = main._contraste_suelo(
        {"referencia": "75192", "precios": {"compra": 500.0, "origen": "oferta_detectada"}},
        previo,
    )
    assert c["es_minimo_historico"] is False
    assert c["mejora_vs_minimo_pct"] == -4.17


def test_contraste_primer_precio_real():
    c = main._contraste_suelo(
        {"referencia": "999", "precios": {"compra": 500.0, "origen": "oferta_detectada"}},
        {},
    )
    assert c["es_minimo_historico"] is True
    assert c["precio_minimo_historico"] is None


def test_contraste_sin_oferta_real_no_marca_minimo():
    c = main._contraste_suelo(
        {"referencia": "75192", "precios": {"compra": 700.0, "origen": "precio_objetivo_compra"}},
        {"75192": {"precio": 480.0}},
    )
    assert c["es_minimo_historico"] is False
    # Si ya había un mínimo previo se informa, pero no se marca como superado
    # porque el precio actual no es una oferta real.
    assert c["precio_minimo_historico"] == 480.0
    assert "sin oferta real" in c["nota"]


# ---------------------------------------------------------------------------
# cargar_historico
# ---------------------------------------------------------------------------

def test_cargar_historico_inexistente(tmp_path):
    assert main.cargar_historico(tmp_path / "no.json") == {}


def test_cargar_historico_valido(tmp_path):
    ruta = tmp_path / "h.json"
    datos = {"oportunidades": [{"id": "a"}]}
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    assert main.cargar_historico(ruta) == datos


def test_cargar_historico_corrupto_hace_backup(tmp_path):
    ruta = tmp_path / "h.json"
    ruta.write_text("no es json", encoding="utf-8")
    assert main.cargar_historico(ruta) == {}
    assert not ruta.exists()
    backups = list(tmp_path.glob("h.corrupto.*.json"))
    assert len(backups) == 1
