"""Fase 5 — Worker continuo (``modo_loop``) y arranque (``main``)."""

from __future__ import annotations

import logging

import pytest
import requests

import main


class FakeEvent:
    """Sustituto de ``threading.Event`` que no espera de verdad."""

    def __init__(self):
        self._set = False

    def set(self):
        self._set = True

    def is_set(self):
        return self._set

    def wait(self, timeout=None):
        self._set = True
        return True


def _args():
    return main.parse_args(["--simulate", "--interval", "1"])


def _sin_senales(monkeypatch):
    monkeypatch.setattr(main.signal, "signal", lambda sig, fn: None)


# ---------------------------------------------------------------------------
# modo_loop
# ---------------------------------------------------------------------------

def test_modo_loop_sale_por_senal(monkeypatch):
    handlers = {}
    monkeypatch.setattr(main.signal, "signal",
                        lambda sig, fn: handlers.__setitem__(sig, fn))
    monkeypatch.setattr(main.threading, "Event", FakeEvent)
    monkeypatch.setattr(main.time, "monotonic", lambda: 0.0)

    def fake_ejecutar(args):
        # Simula SIGTERM durante el ciclo: el worker debe salir con 0.
        handlers[main.signal.SIGTERM](main.signal.SIGTERM, None)
        return 0

    monkeypatch.setattr(main, "ejecutar", fake_ejecutar)
    assert main.modo_loop(_args()) == 0


def test_modo_loop_tolera_fallo_de_red(monkeypatch, caplog):
    _sin_senales(monkeypatch)
    monkeypatch.setattr(main.threading, "Event", FakeEvent)
    monkeypatch.setattr(main.time, "monotonic", lambda: 0.0)

    def fake_ejecutar(args):
        raise requests.ConnectionError("sin red")

    monkeypatch.setattr(main, "ejecutar", fake_ejecutar)
    with caplog.at_level(logging.WARNING):
        assert main.modo_loop(_args()) == 0
    assert any("red" in r.getMessage() for r in caplog.records)


def test_modo_loop_tolera_error_inesperado(monkeypatch, caplog):
    _sin_senales(monkeypatch)
    monkeypatch.setattr(main.threading, "Event", FakeEvent)
    monkeypatch.setattr(main.time, "monotonic", lambda: 0.0)

    def fake_ejecutar(args):
        raise ValueError("bug")

    monkeypatch.setattr(main, "ejecutar", fake_ejecutar)
    with caplog.at_level(logging.ERROR):
        assert main.modo_loop(_args()) == 0
    assert any("falló" in r.getMessage() for r in caplog.records)


def test_modo_loop_avisa_si_el_ciclo_devuelve_error(monkeypatch, caplog):
    _sin_senales(monkeypatch)
    monkeypatch.setattr(main.threading, "Event", FakeEvent)
    monkeypatch.setattr(main.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(main, "ejecutar", lambda args: 2)

    with caplog.at_level(logging.WARNING):
        assert main.modo_loop(_args()) == 0
    assert any("código 2" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# main(): selección de modo y cortafuegos
# ---------------------------------------------------------------------------

def test_main_usa_modo_loop_con_variable_de_entorno(monkeypatch):
    monkeypatch.setenv("LOOP_MODE", "1")
    monkeypatch.setattr(main, "modo_loop", lambda args: 0)
    assert main.main(["--simulate"]) == 0


def test_main_usa_modo_loop_con_flag(monkeypatch):
    monkeypatch.setattr(main, "modo_loop", lambda args: 0)
    assert main.main(["--loop", "--simulate"]) == 0


def test_main_once_tiene_prioridad_sobre_loop(monkeypatch):
    monkeypatch.setenv("LOOP_MODE", "1")
    monkeypatch.setattr(main, "modo_loop", lambda args: 99)
    monkeypatch.setattr(main, "ejecutar", lambda args: 0)
    assert main.main(["--once", "--simulate"]) == 0


def test_main_keyboard_interrupt_devuelve_130(monkeypatch):
    monkeypatch.setattr(
        main, "ejecutar",
        lambda args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert main.main(["--simulate"]) == 130


def test_main_error_inesperado_devuelve_1(monkeypatch):
    monkeypatch.setattr(
        main, "ejecutar",
        lambda args: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert main.main(["--simulate"]) == 1
