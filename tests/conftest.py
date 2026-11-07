# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""O plugin nativo do pytest controla o único temporizador por teste."""


def pytest_configure(config):
    # O CI fixa pytest 9.1.1, que oferece encerramento no timeout.
    # As opções -o do comando prevalecem sobre estes valores padrão.
    config.inicfg.setdefault('faulthandler_timeout', '600')
    config.inicfg.setdefault('faulthandler_exit_on_timeout', 'true')
