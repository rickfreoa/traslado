# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import zipfile

from traslado.advanced import sanitize
from traslado.model import Options
from traslado.runner import discover, execute
from traslado.safety import check_archive, run_bounded


class Boundaries(unittest.TestCase):
    def test_dtd_rejected_even_after_large_whitespace(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "doc.docx"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("word/document.xml", b" " * 70000 + b'<!DOCTYPE root [<!ENTITY x SYSTEM "file:///secret">]><root/>')
            with self.assertRaisesRegex(ValueError, "DTD"):
                check_archive(archive, Options())

    def test_advanced_external_links_and_active_html_are_passive(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "assets").mkdir()
            text = '<table onclick="evil()"><tr><td rowspan="2">X</td></tr></table><script>evil()</script>![x](https://example.com/a.png) [e](../../secret)'
            safe = sanitize(text, root, root, 1, Options())
            self.assertNotIn("onclick", safe)
            self.assertNotIn("<script>", safe)
            self.assertNotIn("![", safe)
            self.assertNotIn("](", safe)
            self.assertIn('rowspan="2"', safe)

    def test_utf16_doctype_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            archive = Path(raw) / "doc.docx"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///secret">]><r/>'.encode("utf-16"))
            with self.assertRaisesRegex(ValueError, "DTD"):
                check_archive(archive, Options())

    def test_advanced_reference_destinations_removed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            content = '![remote][id]\n\n[id]: https://example.invalid/tracker.png\n[run][js]\n[js]: javascript:alert(1)'
            safe = sanitize(content, root, root, 1, Options())
            # Destinations remain literal source text; they are no longer active syntax.
            self.assertNotIn("[id]:", safe)
            self.assertNotIn("[js]:", safe)
            self.assertNotIn("![", safe)

    def test_batch_cannot_ingest_own_output(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.txt").write_text("sentinel")
            with self.assertRaises(ValueError):
                discover(root, root / "generated", recursive=True)

    def test_bad_advanced_executable_does_not_publish_pdf_result(self):
        import fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "a.pdf"
            with fitz.open() as pdf:
                page = pdf.new_page()
                page.insert_text((50, 50), "SAFE NATIVE TEXT")
                pdf.save(source)
            result = execute(source, root / "out", Options(advanced_kit=str(root / "missing.exe")))
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["output"])
            self.assertEqual(list((root / "out").iterdir()), [])

    def test_resource_failure_leaves_no_published_partial_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "a.txt"
            source.write_text("SENTINEL " * 100)
            result = execute(source, root / "out", Options(max_output_bytes=100))
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["output"])
            self.assertEqual(source.read_text(), "SENTINEL " * 100)
            self.assertEqual(list((root / "out").iterdir()), [])

    def test_table_crossing_text_block_does_not_drop_outside_caption(self):
        import fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "table.pdf"
            with fitz.open() as pdf:
                p = pdf.new_page(width=400, height=400)
                p.insert_text((60, 90), "KEEP_OUTSIDE\nA\nB\nC", fontsize=12)
                for y in (100, 120, 140, 160):
                    p.draw_line((50, y), (250, y))
                for x in (50, 150, 250):
                    p.draw_line((x, 100), (x, 160))
                p.insert_text((170, 115), "1")
                p.insert_text((170, 135), "2")
                p.insert_text((170, 155), "3")
                pdf.save(path)
            result = execute(path, root / "out", Options(mode="native"))
            self.assertEqual(result["status"], "partial")
            package = Path(result["output"])
            self.assertIn("KEEP_OUTSIDE", (package / "document.md").read_text(encoding="utf-8"))
            document = json.loads((package / "document.json").read_text(encoding="utf-8"))
            self.assertTrue(any("overlap_text_preserved" in w for w in document["pages"][0]["warnings"]))

    def test_manifest_counts_toward_output_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "tiny.txt"
            path.write_text("hello")
            result = execute(path, root / "out", Options(max_output_bytes=1531))
            self.assertEqual(result["status"], "failed")
            self.assertIsNone(result["output"])
            self.assertEqual(list((root / "out").iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_successful_parent_does_not_leave_descendant(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            signal = root / "descendant.txt"
            child = "import time; from pathlib import Path; time.sleep(.5); Path(" + repr(str(signal)) + ").write_text('escaped')"
            parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]);"
            code = run_bounded([sys.executable, "-c", parent], root, 5, root / "log")
            self.assertEqual(code, 0)
            time.sleep(.8)
            self.assertFalse(signal.exists(), "A descendant escaped successful parent cleanup")

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_windows_memory_budget_blocks_large_allocation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            code = run_bounded([sys.executable, "-c", "x=bytearray(180*1024*1024)"], root, 10,
                               root / "log", max_memory_bytes=128*1024*1024)
            self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
