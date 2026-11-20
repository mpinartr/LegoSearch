"""Fase A — BrickLink: firma OAuth, normalización y cliente con HTTP simulado."""

from __future__ import annotations

import pytest
import requests

import mercado


class FakeResp:
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
# OAuth 1.0a
# ---------------------------------------------------------------------------

def test_escape_oauth_rfc3986():
    assert mercado._escape("a b") == "a%20b"
    assert mercado._escape("a-._~") == "a-._~"
    assert mercado._escape("á/&=") == "%C3%A1%2F%26%3D"


def test_firma_oauth_vector_canonico_rfc5849():
    """Vector de prueba público de OAuth 1.0a (Twitter/RFC 5849)."""
    oauth = mercado._firma_oauth(
        "GET", "http://photos.example.net/photos",
        {"file": "vacation.jpg", "size": "original"},
        consumer_key="dpf43f3p2l4k3l03",
        consumer_secret="kd94hf93k423kf44",
        token="nnch734d00sl2jdk",
        token_secret="pfkkdhi9sl3r4s00",
        timestamp="1191242096",
        nonce="kllo9940pd9333jh",
    )
    assert oauth["oauth_signature"] == "tR3+Ty81lMeYAr/Fid0kMTYa/WM="


def test_cabecera_oauth_tiene_formato_esperado():
    oauth = {"oauth_consumer_key": "k", "oauth_signature": "a+b/c="}
    cabecera = mercado._cabecera_oauth(oauth)
    assert cabecera.startswith("OAuth ")
    assert 'oauth_consumer_key="k"' in cabecera
    assert 'oauth_signature="a%2Bb%2Fc%3D"' in cabecera


# ---------------------------------------------------------------------------
# Normalización
# ---------------------------------------------------------------------------

def test_normalizar_precio_extrae_campos():
    datos = {
        "avg_price": "1120.00", "min_price": "900", "max_price": "1300",
        "qty_avg_price": "1110.5", "total_quantity": "18", "unit_quantity": "4",
    }
    normalizado = mercado._normalizar_precio(datos, "75192", "sold")
    assert normalizado["precio_medio"] == 1120.0
    assert normalizado["precio_min"] == 900.0
    assert normalizado["precio_max"] == 1300.0
    assert normalizado["unidades"] == 18
    assert normalizado["lotes"] == 4
    assert normalizado["guia"] == "sold"


def test_normalizar_precio_acepta_lista_y_descarta_vacio():
    assert mercado._normalizar_precio([{"avg_price": "10"}], "1", "sold")["precio_medio"] == 10.0
    assert mercado._normalizar_precio(None, "1", "sold") is None
    assert mercado._normalizar_precio({}, "1", "sold") is None


def test_numero_bricklink_anade_sufijo():
    assert mercado._numero_bricklink("75192") == "75192-1"
    assert mercado._numero_bricklink("75192-1") == "75192-1"


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------

def test_cliente_sin_credenciales_es_inactivo():
    cliente = mercado.BrickLinkClient(None, None, None, None)
    assert cliente.activo is False
    assert cliente.precio_venta("75192") is None
    assert cliente.precio_compra("75192") is None


def test_cliente_incompleto_es_inactivo():
    assert mercado.BrickLinkClient("k", "s", "t", None).activo is False
    assert mercado.BrickLinkClient("k", "s", "t", "").activo is False


def _cliente(fake_get):
    return mercado.BrickLinkClient("ck", "cs", "tk", "ts", get=fake_get)


def test_precio_venta_consulta_y_normaliza():
    llamadas = []

    def fake_get(url, params=None, headers=None, timeout=None):
        llamadas.append({"url": url, "params": params, "headers": headers})
        return FakeResp({
            "meta": {"code": 200, "message": "OK"},
            "data": {"avg_price": "1120.00", "min_price": "900",
                     "max_price": "1300", "total_quantity": "18"},
        })

    datos = _cliente(fake_get).precio_venta("75192")

    assert datos["precio_medio"] == 1120.0
    assert datos["unidades"] == 18
    assert llamadas[0]["params"] == {"guide_type": "sold", "new_or_used": "N"}
    assert "75192-1" in llamadas[0]["url"]
    assert llamadas[0]["headers"]["Authorization"].startswith("OAuth ")


