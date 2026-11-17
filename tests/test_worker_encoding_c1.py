# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from traslado.model import Options
from traslado.runner import execute

FIXTURE = Path(__file__).parent / 'fixtures' / 'worker' / 'diagnostico-nao-utf8.pdf'


@unittest.skipIf(importlib.util.find_spec('fitz') is None, 'Requer PyMuPDF (perfil Full)')
class WorkerEncodingC1(unittest.TestCase):
    """Mutante 128 da bateria E6: o MuPDF emite diagnóstico com bytes não UTF-8, que o PyMuPDF imprime em stdout
    de dentro da chamada C. Com stdout em cp1252 (Windows, saída redirecionada a arquivo), o erro de codificação
    virava SystemError e a conversão terminava como `internal`. Discrimina no Windows; em POSIX passa nos dois lados."""

    def test_diagnostico_do_mupdf_nao_vira_falha_interna(self):
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.dict(os.environ, {'PYTHONIOENCODING': 'cp1252', 'PYTHONUTF8': '0'}):
            result = execute(FIXTURE, Path(raw) / 'out', Options(mode='native', images=False, tables=False, chunks=False,
                                                                  job_timeout=30))
        self.assertNotEqual(result.get('error_code'), 'internal', result)


if __name__ == '__main__':
    unittest.main()
