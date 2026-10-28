# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path
import json
import tempfile
import unittest

from traslado.model import Options
from traslado.runner import execute


class DocumentWarnings(unittest.TestCase):
    def test_global_reason_is_visible_and_counted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "sample.txt"
            source.write_text("PAYMENT_123", encoding="utf-8")
            result = execute(source, root / "out", Options(advanced_kit="unused-for-text"))
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["warnings"], 1)
            folder = Path(result["output"])
            document = json.loads((folder / "document.json").read_text(encoding="utf-8"))
            self.assertEqual(document["warnings"], ["advanced_pdf_only"])
            markdown = (folder / "document.md").read_text(encoding="utf-8")
            self.assertIn("[os avisos de conversão](LEIA-AVISOS.txt)", markdown)
            self.assertIn("Documento: advanced pdf only", (folder / "LEIA-AVISOS.txt").read_text(encoding="utf-8"))
            self.assertIn("PAYMENT_123", markdown)
