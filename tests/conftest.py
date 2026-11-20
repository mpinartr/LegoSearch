"""Fixtures compartidas por toda la suite de tests.

Principios:
- Los tests NUNCA tocan la red ni dependen de la fecha del sistema.
- Los datos de ejemplo se construyen aquí, no se leen de ficheros generados
  (``opportunities.json``, ``watchlist.json``...) que pueden no existir en CI.

Fase 1 irá añadiendo fixtures de dominio (entries, candidatos, JSON histórico)
a medida que se escriban los tests de caracterización.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# La raíz del repo debe estar en sys.path para poder importar los módulos
# planos del proyecto (main.py, config.py, brickset.py...). ``pythonpath`` en
# pyproject.toml ya lo hace, pero lo dejamos explícito por robustez.
RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import main  # noqa: E402  (import tras ajustar sys.path)


# Watchlist mínima y estable para tests. No se usa la de config.py completa
# para que los tests no dependan de qué sets haya configurados en cada momento.
WATCHLIST_MINIMA: dict[str, dict] = {
    "75192": {
        "nombre": "Millennium Falcon UCS",
        "tematica": "Star Wars UCS",
        "precio_oficial": 849.99,
        "precio_objetivo_compra": 595.00,
        "precio_estimado_reventa": 1150.00,
        "estado_eol": "Próximo EoL",
        "piezas": 7541,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
        "alias": ["UCS Falcon"],
        "notas": "",
    },
    "10294": {
        "nombre": "Titanic",
        "tematica": "Icons",
        "precio_oficial": 679.99,
        "precio_objetivo_compra": 475.00,
        "precio_estimado_reventa": 850.00,
        "estado_eol": "Activo",
        "piezas": 9090,
        "minifiguras_exclusivas": False,
        "publico_adulto_afol": True,
        "alias": ["LEGO Titanic"],
        "notas": "",
    },
}


@pytest.fixture(autouse=True)
def _sin_credenciales_reales(monkeypatch):
    """Ningún test debe salir a APIs reales por variables del entorno del dev."""
    for var in (
        "BRICKLINK_CONSUMER_KEY",
        "BRICKLINK_CONSUMER_SECRET",
        "BRICKLINK_TOKEN",
        "BRICKLINK_TOKEN_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def watchlist() -> dict[str, dict]:
    """Watchlist validada y mínima, independiente de config.py y del disco."""
    return {
        ref: main._validar_set(ref, dict(datos))
        for ref, datos in WATCHLIST_MINIMA.items()
    }


@pytest.fixture
def entradas() -> list:
    """Entradas de ejemplo con el mismo formato que produce el rastreo real."""
    return main.entradas_simuladas()


@pytest.fixture
def make_entry():
    """Fábrica de ``Entry`` a partir de un texto libre.

    Normaliza el texto igual que el motor real, de modo que los tests ejerciten
    ``extraer_referencias`` y ``extraer_precios`` de verdad.
    """

    def _make(texto: str, *, titulo: str | None = None, tipo: str = "ofertas",
              feed: str = "Test", url: str = "https://example.com/anuncio",
              fecha=None, valor_eur: float = 1.0):
        texto_norm = main.normalizar(texto)
        return main.Entry(
            feed=feed,
            tipo=tipo,
            valor_eur=valor_eur,
            titulo=titulo if titulo is not None else texto,
            texto=texto_norm,
            url=url,
            fecha=fecha,
            referencias=main.extraer_referencias(texto_norm),
            precios=main.extraer_precios(texto_norm),
        )

    return _make


@pytest.fixture
def candidate(watchlist, make_entry):
    """``Candidate`` coherente con la watchlist mínima (set 75192)."""
    set_ = watchlist["75192"]
    entry = make_entry("solo 500 € lego 75192 falcon", tipo="ofertas")
    precio = main.PriceMatch(valor=500.0, texto_original="500 €", posicion=0,
                             etiqueta="oferta")
    financiero = main.calcular_financiero(500.0, set_["precio_estimado_reventa"])
    return main.Candidate(
        referencia="75192",
        set=set_,
        entry=entry,
        precio_detectado=precio,
        precio_compra=500.0,
        origen_precio="oferta_detectada",
        score_coleccionista=3,
        score_coleccionista_label="AFOL + 7,541 piezas + minifiguras exclusivas",
        confianza_cruce=100,
        financiero=financiero,
        score_total=90.0,
    )
