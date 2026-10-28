# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import csv
from dataclasses import asdict
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import fitz

from traslado import ocr, pdf, worker
from traslado.model import Document, Options
from traslado.safety import sha256


ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = Path(r"C:\Users\rfreoa\AppData\Local\Freoa\MarkdownConverter\releases\1.0.0_e3c1818437b949bdb62028427f521637\ocr")
HEADER = "level page_num block_num par_num line_num word_num left top width height conf text".split()


def tsv(rows):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
    writer.writerow(HEADER)
    writer.writerows(rows)
    return stream.getvalue()


ROWS = [
    [4, 1, 2, 1, 1, 0, 0, 0, 99, 99, -1, ""],
    [5, 1, 2, 1, 1, 1, 10, 20, 30, 10, 90, "Alpha"],
    [5, 1, 2, 1, 1, 2, 45, 20, 20, 10, 70, "beta"],
    [5, 1, 2, 1, 2, 1, 12, 40, 25, 12, 80, "gamma"],
    [5, 1, 2, 2, 1, 1, 15, 80, 35, 10, 99, "Delta"],
    [5, 1, 3, 1, 1, 1, 90, 10, 20, 10, 95, "42"],
    [5, 1, 3, 1, 1, 2, 0, 0, 1, 1, 0, "   "],
]
TXT = "Alpha   beta\ngamma\n\nDelta\n42\n"


