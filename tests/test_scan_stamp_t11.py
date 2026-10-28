# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from traslado import pdf
from traslado.chunks import write_chunks
from traslado.model import Block, Document, Options
from traslado.render import layout
from test_pdf_lines_t11 import baseline_module


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "e7c3b7e79e66c0e410215c9c592a62a18cfafc5a"
NEW_METRICS = {"single_image_ratio", "visible_glyph_area_ratio", "invisible_text_chars",
               "scan_stamp", "invisible_text_layer"}
LONG = "Bates 123456 autenticado " * 10


class ScanStampT11(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stamp-", dir=ROOT / ".t11-logs")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, text=LONG, images=(1.,), render_mode=0, opacity=1, rotation=0):
        path = self.root / f"source-{len(list(self.root.iterdir()))}.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 16), False)
            pix.clear_with(180)
            y = 0
            for ratio in images:
                page.insert_image((0, y, 600, y + 800 * ratio), pixmap=pix, keep_proportion=False)
                y += 800 * ratio
            if text:
                # A long stamp can still occupy less than 1% of a page.
                page.insert_text((30, 40), text, fontsize=3, render_mode=render_mode,
                                 fill_opacity=opacity, stroke_opacity=opacity)
            page.set_rotation(rotation)
            doc.save(path)
        return path

    def extract(self, path, mode="auto", extractor=None):
        workspace = self.root / f"out-{len(list(self.root.iterdir()))}"
        workspace.mkdir()
        doc = Document(path.name, "hash")
        options = Options(mode=mode, images=False, tables=False, chunks=True)
        with patch.object(pdf, "render_page") as render, patch.object(
                pdf, "recognize_blocks", return_value=([Block("text", "OCR body", engine="tesseract")], [])) as ocr:
            extract = extractor or pdf.extract
            with patch.dict(extract.__globals__, render_page=render, recognize_blocks=ocr):
                extract(path, workspace, options, doc)
        return doc, workspace, render.call_count, ocr.call_count

    def test_short_and_long_visible_stamps_require_ocr_and_retain_native_json(self):
        for text in ("Fls. 23", LONG):
            with self.subTest(text=text):
                doc, _, renders, calls = self.extract(self.source(text=text))
                page = doc.pages[0]
                self.assertTrue(page.metrics["scan_stamp"])
                self.assertFalse(page.metrics["invisible_text_layer"])
                self.assertEqual(page.metrics["single_image_ratio"], 1.)
                self.assertLess(page.metrics["visible_glyph_area_ratio"], .01)
                self.assertEqual(page.metrics["invisible_text_chars"], 0)
                self.assertEqual((page.route, renders, calls), ("ocr", 1, 1))
                self.assertEqual(page.native_text, text.strip())
                self.assertEqual(doc.to_dict()["pages"][0]["native_text"], text.strip())
                self.assertTrue(page.metrics["native_text_retained_in_json_ocr_supersedes_markdown"])
                self.assertEqual([b.text for b in page.blocks], ["OCR body"])
                self.assertEqual(page.warnings, [])

    def test_invisible_render_mode_and_zero_opacity_remain_native(self):
        for render_mode, opacity in ((3, 1), (0, 0)):
            with self.subTest(render_mode=render_mode, opacity=opacity):
                doc, _, renders, calls = self.extract(self.source(render_mode=render_mode, opacity=opacity))
                page = doc.pages[0]
                self.assertTrue(page.metrics["invisible_text_layer"])
                self.assertFalse(page.metrics["scan_stamp"])
                self.assertEqual(page.metrics["invisible_text_chars"], len(LONG))
                self.assertEqual(page.metrics["visible_glyph_area_ratio"], 0)
                self.assertEqual((page.route, renders, calls), ("native", 0, 0))
                self.assertEqual(page.native_text, LONG.strip())

    def test_two_images_do_not_combine_into_single_scan(self):
        doc, _, _, calls = self.extract(self.source(images=(.4, .4)))
        page = doc.pages[0]
        # Below the 50% single-image threshold no class can apply and no new metric is recorded.
        self.assertTrue(NEW_METRICS.isdisjoint(page.metrics))
        self.assertEqual((page.route, calls), ("native", 0))

    def test_half_page_boundary_and_clipped_image(self):
        doc, _, _, _ = self.extract(self.source(images=(.499,)))
        self.assertTrue(NEW_METRICS.isdisjoint(doc.pages[0].metrics))
        for ratio in (.5, 1.2):
            doc, _, _, _ = self.extract(self.source(images=(ratio,)))
            self.assertTrue(doc.pages[0].metrics["scan_stamp"])
            self.assertAlmostEqual(doc.pages[0].metrics["single_image_ratio"], min(ratio, 1.))

    def test_rotated_page_keeps_full_image_ratio(self):
        # Review of stage 7: page.rect is the displayed box; image boxes are unrotated.
        for rotation in (90, 270):
            doc, _, _, _ = self.extract(self.source(text="Fls. 23", rotation=rotation))
            self.assertAlmostEqual(doc.pages[0].metrics["single_image_ratio"], 1.)
            self.assertTrue(doc.pages[0].metrics["scan_stamp"])

    def test_malformed_trace_span_records_nothing(self):
        path = self.source(text="Fls. 23")
        with patch.object(fitz.Page, "get_texttrace", return_value=[{"type": 0}]):
            doc, _, _, _ = self.extract(path)
        self.assertTrue(NEW_METRICS.isdisjoint(doc.pages[0].metrics))

    def test_forced_modes_keep_their_routes(self):
        for mode in ("native", "ocr"):
            doc, _, _, calls = self.extract(self.source(), mode=mode)
            self.assertTrue(doc.pages[0].metrics["scan_stamp"])
            self.assertEqual(doc.pages[0].route, mode)
            self.assertEqual(calls, int(mode == "ocr"))

    def test_trace_failure_keeps_stage_six_decision_and_no_new_metrics(self):
        baseline = baseline_module("pdf", BASELINE)
        for text in ("Fls. 23", LONG):
            path = self.source(text=text)
            before, _, _, _ = self.extract(path, extractor=baseline.extract)
            with patch.object(fitz.Page, "get_texttrace", side_effect=RuntimeError("trace failed")):
                after, _, _, _ = self.extract(path)
            self.assertEqual(before.to_dict(), after.to_dict())
            self.assertTrue(NEW_METRICS.isdisjoint(after.pages[0].metrics))

    def test_native_artifacts_identical_to_stage_six_except_new_metrics(self):
        baseline = baseline_module("pdf", BASELINE)
        for rotation in (0, 90):
            path = self.source(text="Native body text", images=(), rotation=rotation)
            before, before_dir, _, _ = self.extract(path, extractor=baseline.extract)
            after, after_dir, renders, calls = self.extract(path)
            # Pages without a dominant image record no new metric: document.json identical to stage 6.
            self.assertTrue(NEW_METRICS.isdisjoint(after.pages[0].metrics))
            self.assertEqual((renders, calls), (0, 0))
            self.assertEqual(after.to_dict(), before.to_dict())
            for doc, directory in ((before, before_dir), (after, after_dir)):
                markdown = layout(doc).markdown
                (directory / "document.md").write_text(markdown, encoding="utf-8")
                write_chunks(doc, markdown, directory / "chunks")
            for artifact in ["document.md"] + [p.relative_to(before_dir).as_posix() for p in (before_dir / "chunks").iterdir()]:
                self.assertEqual((before_dir / artifact).read_bytes(), (after_dir / artifact).read_bytes())


if __name__ == "__main__":
    unittest.main()
