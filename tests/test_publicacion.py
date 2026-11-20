"""Fase 1 — Publicación en GitHub: helpers y comportamiento best-effort."""

from __future__ import annotations

import json

import publicacion


# ---------------------------------------------------------------------------
# configuracion
# ---------------------------------------------------------------------------

def test_configuracion_sin_token_es_none(monkeypatch):
    monkeypatch.delenv("GITHUB_PUBLISH_TOKEN", raising=False)
    assert publicacion.configuracion() is None


def test_configuracion_con_token_usa_defaults(monkeypatch):
    monkeypatch.setenv("GITHUB_PUBLISH_TOKEN", " tok ")
    monkeypatch.delenv("GITHUB_PUBLISH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_PUBLISH_BRANCH", raising=False)
    cfg = publicacion.configuracion()
    assert cfg == {
        "token": "tok",
        "repo": publicacion.DEFAULT_REPO,
        "branch": publicacion.DEFAULT_BRANCH,
    }


def test_configuracion_respeta_overrides(monkeypatch):
    monkeypatch.setenv("GITHUB_PUBLISH_TOKEN", "t")
    monkeypatch.setenv("GITHUB_PUBLISH_REPO", "o/r")
    monkeypatch.setenv("GITHUB_PUBLISH_BRANCH", "br")
    assert publicacion.configuracion() == {"token": "t", "repo": "o/r", "branch": "br"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def test_cabeceras_incluyen_bearer():
    cab = publicacion._cabeceras("tok")
    assert cab["Authorization"] == "Bearer tok"
    assert cab["Accept"] == "application/vnd.github+json"


def test_url_contenido():
    assert publicacion._url_contenido("a/b", "f.json") == \
        "https://api.github.com/repos/a/b/contents/f.json"


def test_marca_lee_generado_en(tmp_path):
    ruta = tmp_path / "o.json"
    ruta.write_text(json.dumps({"generado_en": "2026-11-16T00:00:00+00:00"}),
                    encoding="utf-8")
    assert publicacion._marca(ruta) == "2026-11-16T00:00:00+00:00"


def test_marca_con_json_invalido_devuelve_now(tmp_path):
    ruta = tmp_path / "o.json"
    ruta.write_text("no json", encoding="utf-8")
    assert publicacion._marca(ruta) == "now"
    assert publicacion._marca(tmp_path / "no-existe.json") == "now"


# ---------------------------------------------------------------------------
# publicar (best-effort, nunca lanza)
# ---------------------------------------------------------------------------

def test_publicar_sin_token_devuelve_false(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_PUBLISH_TOKEN", raising=False)
    ruta = tmp_path / "o.json"
    ruta.write_text("{}", encoding="utf-8")
    assert publicacion.publicar(ruta) is False


def test_publicar_con_token_pero_sin_fichero_devuelve_false(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_PUBLISH_TOKEN", "t")
    assert publicacion.publicar(tmp_path / "no-existe.json") is False
