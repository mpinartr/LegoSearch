"""Fase 2 — Publicación en GitHub con HTTP simulado (sin red real)."""

from __future__ import annotations

import json

import responses

import publicacion


API = "https://api.github.com/repos/o/r/contents"


def _entorno(monkeypatch):
    monkeypatch.setenv("GITHUB_PUBLISH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_PUBLISH_REPO", "o/r")
    monkeypatch.setenv("GITHUB_PUBLISH_BRANCH", "br")


def _json_file(tmp_path):
    ruta = tmp_path / "o.json"
    ruta.write_text(json.dumps({"generado_en": "2026-11-16T00:00:00+00:00"}),
                    encoding="utf-8")
    return ruta


@responses.activate
def test_publicar_crea_fichero_si_no_existe(monkeypatch, tmp_path):
    _entorno(monkeypatch)
    ruta = _json_file(tmp_path)
    url = f"{API}/opportunities.json"
    responses.add(responses.GET, url, status=404)
    responses.add(responses.PUT, url, status=201, json={"content": {}})

    assert publicacion.publicar(ruta, timeout=5) is True

    cuerpo = json.loads(responses.calls[1].request.body)
    assert cuerpo["branch"] == "br"
    assert "sha" not in cuerpo  # creación: no se envía SHA


@responses.activate
def test_publicar_actualiza_fichero_existente(monkeypatch, tmp_path):
    _entorno(monkeypatch)
    ruta = _json_file(tmp_path)
    url = f"{API}/opportunities.json"
    responses.add(responses.GET, url, json={"sha": "abc"})
    responses.add(responses.PUT, url, status=200, json={"content": {}})

    assert publicacion.publicar(ruta, timeout=5) is True

    cuerpo = json.loads(responses.calls[1].request.body)
    assert cuerpo["sha"] == "abc"


@responses.activate
def test_publicar_si_el_put_falla_devuelve_false(monkeypatch, tmp_path):
    _entorno(monkeypatch)
    ruta = _json_file(tmp_path)
    url = f"{API}/opportunities.json"
    responses.add(responses.GET, url, json={"sha": "abc"})
    responses.add(responses.PUT, url, status=422, json={"message": "bad"})

    assert publicacion.publicar(ruta, timeout=5) is False


@responses.activate
def test_publicar_si_falla_la_consulta_de_sha_no_intenta_escribir(
        monkeypatch, tmp_path):
    _entorno(monkeypatch)
    ruta = _json_file(tmp_path)
    url = f"{API}/opportunities.json"
    responses.add(responses.GET, url, status=500)

    assert publicacion.publicar(ruta, timeout=5) is False
    # Solo la consulta de SHA; no debe haber PUT.
    assert len(responses.calls) == 1


@responses.activate
def test_publicar_tambien_sube_el_csv_si_existe(monkeypatch, tmp_path):
    _entorno(monkeypatch)
    ruta = _json_file(tmp_path)
    csv_ruta = tmp_path / "o.csv"
    csv_ruta.write_text("a;b\n1;2\n", encoding="utf-8")

    for nombre in ("opportunities.json", "oportunidades.csv"):
        url = f"{API}/{nombre}"
        responses.add(responses.GET, url, status=404)
        responses.add(responses.PUT, url, status=201, json={"content": {}})

    assert publicacion.publicar(ruta, csv_ruta, timeout=5) is True
    subidos = [c.request.url for c in responses.calls if c.request.method == "PUT"]
    assert any("opportunities.json" in u for u in subidos)
    assert any("oportunidades.csv" in u for u in subidos)
