# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Configuração do pytest com faulthandler ativo e limite de 600 s por teste."""
from __future__ import annotations

import faulthandler

try:
    import pytest
except ImportError:
    pytest = None  # type: ignore[assignment]

TIMEOUT_SEGUNDOS = 600.0


def _arm_faulthandler():
    try:
        faulthandler.dump_traceback_later(TIMEOUT_SEGUNDOS, repeat=False, exit=True)
    except Exception:
        pass


def _cancel_faulthandler():
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass


if pytest is not None:
    @pytest.hookimpl(tryfirst=True)
    def pytest_configure(config):
        if not faulthandler.is_enabled():
            faulthandler.enable()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(item, nextitem):
        _arm_faulthandler()
        try:
            yield
        finally:
            _cancel_faulthandler()
else:
    def pytest_configure(config):
        if not faulthandler.is_enabled():
            faulthandler.enable()

    def pytest_runtest_protocol(item, nextitem):
        _arm_faulthandler()
        try:
            yield
        finally:
            _cancel_faulthandler()
