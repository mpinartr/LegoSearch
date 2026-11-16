"""Test de humo de la Fase 0: valida que la infraestructura de tests funciona.

No pretende cubrir lógica de negocio (eso llega en la Fase 1). Solo comprueba
que el proyecto se importa, que las fixtures existen y devuelven datos con la
forma esperada, y que el runner está bien configurado.
"""

from __future__ import annotations

import main


def test_el_proyecto_se_importa_y_tiene_version():
    assert main.APP_VERSION
    assert main.SCHEMA_VERSION


def test_fixture_watchlist_devuelve_sets_validados(watchlist):
    assert set(watchlist) == {"75192", "10294"}
    assert watchlist["75192"]["precio_oficial"] == 849.99
    assert watchlist["75192"]["publico_adulto_afol"] is True


def test_fixture_entradas_devuelve_entradas_normalizadas(entradas):
    assert entradas, "entradas_simuladas() no debe devolver una lista vacía"
    for entry in entradas:
        assert isinstance(entry.texto, str) and entry.texto
        assert isinstance(entry.titulo, str) and entry.titulo
        assert isinstance(entry.referencias, list)
        assert isinstance(entry.precios, list)


def test_utilidad_de_normalizacion_esta_disponible():
    # Comprobación mínima de que la lógica pura es importable y ejecutable.
    assert main.normalizar("<p>Millennium Fálcon</p>") == "millennium falcon"
