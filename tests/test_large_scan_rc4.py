# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz
from PIL import Image

from traslado import pdf
from traslado.model import Block, Document, Options


class LargeScanRC4(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="freoa-large-scan-rc4-")
        self.root = Path(self.temp.name)
        self.workspace = self.root / "package"
        self.workspace.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def source(self, count=1, native=True, full_image=True, width=300, height=400):
        data = io.BytesIO()
        Image.new("RGB", (60, 80), "#e7e7e7").save(data, "PNG")
        document = fitz.open()
        for number in range(count):
            page = document.new_page(width=width, height=height)
            if full_image:
                page.insert_image(page.rect, stream=data.getvalue(), keep_proportion=False)
            if native:
                # Hidden OCR-like text is still source text: a large book with
                # this layer must not render/export its full-page scan again.
                page.insert_textbox(fitz.Rect(10, 10, width-10, height-10),
                                    ("SOURCE_TEXT_SENTINEL 12345 original paragraph. " * 12),
                                    fontsize=8, render_mode=3)
        path = self.root / "scan.pdf"
        document.save(path)
        document.close()
        return path

    def extract(self, source, options=None):
        document = Document(source.name, "a" * 64)
        pdf.extract(source, self.workspace, options or Options(), document)
        return document

    def test_large_existing_text_scan_has_no_accumulated_page_images(self):
        source = self.source(count=120)
        with patch.object(pdf, "render_page", side_effect=AssertionError("native fast path rendered")), \
                patch.object(pdf, "recognize", side_effect=AssertionError("native fast path OCR")), \
                patch.object(pdf, "recognize_blocks", side_effect=AssertionError("native fast path OCR")):
            document = self.extract(source)
        self.assertEqual(len(document.pages), 120)
        self.assertEqual(list((self.workspace / "assets").iterdir()), [])
        for page in document.pages:
            self.assertEqual(page.route, "native")
            self.assertTrue(page.metrics["scanned_page_with_native_text"])
            self.assertFalse(page.metrics["scan_previews_exported"])
            self.assertEqual(page.warnings, [])
            self.assertIn("SOURCE_TEXT_SENTINEL", "".join(block.text for block in page.blocks))

    def test_full_ocr_uses_only_one_private_png_and_removes_every_input(self):
        source = self.source(count=4, native=False, width=100, height=150)
        seen = []

        def recognize(image, workspace, options):
            self.assertTrue(image.is_file())
            self.assertNotIn(workspace, image.parents)
            self.assertEqual(len(list(self.root.glob(".freoa-ocr-page-*"))), 1)
            seen.append(image)
            return Block("text", "OCR_SENTINEL", engine="tesseract"), []

        # T11 A3-0: full-page OCR goes through recognize_blocks (same single OCR run per page).
        with patch.object(pdf, "recognize_blocks", side_effect=lambda i, w, o, transform: ([recognize(i, w, o)[0]], [])):
            document = self.extract(source, Options(dpi=72))
        self.assertEqual(len(seen), 4)
        self.assertTrue(all(not path.exists() for path in seen))
        self.assertEqual(list(self.root.glob(".freoa-ocr-page-*")), [])
        self.assertEqual(list((self.workspace / "assets").iterdir()), [])
        self.assertTrue(all(page.route == "ocr" for page in document.pages))
        self.assertTrue(all(page.blocks[0].details["source_page"] == page.number for page in document.pages))

    def test_page_render_failures_are_local_and_native_fallback_survives(self):
        source = self.source(count=3)
        with patch.object(pdf, "render_page", side_effect=ValueError("Página excede limite de pixels")):
            document = self.extract(source, Options(mode="ocr"))
        self.assertEqual(len(document.pages), 3)
        self.assertTrue(all(any(w.startswith("ocr_failed:") for w in page.warnings) for page in document.pages))
        self.assertTrue(all("SOURCE_TEXT_SENTINEL" in "".join(b.text for b in page.blocks) for page in document.pages))
        self.assertEqual(list(self.root.glob(".freoa-ocr-page-*")), [])

    def test_optional_scan_previews_are_bounded_and_marked_lossy(self):
        source = self.source(width=2000, height=1500)
        document = self.extract(source, Options(scan_previews=True))
        images = [b for b in document.pages[0].blocks if b.kind == "image"]
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].asset.endswith("-preview.jpg"))
        self.assertTrue(images[0].details["lossy_preview"])
        with Image.open(self.workspace / images[0].asset) as image:
            self.assertLessEqual(max(image.size), 1200)
        self.assertTrue(document.pages[0].metrics["scan_previews_exported"])

    def test_preview_failure_does_not_discard_source_text(self):
        source = self.source()
        document = self.extract(source, Options(scan_previews=True, max_pixels=1000))
        page = document.pages[0]
        self.assertIn("SOURCE_TEXT_SENTINEL", "".join(b.text for b in page.blocks))
        self.assertTrue(any(w.startswith("scan_preview_failed:") for w in page.warnings))
        self.assertEqual(list((self.workspace / "assets").iterdir()), [])

    def test_ordinary_illustration_export_is_retained(self):
        source = self.source(full_image=False)
        with fitz.open(source) as document:
            data = io.BytesIO()
            Image.new("RGB", (30, 30), "red").save(data, "PNG")
            document[0].insert_image(fitz.Rect(240, 300, 290, 350), stream=data.getvalue())
            document.saveIncr()
        document = self.extract(source, Options(dpi=72))
        assets = [b.asset for b in document.pages[0].blocks if b.kind == "image"]
        self.assertEqual(assets, ["assets/page-1-image-1.png"])
        self.assertTrue((self.workspace / assets[0]).is_file())
        self.assertIn("image_semantics_unverified", document.pages[0].warnings)

    def test_progress_is_atomic_bounded_and_has_no_source_text(self):
        document = self.extract(self.source(count=5))
        progress = json.loads((self.workspace / "progress.json").read_text(encoding="utf-8"))
        self.assertEqual(progress, {"page": 5, "total": len(document.pages), "route": "native"})
        self.assertFalse((self.workspace / ".progress.json.tmp").exists())

    def test_progress_reader_cannot_interrupt_conversion(self):
        pdf._progress(self.workspace, 1, 3, "native")
        target = self.workspace / "progress.json"
        if os.name == "nt":
            # Real Windows sharing violation, rather than only a mocked rename.
            with target.open("rb"):
                pdf._progress(self.workspace, 2, 3, "ocr")
                self.assertEqual(json.loads(target.read_text())["page"], 1)
        else:
            with patch.object(Path, "replace", side_effect=PermissionError("reader holds progress")):
                pdf._progress(self.workspace, 2, 3, "ocr")
            self.assertEqual(json.loads(target.read_text())["page"], 1)
        self.assertFalse((self.workspace / ".progress.json.tmp").exists())
        pdf._progress(self.workspace, 3, 3, "native")
        self.assertEqual(json.loads(target.read_text())["page"], 3)


if __name__ == "__main__":
    unittest.main()
