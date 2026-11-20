"""Fase 1 — Tests de caracterización de la extracción/parsing de texto.

Documentan el comportamiento ACTUAL de las funciones puras de ``main.py``. Si
un refactor cambia alguno de estos resultados, el test falla y nos avisa.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import main


# ---------------------------------------------------------------------------
# normalizar
# ---------------------------------------------------------------------------

def test_normalizar_quita_html_acentos_y_mayusculas():
    assert main.normalizar("<p>Millennium Fálcon</p>") == "millennium falcon"


def test_normalizar_colapsa_espacios_y_entidades():
    assert main.normalizar("HOLA   mundo") == "hola mundo"
    assert main.normalizar("a &amp; b") == "a & b"


def test_normalizar_texto_vacio():
    assert main.normalizar("") == ""


# ---------------------------------------------------------------------------
# _parse_numero_eur
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("crudo,esperado", [
    ("599,99", 599.99),
    ("79.9", 79.9),
    ("1.499", 1499.0),          # punto de miles (un set no cuesta 1,499 €)
    ("1,299.99", 1299.99),      # formato anglosajón
    ("1.499,50", 1499.5),       # formato europeo con miles
])
def test_parse_numero_eur_formatos_validos(crudo, esperado):
    assert main._parse_numero_eur(crudo) == esperado


@pytest.mark.parametrize("crudo", ["400€", "0", "abc", "", "   "])
def test_parse_numero_eur_invalidos_o_no_positivos(crudo):
    assert main._parse_numero_eur(crudo) is None


# ---------------------------------------------------------------------------
# extraer_precios
# ---------------------------------------------------------------------------

def test_extraer_precios_marca_oferta_por_contexto():
    precios = main.extraer_precios("solo 449,99 €")
    assert len(precios) == 1
    assert precios[0].valor == 449.99
    assert precios[0].etiqueta == "oferta"
    assert precios[0].texto_original == "449,99 €"


def test_extraer_precios_marca_pvp():
    precios = main.extraer_precios("PVP 599,99 €")
    assert len(precios) == 1
    assert precios[0].valor == 599.99
    assert precios[0].etiqueta == "pvp"


def test_extraer_precios_etiqueta_general_sin_contexto():
    precios = main.extraer_precios("precio 84.99€")
    assert len(precios) == 1
    assert precios[0].etiqueta == "general"


def test_extraer_precios_acepta_formato_eur_y_euros():
    assert main.extraer_precios("EUR 1.499")[0].valor == 1499.0
    assert main.extraer_precios("599 euros")[0].valor == 599.0
    assert main.extraer_precios("€599.99")[0].valor == 599.99


def test_extraer_precios_multiples_conserva_orden():
    precios = main.extraer_precios("antes 599,99 € ahora 449,99 €")
    assert [p.valor for p in precios] == [599.99, 449.99]
    # Caracterización: ambos caen como "pvp" porque la palabra "antes" está en
    # la ventana de contexto. Se fija para detectar cambios futuros.
    assert [p.etiqueta for p in precios] == ["pvp", "pvp"]


def test_extraer_precios_sin_precio():
    assert main.extraer_precios("sin precio") == []
    assert main.extraer_precios("") == []


# ---------------------------------------------------------------------------
# extraer_descuento_pct
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("texto,esperado", [
    ("30% off", 30.0),
    ("-35%", 35.0),
    ("30 % descuento", 30.0),
    ("25% de rebaja", 25.0),
])
def test_extraer_descuento_pct_validos(texto, esperado):
    assert main.extraer_descuento_pct(texto) == esperado


@pytest.mark.parametrize("texto", [
    "100% compatible",   # > 90 %, suele ser una condición
    "3% off",            # < 5 %, no es una oferta interesante
    "20% de algo",       # sin palabra de rebaja
    "30%",               # porcentaje suelto sin cola
    "",
])
def test_extraer_descuento_pct_descartados(texto):
    assert main.extraer_descuento_pct(texto) is None


def test_extraer_descuento_pct_devuelve_el_mayor():
    assert main.extraer_descuento_pct("20% off y 45% de descuento") == 45.0


# ---------------------------------------------------------------------------
# extraer_referencias
# ---------------------------------------------------------------------------

def test_extraer_referencias_basico():
    assert main.extraer_referencias("lego 75192 falcon") == ["75192"]


def test_extraer_referencias_ignora_grupos_de_miles():
    # "599,99" no debe confundirse con una referencia.
    assert main.extraer_referencias("599,99 75192") == ["75192"]


def test_extraer_referencias_descarta_anios_y_cantidades_cortas():
    assert main.extraer_referencias("año 1999 y 2023") == ["2023"]
    assert main.extraer_referencias("123") == []


def test_extraer_referencias_no_duplica_y_respeta_limite():
    assert main.extraer_referencias("75192 75192") == ["75192"]
    texto = " ".join(str(10000 + i) for i in range(10))
    assert len(main.extraer_referencias(texto, limite=7)) == 7


# ---------------------------------------------------------------------------
# extraer_tienda
# ---------------------------------------------------------------------------

def test_extraer_tienda_reconoce_tiendas_conocidas():
    assert main.extraer_tienda("en amazon.es") == "Amazon.Es"
    assert main.extraer_tienda("El Corte Inglés") == "El Corte Inglés"
    assert main.extraer_tienda("Target MN") == "Target"


def test_extraer_tienda_desconocida_o_vacia():
    assert main.extraer_tienda("tienda rara") is None
    assert main.extraer_tienda("") is None


# ---------------------------------------------------------------------------
# parse_fecha
# ---------------------------------------------------------------------------

def test_parse_fecha_desde_cadena_rfc():
    fecha = main.parse_fecha({"published": "Mon, 10 Nov 2026 12:00:00 GMT"})
    assert fecha == datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def test_parse_fecha_asume_utc_si_es_ingenua():
    fecha = main.parse_fecha({"updated": "Mon, 10 Nov 2026 12:00:00"})
    assert fecha is not None
    assert fecha.tzinfo == timezone.utc


def test_parse_fecha_sin_datos_o_invalida():
    assert main.parse_fecha({}) is None
    assert main.parse_fecha({"published": "no es una fecha"}) is None


# ---------------------------------------------------------------------------
# dias_desde
# ---------------------------------------------------------------------------

def test_dias_desde_none_es_infinito():
    assert main.dias_desde(None) == float("inf")


def test_dias_desde_fecha_pasada():
    from freezegun import freeze_time

    with freeze_time("2026-11-16"):
        fecha = datetime(2026, 11, 14, tzinfo=timezone.utc)
        assert main.dias_desde(fecha) == pytest.approx(2.0)