class GeometryT11(unittest.TestCase):
    def setUp(self):
        (ROOT / ".t11-logs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="geometry-", dir=ROOT / ".t11-logs")
        self.root = Path(self.temp.name)
        self.exe = self.root / "tesseract.exe"
        self.exe.write_bytes(b"inert")
        self.options = Options(language="eng", tesseract=str(self.exe))
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def fake(self, text=TXT, rows=None, languages="eng", diagnostics="", code=0, missing=False, raw_tsv=None):
        def run(argv, cwd, timeout, log, **kwargs):
            self.calls.append(argv)
            if "--list-langs" in argv:
                log.write_text("List of available languages:\n" + languages + "\n", encoding="utf-8")
                return 0
            base = Path(argv[2])
            base.with_suffix(".txt").write_text(text, encoding="utf-8")
            if not missing:
                base.with_suffix(".tsv").write_text(raw_tsv if raw_tsv is not None else
                                                    tsv(ROWS if rows is None else rows), encoding="utf-8")
            log.write_text(diagnostics, encoding="utf-8")
            return code
        return patch.object(ocr, "run_bounded", side_effect=run)

    def recognize(self, transform=lambda x, y: (x / 2 + 7, y / 2 + 11)):
        return ocr.recognize_blocks(self.root / "image.png", self.root, self.options, transform)

    def baseline(self, name):
        # Immutable 1.0.0 source, read only; no checkout or other worktree writes.
        try:
            source = subprocess.check_output(["git", "show", f"v1.0.0:src/freoa_v6/{name}.py"], cwd=ROOT,
                                             stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):  # installed-wheel CI has no history
            raise unittest.SkipTest("v1.0.0 indisponível fora do repositório")
        module = types.ModuleType(f"traslado._t11_baseline_{name}")
        module.__package__ = "traslado"
        exec(compile(source, f"v1.0.0/{name}.py", "exec"), module.__dict__)
        return module

    def test_paragraphs_lines_confidence_metadata_and_single_execution(self):
        with self.fake():
            blocks, notes = self.recognize()
        self.assertEqual(notes, [])
        self.assertEqual([b.text for b in blocks], ["Alpha beta\ngamma", "Delta", "42"])
        self.assertEqual([b.details["ocr_paragraph"] for b in blocks], [[2, 1], [2, 2], [3, 1]])
        self.assertEqual([b.bbox for b in blocks], [[12, 21, 39.5, 37], [14.5, 51, 32, 56], [52, 16, 62, 21]])
        self.assertAlmostEqual(blocks[0].confidence, .8)
        self.assertEqual(blocks[1].confidence, .99)
        for block in blocks:
            self.assertEqual(block.engine, "tesseract")
            self.assertEqual(block.kind, "text")
            self.assertEqual(block.details, {"language": "eng", "requested_languages": ["eng"],
                "available_languages": ["eng"], "language_validation": "listed_and_no_load_error",
                "confidence_kind": "mean_word_score_not_accuracy", "ocr_paragraph": block.details["ocr_paragraph"],
                "geometry": "tesseract_tsv_word_boxes"})
        self.assertEqual(" ".join(b.text for b in blocks).split(), TXT.split())
        self.assertEqual(len(self.calls), 2)  # validation plus one OCR, never a second OCR
        argv = self.calls[1]
        self.assertEqual(argv[3:], ["-l", "eng", "--psm", "3", "-c", "tessedit_create_txt=1", "-c", "tessedit_create_tsv=1"])

    def test_raw_tesseract_tsv_with_unescaped_quotes_keeps_geometry(self):
        # Tesseract writes quotes verbatim; a CSV-quoting reader would swallow the following rows.
        raw = "\t".join(HEADER) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in [
            [5, 1, 1, 1, 1, 1, 10, 20, 30, 10, 90, '"Acordo'],
            [5, 1, 1, 1, 1, 2, 45, 20, 40, 10, 80, 'de'],
            [5, 1, 1, 1, 1, 3, 90, 20, 50, 10, -1, 'Acionistas"'],
            [5, 1, 1, 1, 2, 1, 10, 40, 10, 10, 70, '"A"'],
        ])
        with self.fake(text='"Acordo de Acionistas"\n"A"\n', raw_tsv=raw):
            blocks, notes = self.recognize()
        self.assertEqual(notes, [])
        self.assertEqual([b.text for b in blocks], ['"Acordo de Acionistas"\n"A"'])
        self.assertIsNotNone(blocks[0].bbox)
        self.assertAlmostEqual(blocks[0].confidence, (.9 + .8 + .7) / 3)  # conf -1 stays out of the mean

    def test_mismatch_fallback_preserves_txt_and_whole_page_confidence(self):
        for text in ("Alpha beta gamma Delta 43", "Delta Alpha beta gamma 42"):
            with self.subTest(text=text), self.fake(text=text):
                blocks, notes = self.recognize()
            self.assertEqual(notes, ["ocr_geometry_fallback"])
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0].text, text)
            self.assertIsNone(blocks[0].bbox)
            self.assertAlmostEqual(blocks[0].confidence, .868)
            self.assertNotIn("geometry", blocks[0].details)

    def test_invalid_geometry_and_interleaved_ids_fall_back_without_text_loss(self):
        # A non-finite or negative conf no longer forces fallback: the word keeps its box and stays out of the
        # mean (review of A3-0); malformed geometry and reordering still fall back.
        for column, value in (("width", "nan"), ("height", 0), ("conf", "bad"), ("left", "bad"), ("line_num", 1)):
            rows = [row[:] for row in ROWS]
            # Interleave a line ID after another line; grouping must not reorder.
            index = 3 if column != "line_num" else 4
            rows[index][HEADER.index(column)] = value
            if column == "line_num":
                rows[index][HEADER.index("par_num")] = 1
                rows[2][HEADER.index("line_num")] = 2
            with self.subTest(column=column), self.fake(rows=rows):
                blocks, notes = self.recognize()
            self.assertEqual(notes, ["ocr_geometry_fallback"])
            self.assertEqual(blocks[0].text, TXT.strip())
            self.assertIsNone(blocks[0].bbox)

    def test_empty_and_low_confidence_warnings_remain_page_wide(self):
        with self.fake(text="", rows=[]):
            blocks, notes = self.recognize()
        self.assertEqual(notes, ["ocr_empty"])
        self.assertEqual(blocks[0].text, "")
        rows = [row[:] for row in ROWS]
        for row in rows:
            if row[-1].strip():
                row[-2] = 20
        with self.fake(rows=rows):
            blocks, notes = self.recognize()
        self.assertEqual(notes, ["ocr_low_confidence"])
        self.assertEqual(len(blocks), 3)
        with self.fake(text="different", rows=rows):
            _, notes = self.recognize()
        self.assertEqual(notes, ["ocr_low_confidence", "ocr_geometry_fallback"])

    def test_legacy_recognize_matches_1_0_0_including_argv(self):
        baseline = self.baseline("ocr")
        for text, rows in ((TXT, ROWS), ("", []), ("different", ROWS)):
            with self.subTest(text=text), self.fake(text=text, rows=rows):
                current = ocr.recognize(self.root / "image.png", self.root, self.options)
            current_argv = self.calls[-1][3:]
            with self.fake(text=text, rows=rows):
                # Share the patched runner without changing baseline code.
                with patch.object(baseline, "run_bounded", ocr.run_bounded):
                    previous = baseline.recognize(self.root / "image.png", self.root, self.options)
            self.assertEqual(current, previous)
            self.assertEqual(current_argv, self.calls[-1][3:])

    def test_language_validation_load_errors_limits_and_missing_outputs_unchanged(self):
        self.options = Options(language="eng+por", tesseract=str(self.exe))
        with self.fake(), self.assertRaisesRegex(RuntimeError, "Idiomas OCR ausentes"):
            self.recognize()
        self.assertEqual(len(self.calls), 1)
        self.options = Options(language="eng", tesseract=str(self.exe))
        with self.fake(diagnostics="Failed loading language"), self.assertRaisesRegex(RuntimeError, "Tesseract"):
            self.recognize()
        with self.fake(missing=True), self.assertRaisesRegex(RuntimeError, "contrato esperado"):
            self.recognize()
        self.options = Options(language="eng", tesseract=str(self.exe), max_output_bytes=100)
        with self.fake(), self.assertRaisesRegex(ValueError, "limite"):
            self.recognize()

    def source(self, rotation=0, crop=False):
        path = self.root / f"native-{rotation}-{crop}.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            if crop:
                page.set_cropbox(fitz.Rect(31, 47, 561, 747))
            page.insert_text((260, 350), "Geometry", fontsize=24, rotate=rotation)
            page.set_rotation(rotation)
            doc.save(path)
        return path

    def test_pdf_chunks_boxes_offsets_paragraphs_and_private_png_cleanup(self):
        path = self.source()
        workspace = self.root / "package"
        workspace.mkdir()
        seen = []
        def bounded(argv, cwd, timeout, log, **kwargs):
            if "--list-langs" in argv:
                log.write_text("eng\n", encoding="utf-8")
            else:
                image = Path(argv[1])
                seen.append(image)
                self.assertTrue(image.is_file())
                self.assertNotIn(workspace, image.parents)
                self.assertEqual(len(list(self.root.glob(".freoa-ocr-page-*"))), 1)
                base = Path(argv[2])
                base.with_suffix(".txt").write_text(TXT, encoding="utf-8")
                base.with_suffix(".tsv").write_text(tsv(ROWS), encoding="utf-8")
            return 0
        options = Options(mode="ocr", language="eng", tesseract=str(self.exe), chunks=True)
        with patch.object(ocr, "run_bounded", side_effect=bounded):
            self.run_worker(path, workspace, options)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())
        document = json.loads((workspace / "document.json").read_text(encoding="utf-8"))
        self.assertEqual(len(document["pages"][0]["blocks"]), 3)
        self.assertTrue(all(b["details"]["source_page"] == 1 for b in document["pages"][0]["blocks"]))
        md = (workspace / "document.md").read_bytes().decode("utf-8")
        self.assertIn("gamma\n\nDelta", md)
        refs = []
        for raw in (workspace / "chunks/chunks.jsonl").read_text(encoding="utf-8").splitlines():
            chunk = json.loads(raw)
            self.assertEqual(chunk["text"], md[chunk["char_start"]:chunk["char_end"]])
            refs.extend(chunk["blocks"])
        self.assertEqual(len(refs), 3)
        for ref in refs:
            self.assertEqual(len(ref["bbox"]), 4)
            a, b = ref["char_start"], ref["char_end"]
            self.assertTrue(0 <= a < b <= len(md))
            self.assertTrue(md[a:b].strip())

    def run_worker(self, path, workspace, options):
        request = self.root / "request.json"
        request.write_text(json.dumps({"workspace": str(workspace), "snapshot": str(path),
            "source_name": path.name, "sha256": sha256(path), "options": asdict(options)}), encoding="utf-8")
        with patch.object(sys, "argv", ["worker", str(request)]), patch.object(worker.time, "monotonic", return_value=100):
            code = worker.main()
        self.assertEqual(code, 0, (workspace / "error.json").read_text() if code else "")

    def test_native_artifacts_byte_identical_to_1_0_0(self):
        baseline = self.baseline("pdf")
        path = self.source(rotation=90, crop=True)
        options = Options(mode="auto", language="eng", chunks=True, furniture="off")  # off = stage 5 state
        before, after = self.root / "before", self.root / "after"
        before.mkdir()
        after.mkdir()
        with patch.object(pdf, "extract", baseline.extract):
            self.run_worker(path, before, options)
        with patch.object(pdf, "recognize_blocks", side_effect=AssertionError("native page OCR")):
            self.run_worker(path, after, options)
        files = ["document.md", "document.json"] + [p.relative_to(before).as_posix() for p in (before / "chunks").iterdir()]
        for name in files:
            with self.subTest(artifact=name):
                self.assertEqual((before / name).read_bytes(), (after / name).read_bytes())

    def test_ocr_failure_keeps_native_blocks_and_warning(self):
        path = self.source()
        workspace = self.root / "package"
        workspace.mkdir()
        doc = Document(path.name, sha256(path))
        with patch.object(pdf, "recognize_blocks", side_effect=RuntimeError("sentinel")):
            pdf.extract(path, workspace, Options(mode="ocr"), doc)
        self.assertEqual(doc.pages[0].warnings, ["ocr_failed: sentinel"])
        self.assertEqual(doc.pages[0].blocks[0].text, "Geometry")
        self.assertEqual(doc.pages[0].blocks[0].engine, "pymupdf")
        self.assertEqual(list(self.root.glob(".freoa-ocr-page-*")), [])

    @unittest.skipUnless((OCR_DIR / "tesseract.exe").is_file(), "installed 1.0.0 Tesseract unavailable")
    def test_real_ocr_rotations_crop_and_same_execution_identity(self):
        options = Options(mode="ocr", language="eng", tesseract=str(OCR_DIR / "tesseract.exe"),
                          tessdata=str(OCR_DIR / "tessdata"))
        real_run = ocr.run_bounded
        for rotation in (0, 90, 180, 270):
            for crop in (False, True):
                with self.subTest(rotation=rotation, crop=crop):
                    path = self.source(rotation, crop)
                    with fitz.open(path) as source:
                        native = fitz.Rect(source[0].get_text("words")[0][:4])
                    captured = []
                    def capture(argv, cwd, timeout, log, **kwargs):
                        code = real_run(argv, cwd, timeout, log, **kwargs)
                        if "--list-langs" not in argv:
                            base = Path(argv[2])
                            txt = base.with_suffix(".txt").read_text(encoding="utf-8")
                            rows = csv.DictReader(io.StringIO(base.with_suffix(".tsv").read_text(encoding="utf-8")), delimiter="\t")
                            captured.append((txt, [r["text"].strip() for r in rows if r["level"] == "5" and r["text"].strip()]))
                        return code
                    workspace = self.root / f"real-{rotation}-{crop}"
                    workspace.mkdir()
                    doc = Document(path.name, sha256(path))
                    with patch.object(ocr, "run_bounded", side_effect=capture):
                        pdf.extract(path, workspace, options, doc)
                    self.assertEqual(len(captured), 1)
                    txt, tokens = captured[0]
                    self.assertEqual(tokens, txt.split())
                    self.assertEqual(tokens, ["Geometry"])
                    self.assertEqual(doc.pages[0].warnings, [])
                    self.assertEqual(len(doc.pages[0].blocks), 1)
                    block = doc.pages[0].blocks[0]
                    self.assertEqual(block.text.split(), txt.split())
                    self.assertEqual(block.details["source_page"], 1)
                    box = fitz.Rect(block.bbox)
                    intersection = (box & native).get_area()
                    iou = intersection / (box.get_area() + native.get_area() - intersection)
                    print(f"T11 rotation={rotation} crop={crop} native={list(native)} ocr={block.bbox} IoU={iou:.6f}")
                    self.assertGreaterEqual(iou, .5)


if __name__ == "__main__":
    unittest.main()