def test_precio_compra_usa_guide_stock():
    llamadas = []

    def fake_get(url, params=None, headers=None, timeout=None):
        llamadas.append(params)
        return FakeResp({"meta": {"code": 200},
                         "data": {"min_price": "850", "avg_price": "860"}})

    datos = _cliente(fake_get).precio_compra("75192")
    assert datos["precio_min"] == 850.0
    assert llamadas[0]["guide_type"] == "stock"


def test_precio_compra_usado():
    llamadas = []

    def fake_get(url, params=None, headers=None, timeout=None):
        llamadas.append(params)
        return FakeResp({"meta": {"code": 200}, "data": {"min_price": "500"}})

    _cliente(fake_get).precio_compra("75192", nuevo=False)
    assert llamadas[0]["new_or_used"] == "U"


def test_cache_evita_segunda_peticion():
    llamadas = []

    def fake_get(url, params=None, headers=None, timeout=None):
        llamadas.append(url)
        return FakeResp({"meta": {"code": 200}, "data": {"avg_price": "10"}})

    cliente = _cliente(fake_get)
    cliente.precio_venta("75192")
    cliente.precio_venta("75192")
    assert len(llamadas) == 1


def test_error_http_devuelve_none():
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResp(status=500)

    assert _cliente(fake_get).precio_venta("75192") is None


def test_excepcion_de_red_devuelve_none():
    def fake_get(url, params=None, headers=None, timeout=None):
        raise requests.ConnectionError("sin red")

    assert _cliente(fake_get).precio_venta("75192") is None


def test_meta_con_codigo_de_error_devuelve_none():
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResp({"meta": {"code": 400, "message": "bad request"},
                         "data": {}})

    assert _cliente(fake_get).precio_venta("75192") is None


def test_desde_entorno_lee_variables(monkeypatch):
    monkeypatch.setenv("BRICKLINK_CONSUMER_KEY", "ck")
    monkeypatch.setenv("BRICKLINK_CONSUMER_SECRET", "cs")
    monkeypatch.setenv("BRICKLINK_TOKEN", "tk")
    monkeypatch.setenv("BRICKLINK_TOKEN_SECRET", "ts")
    cliente = mercado.BrickLinkClient.desde_entorno()
    assert cliente.activo is True


def test_desde_entorno_sin_variables_es_inactivo(monkeypatch):
    for var in ("BRICKLINK_CONSUMER_KEY", "BRICKLINK_CONSUMER_SECRET",
                "BRICKLINK_TOKEN", "BRICKLINK_TOKEN_SECRET"):
        monkeypatch.delenv(var, raising=False)
    assert mercado.BrickLinkClient.desde_entorno().activo is False


# ---------------------------------------------------------------------------
# Fase B — enriquecimiento de la watchlist
# ---------------------------------------------------------------------------

def _watchlist():
    return {
        "75192": {"precio_estimado_reventa": 1000.0},
        "10294": {"precio_estimado_reventa": 850.0},
    }


class FakeClient:
    def __init__(self, mapa, activo=True, compras=None):
        self.activo = activo
        self.mapa = mapa
        self.compras = compras or {}

    def precio_venta(self, ref, *, nuevo=True):
        return self.mapa.get(ref)

    def precio_compra(self, ref, *, nuevo=True):
        return self.compras.get(ref)


def test_enriquecer_watchlist_sustituye_precio_real():
    watchlist = _watchlist()
    cuenta = mercado.enriquecer_watchlist(
        watchlist, FakeClient({"75192": {"precio_medio": 1120.0}}),
    )
    assert watchlist["75192"]["precio_estimado_reventa"] == 1120.0
    assert watchlist["75192"]["reventa_verificada"] is True
    assert watchlist["75192"]["bricklink"]["precio_medio"] == 1120.0
    assert "reventa_verificada" not in watchlist["10294"]
    assert cuenta == {"consultados": 2, "con_reventa": 1,
                      "con_compra": 0, "sin_datos": 1}


