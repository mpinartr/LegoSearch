"""Fase 2 — Tests de los seams de E/S: reloj, red y transporte.

Cubren las rutas que antes no tenían test (descarga de feeds, curl, sesión
HTTP, cliente inyectado) sin tocar Internet ni depender del reloj real.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import feedparser
import pytest
import requests
import responses

import brickset
import calendario
import main


RSS = (
    b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
    b"<item><title>LEGO 75192 oferta 500 EUR</title>"
    b"<link>https://example.com/1</link>"
    b"<description>solo 500 EUR</description></item>"
    b"</channel></rss>"
)


class FakeResp:
    """Respuesta HTTP mínima para los dobles de ``requests.get``."""

    def __init__(self, payload=None, status=200, exc=None):
        self._payload = payload
        self.status_code = status
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# Reloj inyectable
# ---------------------------------------------------------------------------

def test_ahora_iso_inyectado():
    momento = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    assert main.ahora_iso(momento) == "2026-11-16T10:00:00+00:00"


def test_dias_desde_inyectado():
    fecha = datetime(2026, 11, 14, tzinfo=timezone.utc)
    ahora = datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert main.dias_desde(fecha, ahora=ahora) == pytest.approx(2.0)


def test_evaluar_retirada_con_hoy_inyectado():
    assert main.evaluar_retirada(
        {"exit_date": "2026-12-31"}, hoy=date(2026, 11, 16),
    ) == (2, "se retira en 45 días")


def test_serializar_candidate_con_ahora_inyectado(candidate):
    momento = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
    op = main.serializar_candidate(candidate, ahora=momento)
    assert op["detectada_en"] == "2026-11-16T10:00:00+00:00"


def test_entradas_simuladas_con_ahora_inyectado():
    momento = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
    entradas = main.entradas_simuladas(ahora=momento)
    # La primera entrada de ejemplo tiene "dias": 1.
    assert entradas[0].fecha == datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Sesión y transporte de feeds
# ---------------------------------------------------------------------------

def test_crear_sesion_lleva_user_agent():
    sesion = main.crear_sesion()
    assert "User-Agent" in sesion.headers


def test_host_con_pausa():
    assert main._host_con_pausa("www.reddit.com") is True
    assert main._host_con_pausa("example.com") is False


@responses.activate
def test_descargar_bytes_ok():
    responses.add(responses.GET, "https://example.com/feed", body=b"<rss/>")
    assert main._descargar_bytes(main.crear_sesion(), "https://example.com/feed") == b"<rss/>"


@responses.activate
def test_descargar_bytes_bloqueo_403_devuelve_none():
    responses.add(responses.GET, "https://example.com/feed", status=403)
    assert main._descargar_bytes(main.crear_sesion(), "https://example.com/feed") is None


def test_descargar_bytes_via_curl(monkeypatch):
    class Proc:
        returncode = 0
        stdout = b"<rss/>\n200"
        stderr = b""

    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: Proc())
    assert main._descargar_bytes(
        main.crear_sesion(), "https://www.reddit.com/r/x/.rss",
    ) == b"<rss/>"


def test_descargar_bytes_via_curl_bloqueo(monkeypatch):
    class Proc:
        returncode = 0
        stdout = b"\n429"
        stderr = b""

    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: Proc())
    assert main._descargar_bytes(
        main.crear_sesion(), "https://www.reddit.com/r/x/.rss",
    ) is None


def test_descargar_bytes_via_curl_fallo_de_proceso(monkeypatch):
    class Proc:
        returncode = 1
        stdout = b""
        stderr = b"curl: fallo"

    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: Proc())
    with pytest.raises(requests.ConnectionError):
        main._descargar_bytes(main.crear_sesion(), "https://www.reddit.com/r/x/.rss")


@responses.activate
def test_descargar_feed_ok():
    responses.add(
        responses.GET, "https://example.com/feed",
        body=RSS, content_type="application/rss+xml",
    )
    feed = main.descargar_feed(main.crear_sesion(), "https://example.com/feed")
    assert feed is not None
    assert len(feed.entries) == 1


@responses.activate
def test_descargar_feed_mal_formado_devuelve_none():
    responses.add(responses.GET, "https://example.com/feed", body=b"no xml")
    assert main.descargar_feed(main.crear_sesion(), "https://example.com/feed") is None


@responses.activate
def test_descargar_feed_reintenta_y_deshace(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda _s: None)
    responses.add(responses.GET, "https://example.com/feed", status=500)
    responses.add(responses.GET, "https://example.com/feed", status=500)
    assert main.descargar_feed(main.crear_sesion(), "https://example.com/feed") is None
    assert len(responses.calls) == 2  # 1 intento + 1 reintento


# ---------------------------------------------------------------------------
# construir_entradas y rastrear_feeds
# ---------------------------------------------------------------------------

def test_construir_entradas_normaliza_feed():
    feed = feedparser.parse(RSS)
    entradas = main.construir_entradas(
        {"nombre": "Test", "tipo": "ofertas", "valor_eur": 1.0}, feed, 10,
    )
    assert len(entradas) == 1
    entry = entradas[0]
    assert entry.feed == "Test"
    assert entry.tipo == "ofertas"
    assert "75192" in entry.referencias
    assert any(p.valor == 500.0 for p in entry.precios)
    assert entry.fecha is None  # el RSS de prueba no trae fecha


def _feeds_de_prueba():
    return [
        {"nombre": "A", "url": "https://a.example/feed", "tipo": "ofertas", "valor_eur": 1.0},
        {"nombre": "B", "url": "https://b.example/feed", "tipo": "noticias", "valor_eur": 0.5},
    ]


def test_rastrear_feeds_con_descargador_inyectado(monkeypatch):
    monkeypatch.setattr(main.cfg, "FEEDS", _feeds_de_prueba())
    monkeypatch.setattr(main.time, "sleep", lambda _s: None)

    def fake_descarga(url):
        if "a.example" in url:
            return feedparser.parse(RSS)
        return None  # fuente caída

    entradas, estado = main.rastrear_feeds(descargar_feed_fn=fake_descarga)

    assert len(entradas) == 1
    assert estado[0] == {"nombre": "A", "ok": True, "entradas": 1}
    assert estado[1] == {"nombre": "B", "ok": False, "entradas": 0}


def test_rastrear_feeds_filtra_por_seleccion(monkeypatch):
    monkeypatch.setattr(main.cfg, "FEEDS", _feeds_de_prueba())
    entradas, estado = main.rastrear_feeds(
        seleccion=["a.example"], descargar_feed_fn=lambda url: feedparser.parse(RSS),
    )
    assert [e["nombre"] for e in estado] == ["A"]
    assert len(entradas) == 1


# ---------------------------------------------------------------------------
# Seams de Brickset y calendario
# ---------------------------------------------------------------------------

SET_API = {
    "name": "Falcon", "year": 2017, "theme": "Star Wars", "pieces": 7541,
    "minifigs": 8, "LEGOCom": {"DE": {"retailPrice": "849.99"}},
    "exitDate": "2026-12-31T00:00:00Z", "launchDate": "2017-09-01T00:00:00Z",
    "rating": 4.8,
}


def test_brickset_get_inyectado():
    llamadas = []

    def fake_get(url, params=None, timeout=None, headers=None):
        llamadas.append(params)
        return FakeResp({"status": "success", "sets": [SET_API]})

    cliente = brickset.BricksetClient("k", get=fake_get)
    datos = cliente.datos("75192")

    assert datos["pvp_eur"] == 849.99
    assert len(llamadas) == 1


def test_brickset_enriquecer_con_hoy_inyectado():
    def fake_get(url, params=None, timeout=None, headers=None):
        return FakeResp({"status": "success", "sets": [SET_API]})

    watchlist = {"75192": {"precio_oficial": 90.0, "estado_eol": "Activo"}}
    brickset.BricksetClient("k", get=fake_get).enriquecer_watchlist(
        watchlist, hoy="2026-11-16",
    )
    assert watchlist["75192"]["precio_oficial"] == 849.99
    assert watchlist["75192"]["estado_eol"] == "Próximo EoL"


def test_calendario_consultar_con_get_inyectado():
    def fake_get(url, params=None, timeout=None, headers=None):
        return FakeResp({"status": "success", "sets": [{"number": "1"}]})

    assert calendario._consultar("k", "Star Wars", "2024", 5, get=fake_get) == [
        {"number": "1"}
    ]


def test_calendario_construir_con_get_inyectado():
    from freezegun import freeze_time

    def fake_get(url, params=None, timeout=None, headers=None):
        return FakeResp({"status": "success", "sets": [SET_API | {"number": "75192"}]})

    with freeze_time("2026-11-16"):
        sets = calendario.construir_calendario(
            "k", temas=["Star Wars"], años=["2024"], get=fake_get,
        )

    assert len(sets) == 1
    assert sets[0]["referencia"] == "75192"
    assert sets[0]["dias_para_retirada"] == 45
