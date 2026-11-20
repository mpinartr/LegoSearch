"""Fase 1 — Formatos de salida: tabla, CSV y reporte Markdown."""

from __future__ import annotations

from freezegun import freeze_time

import main


def _datos_con_una_op(candidate):
    with freeze_time("2026-11-16T10:00:00+00:00"):
        op = main.serializar_candidate(candidate, ranking=1)
        datos, _, _ = main.actualizar_historico(
            {}, [op], [{"nombre": "Test", "ok": True, "entradas": 1}],
            {"validas": 1}, marca_tiempo="2026-11-16T00:00:00+00:00",
        )
    return datos


def test_tabla_oportunidades_cabecera_y_fila(candidate):
    datos = _datos_con_una_op(candidate)
    tabla = main.tabla_oportunidades(datos["oportunidades"])
    lineas = tabla.splitlines()
    assert lineas[0].startswith("| # | Tipo | Set |")
    assert any("75192" in linea for linea in lineas[1:])
    assert "🟢 chollo" in tabla


def test_tabla_vacia_solo_tiene_cabecera():
    tabla = main.tabla_oportunidades([])
    assert tabla.count("|") > 0
    assert len(tabla.splitlines()) == 2


def test_tabla_marca_minimo_historico(candidate):
    datos = _datos_con_una_op(candidate)
    op = datos["oportunidades"][0]
    op["contraste_suelo"] = {"precio_minimo_historico": 520.0,
                             "es_minimo_historico": True}
    tabla = main.tabla_oportunidades([op])
    assert "🏆" in tabla


def test_imprimir_tabla_sin_oportunidades(capsys):
    main.imprimir_tabla([])
    assert "sin oportunidades" in capsys.readouterr().out


def test_imprimir_tabla_con_oportunidades(candidate, capsys):
    datos = _datos_con_una_op(candidate)
    main.imprimir_tabla(datos["oportunidades"])
    salida = capsys.readouterr().out
    assert "75192" in salida
    assert "CHL" in salida


def test_escribir_csv_usa_separador_punto_y_coma_y_coma_decimal(
        candidate, tmp_path):
    datos = _datos_con_una_op(candidate)
    ruta = tmp_path / "oportunidades.csv"
    main.escribir_csv(ruta, datos["oportunidades"])

    texto = ruta.read_text(encoding="utf-8-sig")
    lineas = texto.splitlines()
    assert lineas[0].startswith("Ranking;Tipo;Referencia;")
    assert "75192" in lineas[1]
    assert "500,00" in lineas[1]  # decimal con coma para Excel ES


def test_escribir_csv_anade_bloque_de_calendario(candidate, tmp_path):
    datos = _datos_con_una_op(candidate)
    ruta = tmp_path / "oportunidades.csv"
    main.escribir_csv(ruta, datos["oportunidades"], calendario=[{
        "exit_date": "2026-12-31", "dias_para_retirada": 45,
        "referencia": "75192", "nombre": "Falcon", "tematica": "Star Wars",
        "pvp_eur": 849.99, "piezas": 7541, "rating": 4.8,
    }])
    texto = ruta.read_text(encoding="utf-8-sig")
    assert "CALENDARIO DE RETIRADAS (EoL)" in texto


def test_escribir_reporte_md_contiene_resumen_y_detalle(candidate, tmp_path):
    datos = _datos_con_una_op(candidate)
    ruta = tmp_path / "reporte.md"
    main.escribir_reporte_md(ruta, datos, datos["oportunidades"])

    texto = ruta.read_text(encoding="utf-8")
    assert texto.startswith("# LegoSearch — Reporte")
    assert "Oportunidades válidas: **1**" in texto
    assert "## Ranking completo" in texto
    assert "75192" in texto
    assert "## Detalle de las nuevas" in texto


def test_reporte_md_incluye_tabla_de_calendario(candidate, tmp_path):
    datos = _datos_con_una_op(candidate)
    datos["calendario_eol"] = [{
        "exit_date": "2026-12-31", "dias_para_retirada": 45,
        "referencia": "75192", "nombre": "Falcon", "tematica": "Star Wars",
        "pvp_eur": 849.99, "piezas": 7541, "rating": 4.8,
    }]
    ruta = tmp_path / "reporte.md"
    main.escribir_reporte_md(ruta, datos, [])
    texto = ruta.read_text(encoding="utf-8")
    assert "## Calendario de retiradas (EoL)" in texto
    assert "75192" in texto
