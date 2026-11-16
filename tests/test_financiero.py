"""Fase 1 — Motor financiero y criterios de coleccionista/retirada."""

from __future__ import annotations

from freezegun import freeze_time

import main


# ---------------------------------------------------------------------------
# calcular_financiero
# ---------------------------------------------------------------------------

def test_financiero_caso_base():
    f = main.calcular_financiero(precio_compra=600.0, precio_venta=1150.0)
    # Gastos configurados: plataforma 10 %, envío 5 %.
    assert f.precio_compra == 600.0
    assert f.gastos_plataforma == 115.0
    assert f.gastos_envio == 57.5
    assert f.coste_total == 772.5
    assert f.ingresos_netos == 977.5
    assert f.beneficio_neto == 377.5
    assert f.roi_pct == 48.87


def test_financiero_costes_cero_evita_division_por_cero():
    f = main.calcular_financiero(precio_compra=0.0, precio_venta=0.0)
    assert f.coste_total == 0.0
    assert f.beneficio_neto == 0.0
    assert f.roi_pct == -100.0


def test_financiero_redondea_a_dos_decimales():
    f = main.calcular_financiero(precio_compra=10.005, precio_venta=20.0)
    for valor in (f.precio_compra, f.gastos_plataforma, f.gastos_envio,
                  f.coste_total, f.ingresos_netos, f.beneficio_neto, f.roi_pct):
        assert round(valor, 2) == valor


# ---------------------------------------------------------------------------
# evaluar_coleccionista
# ---------------------------------------------------------------------------

def test_coleccionista_tres_criterios():
    puntos, etiqueta = main.evaluar_coleccionista({
        "piezas": 7541,
        "minifiguras_exclusivas": True,
        "publico_adulto_afol": True,
    })
    assert puntos == 3
    assert "AFOL" in etiqueta
    assert "minifiguras exclusivas" in etiqueta


def test_coleccionista_sin_criterios():
    puntos, etiqueta = main.evaluar_coleccionista({"piezas": 10})
    assert puntos == 0
    assert etiqueta == "sin criterios de coleccionista"


def test_coleccionista_umbral_de_piezas_depende_de_config():
    import config as cfg

    alto = int(cfg.FILTROS["piezas_alto_volumen"])
    assert main.evaluar_coleccionista({"piezas": alto})[0] == 1
    assert main.evaluar_coleccionista({"piezas": alto - 1})[0] == 0


# ---------------------------------------------------------------------------
# evaluar_retirada
# ---------------------------------------------------------------------------

@freeze_time("2026-11-16")
def test_retirada_inminente_dos_puntos():
    assert main.evaluar_retirada({"exit_date": "2026-12-31"}) == (2, "se retira en 45 días")


@freeze_time("2026-11-16")
def test_retirada_menos_de_un_ano_un_punto():
    assert main.evaluar_retirada({"exit_date": "2027-06-01"}) == (1, "se retira en 197 días")


@freeze_time("2026-11-16")
def test_retirada_lejana_cero_puntos():
    assert main.evaluar_retirada({"exit_date": "2028-01-01"}) == (0, "se retira en 411 días")


@freeze_time("2026-11-16")
def test_retirada_ya_retirado():
    assert main.evaluar_retirada({"exit_date": "2025-01-01"}) == (0, "retirado hace 684 días")


def test_retirada_sin_fecha_usa_estado_cualitativo():
    assert main.evaluar_retirada({"estado_eol": "Próximo EoL"}) == (2, "próximo EoL")
    assert main.evaluar_retirada({"estado_eol": "Retirado"}) == (0, "retirado")
    assert main.evaluar_retirada({"estado_eol": "Activo"}) == (0, "")


@freeze_time("2026-11-16")
def test_retirada_fecha_invalida_cae_al_estado():
    assert main.evaluar_retirada({"exit_date": "no-fecha", "estado_eol": "Próximo EoL"}) \
        == (2, "próximo EoL")