def test_enriquecer_watchlist_guarda_precio_de_compra():
    watchlist = _watchlist()
    cliente = FakeClient(
        {"75192": {"precio_medio": 1120.0}},
        compras={"75192": {"precio_min": 800.0}},
    )
    cuenta = mercado.enriquecer_watchlist(watchlist, cliente)
    assert watchlist["75192"]["bricklink_compra"]["precio_min"] == 800.0
    assert cuenta["con_compra"] == 1


def test_enriquecer_watchlist_puede_omitir_compra():
    watchlist = _watchlist()
    cliente = FakeClient(
        {"75192": {"precio_medio": 1120.0}},
        compras={"75192": {"precio_min": 800.0}},
    )
    mercado.enriquecer_watchlist(watchlist, cliente, incluir_compra=False)
    assert "bricklink_compra" not in watchlist["75192"]


def test_enriquecer_watchlist_inactivo_no_toca_nada():
    watchlist = _watchlist()
    cuenta = mercado.enriquecer_watchlist(watchlist, FakeClient({}, activo=False))
    assert cuenta == {"consultados": 0, "con_reventa": 0,
                      "con_compra": 0, "sin_datos": 0}
    assert watchlist["75192"]["precio_estimado_reventa"] == 1000.0


def test_enriquecer_watchlist_sin_precio_medio_no_toca_el_set():
    watchlist = _watchlist()
    cliente = FakeClient({"75192": {"precio_medio": None}})
    cuenta = mercado.enriquecer_watchlist(watchlist, cliente)
    assert "reventa_verificada" not in watchlist["75192"]
    assert cuenta["sin_datos"] == 2


def test_enriquecido_marca_reventa_verificada_en_plan():
    import main

    watchlist = _watchlist()
    mercado.enriquecer_watchlist(
        watchlist, FakeClient({"75192": {"precio_medio": 1120.0}}),
    )
    watchlist["75192"]["estado_eol"] = "Activo"
    plan = main.estimar_reventa(watchlist["75192"])
    assert plan["valor_verificado"] is True


# ---------------------------------------------------------------------------
# Fase B — cableado en main._enriquecer_con_mercado
# ---------------------------------------------------------------------------

def test_enriquecer_con_mercado_actualiza_watchlist(monkeypatch, watchlist):
    import main

    class ClienteFalso:
        activo = True

        def precio_venta(self, ref, *, nuevo=True):
            return {"precio_medio": 1120.0}

        def precio_compra(self, ref, *, nuevo=True):
            return {"precio_min": 800.0}

    monkeypatch.setattr(
        mercado.BrickLinkClient, "desde_entorno",
        classmethod(lambda cls: ClienteFalso()),
    )
    main._enriquecer_con_mercado(watchlist, main.parse_args([]))
    assert watchlist["75192"]["precio_estimado_reventa"] == 1120.0
    assert watchlist["75192"]["reventa_verificada"] is True


def test_enriquecer_con_mercado_inactivo_no_modifica(monkeypatch, watchlist):
    import main

    class Inactivo:
        activo = False

    monkeypatch.setattr(
        mercado.BrickLinkClient, "desde_entorno",
        classmethod(lambda cls: Inactivo()),
    )
    main._enriquecer_con_mercado(watchlist, main.parse_args([]))
    assert watchlist["75192"]["precio_estimado_reventa"] == 1150.00


def test_enriquecer_con_mercado_se_omite_en_simulate(monkeypatch, watchlist):
    import main

    def boom(cls):
        raise AssertionError("no debe consultarse BrickLink en --simulate")

    monkeypatch.setattr(mercado.BrickLinkClient, "desde_entorno", classmethod(boom))
    main._enriquecer_con_mercado(watchlist, main.parse_args(["--simulate"]))


def test_enriquecer_con_mercado_se_omite_con_flag(monkeypatch, watchlist):
    import main

    def boom(cls):
        raise AssertionError("--sin-bricklink no debe consultar la API")

    monkeypatch.setattr(mercado.BrickLinkClient, "desde_entorno", classmethod(boom))
    main._enriquecer_con_mercado(watchlist, main.parse_args(["--sin-bricklink"]))
