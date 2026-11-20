"""Fase 2 — CLI y pipeline completo sin red (``--simulate``)."""

from __future__ import annotations

import json

import main


# ---------------------------------------------------------------------------
# parse_args / intervalo
# ---------------------------------------------------------------------------

def test_parse_args_defaults():
    args = main.parse_args([])
    assert args.simulate is False
    assert args.output is None
    assert args.top is None


def test_intervalo_desde_cli_tiene_prioridad():
    args = main.parse_args(["--interval", "2"])
    assert main._segundos_intervalo(args) == 120


def test_intervalo_desde_entorno(monkeypatch):
    monkeypatch.setenv("LEGO_REVISION_MINUTOS", "30")
    args = main.parse_args([])
    assert main._segundos_intervalo(args) == 1800


def test_intervalo_alias_antiguo(monkeypatch):
    monkeypatch.delenv("LEGO_REVISION_MINUTOS", raising=False)
    monkeypatch.setenv("SCAN_INTERVAL_MINUTES", "10")
    assert main._segundos_intervalo(main.parse_args([])) == 600


def test_intervalo_por_defecto(monkeypatch):
    monkeypatch.delenv("LEGO_REVISION_MINUTOS", raising=False)
    monkeypatch.delenv("SCAN_INTERVAL_MINUTES", raising=False)
    assert main._segundos_intervalo(main.parse_args([])) == 360 * 60


def test_intervalo_invalido_cae_al_defecto(monkeypatch):
    monkeypatch.setenv("LEGO_REVISION_MINUTOS", "no-numero")
    monkeypatch.delenv("SCAN_INTERVAL_MINUTES", raising=False)
    assert main._segundos_intervalo(main.parse_args([])) == 360 * 60


# ---------------------------------------------------------------------------
# ejecutar: modos que no escriben
# ---------------------------------------------------------------------------

def test_tabla_sin_json_devuelve_error(tmp_path):
    args = main.parse_args(["--tabla", "--output", str(tmp_path / "no.json")])
    assert main.ejecutar(args) == 2


def test_eol_sin_api_key_devuelve_error(monkeypatch):
    monkeypatch.delenv("BRICKSET_API_KEY", raising=False)
    assert main.ejecutar(main.parse_args(["--eol"])) == 2


def test_simulate_dry_run_no_escribe(tmp_path, capsys):
    salida = tmp_path / "no-debe-existir.json"
    args = main.parse_args([
        "--simulate", "--dry-run", "--sin-brickset",
        "--output", str(salida),
    ])
    assert main.ejecutar(args) == 0
    assert not salida.exists()
    assert "TIPO" in capsys.readouterr().out  # imprimió la tabla por consola


# ---------------------------------------------------------------------------
# ejecutar: pipeline completo con exportación a un fichero temporal
# ---------------------------------------------------------------------------

def test_simulate_exporta_json_valido(tmp_path):
    salida = tmp_path / "opportunities.json"
    args = main.parse_args([
        "--simulate", "--sin-brickset", "--no-report", "--no-publish",
        "--output", str(salida),
    ])
    assert main.ejecutar(args) == 0
    assert salida.exists()

    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["schema_version"] == main.SCHEMA_VERSION
    assert datos["app"]["name"] == "LegoSearch"
    assert "resumen" in datos and "oportunidades" in datos
    assert isinstance(datos["oportunidades"], list)


def test_escribir_json_atomico_crea_directorio_y_reemplaza(tmp_path):
    ruta = tmp_path / "sub" / "dir" / "x.json"
    main.escribir_json_atomico(ruta, {"a": 1})
    assert json.loads(ruta.read_text(encoding="utf-8")) == {"a": 1}

    main.escribir_json_atomico(ruta, {"a": 2})
    assert json.loads(ruta.read_text(encoding="utf-8")) == {"a": 2}
    # No quedan temporales sueltos.
    assert list(ruta.parent.glob("*.tmp")) == []


def test_tabla_lee_json_existente_y_exporta_csv(candidate, tmp_path, capsys):
    op = main.serializar_candidate(candidate, ranking=1)
    datos, _, _ = main.actualizar_historico(
        {}, [op], [{"nombre": "T", "ok": True, "entradas": 1}], {"validas": 1},
        marca_tiempo="2026-11-16T00:00:00+00:00",
    )
    ruta = tmp_path / "oportunidades.json"
    main.escribir_json_atomico(ruta, datos)
    csv = tmp_path / "oportunidades.csv"

    args = main.parse_args([
        "--tabla", "--output", str(ruta), "--csv", str(csv), "--top", "1",
    ])
    assert main.ejecutar(args) == 0
    assert "75192" in capsys.readouterr().out
    assert csv.exists()


def test_export_completo_genera_reporte_y_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(main.cfg, "BASE_DIR", str(tmp_path))
    monkeypatch.delenv("GITHUB_PUBLISH_TOKEN", raising=False)
    salida = tmp_path / "out.json"

    assert main.ejecutar(main.parse_args([
        "--simulate", "--sin-brickset", "--output", str(salida),
    ])) == 0

    assert salida.exists()
    reportes = list((tmp_path / "reports").glob("reporte_*.md"))
    csvs = list((tmp_path / "reports").glob("oportunidades_*.csv"))
    assert reportes, "debe generar el reporte Markdown"
    assert csvs, "debe generar el CSV"
    assert "LegoSearch" in reportes[0].read_text(encoding="utf-8")


def test_eol_con_key_imprime_calendario(monkeypatch, capsys):
    import calendario

    monkeypatch.setenv("BRICKSET_API_KEY", "k")
    monkeypatch.setattr(calendario, "construir_calendario", lambda *a, **k: [{
        "exit_date": "2026-12-31", "dias_para_retirada": 45,
        "referencia": "75192", "nombre": "Falcon", "tematica": "Star Wars",
        "pvp_eur": 849.99, "piezas": 7541, "rating": 4.8,
    }])

    assert main.ejecutar(main.parse_args(["--eol", "--top", "1"])) == 0
    assert "75192" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main(): puntos de entrada
# ---------------------------------------------------------------------------

def test_main_simulate_dry_run_devuelve_0():
    assert main.main(["--simulate", "--dry-run", "--sin-brickset"]) == 0


def test_main_once_desactiva_loop():
    args = main.parse_args(["--once", "--simulate", "--dry-run"])
    # --once tiene prioridad; main() lo aplica antes de ejecutar.
    assert args.once is True
