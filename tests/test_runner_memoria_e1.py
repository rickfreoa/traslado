# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""E1 da 3.0.0: tabela-verdade da heurística de falta de memória do worker (`runner._memoria_esgotada`).

Fixa o comportamento preexistente (C1, achado menor do Cursor), inclusive os limites conhecidos documentados
na função; endurecer a regra mudaria a classificação de erro e fica como questão aberta da 3.0."""
from __future__ import annotations

import math
import unittest

from traslado.runner import _memoria_esgotada

GIB = 1 << 30
LINUX_ALTO = {"rlimit_applied": True, "peak_bytes": int(.95 * GIB), "limit_bytes": GIB, "detected_after_s": None}
LINUX_BAIXO = {**LINUX_ALTO, "peak_bytes": int(.5 * GIB)}
WINDOWS = {"rlimit_applied": False, "peak_bytes": GIB, "limit_bytes": GIB, "detected_after_s": None}


class MemoriaEsgotada(unittest.TestCase):
    def test_codigos_de_cota_do_windows(self):
        for code in (0xC0000017, 0xC000009A, 0xC000012D, -1073741801):  # o último é 0xC0000017 com sinal
            self.assertTrue(_memoria_esgotada(code, WINDOWS, None), hex(code & 0xffffffff))
        self.assertFalse(_memoria_esgotada(0xC0000005, WINDOWS, None))  # violação de acesso não é memória

    def test_texto_do_log_so_quando_lido(self):
        for texto in (b"traceback ... memoryerror", b"fatal: out of memory", b"std::bad_alloc", b"cannot allocate memory"):
            self.assertTrue(_memoria_esgotada(1, {}, texto))
        self.assertFalse(_memoria_esgotada(1, {}, b"valueerror"))
        self.assertFalse(_memoria_esgotada(1, {}, None))

    def test_sinais_com_pico_sob_rlimit(self):
        for code in (-9, -11, -6):
            self.assertTrue(_memoria_esgotada(code, LINUX_ALTO, None))
            self.assertFalse(_memoria_esgotada(code, LINUX_BAIXO, None))
            self.assertFalse(_memoria_esgotada(code, {**LINUX_ALTO, "rlimit_applied": False}, None))
            self.assertTrue(_memoria_esgotada(code, {**LINUX_BAIXO, "detected_after_s": 1.0}, None))
        self.assertTrue(_memoria_esgotada(-9, {**LINUX_ALTO, "peak_bytes": math.ceil(.9 * GIB)}, None))  # limiar inclusivo
        self.assertFalse(_memoria_esgotada(-15, LINUX_ALTO, None))  # SIGTERM fica fora
        self.assertFalse(_memoria_esgotada(-9, {}, None))  # sem medição: não infere

    def test_limite_conhecido_queda_nativa_com_rss_alto(self):
        # SIGSEGV por outra causa com RSS alto sob RLIMIT_AS é classificado como memória (limite (i) documentado).
        self.assertTrue(_memoria_esgotada(-11, LINUX_ALTO, b"segmentation fault in native code"))


if __name__ == "__main__":
    unittest.main()
