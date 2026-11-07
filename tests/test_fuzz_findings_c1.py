# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from traslado.errors import CODES, classify, partial_error
from traslado.formats import extract
from traslado.model import Document, Options
from traslado.runner import execute
from scripts.fuzz_atheris import run_single_input


FIXTURES = Path(__file__).parent / 'fixtures' / 'fuzz'
EXPECTED = {
    'finding-docx-0d70b5901ed1b479.docx': 'unsupported',
    'finding-pptx-b5d00d8b70d17015.pptx': 'unsupported',
    'finding-xlsx-af733d34dfd38931.xlsx': 'unsupported',
    'finding-pdf-f3b5d54b09d31a3e.pdf': 'malformed',
    'finding-png-7bdc039f981feb7b.png': 'malformed',
}


def finding_test(path, route):
    def test(self):
        if path.suffix == '.pdf' and importlib.util.find_spec('fitz') is None:
            self.skipTest('Achado PDF requer PyMuPDF (perfil Full)')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            if route == 'execute':
                result = execute(path, root / 'out', Options(mode='native'))
                self.assertEqual(result['status'], 'failed', result)
                code = result.get('error_code')
            elif route == 'extract':
                out = root / 'out'
                out.mkdir()
                document = Document(path.name, hashlib.sha256(path.read_bytes()).hexdigest())
                try:
                    extract(path, out, Options(mode='native', images=False, tables=False, chunks=False), document)
                    issue = partial_error(document)
                except Exception as exc:
                    issue = classify(exc)
                code = issue.code if issue else None
            else:
                run_single_input(path.suffix[1:], path.read_bytes(), root, root / 'artifacts')
                self.assertFalse((root / 'artifacts').exists())
                return
            self.assertIn(code, CODES - {'internal'})
            if path.name in EXPECTED:
                self.assertEqual(code, EXPECTED[path.name])
    return test


class FuzzFindingsC1(unittest.TestCase):
    def test_corpus_present(self):
        self.assertTrue(list(FIXTURES.iterdir()), 'Corpus de fuzz ausente')


# Discover each new payload automatically, including its independent in-process path.
for fixture in sorted(FIXTURES.iterdir()):
    if fixture.is_file():
        for route in ('execute', 'extract', 'harness'):
            setattr(FuzzFindingsC1, f'test_{route}_{fixture.stem.replace("-", "_")}', finding_test(fixture, route))
