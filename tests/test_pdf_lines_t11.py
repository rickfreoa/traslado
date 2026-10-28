# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import fitz

from traslado import model, pdf, worker
from traslado.model import Block, Document, Options
from traslado.safety import sha256


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "1ff904eafe5f313ea359ff4a8131286e2b6e14a1"


def baseline_module(name, revision=BASELINE):
    try:
        source = subprocess.check_output(
            ["git", "show", f"{revision}:src/freoa_v6/{name}.py"], cwd=ROOT, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        # Stage-identity checks need the repository history; installed-wheel CI runs skip them.
        raise unittest.SkipTest(f"revisão {revision} indisponível fora do repositório")
    module = types.ModuleType(f"traslado._p0_{revision.replace('.', '_')}_{name}")
    module.__package__ = "traslado"
    # dataclasses resolves annotations through the defining module.
    with patch.dict(sys.modules, {module.__name__: module}):
        exec(compile(source, f"{revision}/{name}.py", "exec"), module.__dict__)
    return module


def release_neutral_dict(data):
    """Identity across releases: only the release version (and options added in 1.1) may differ."""
    data = dict(data, converter_version=None)
    data["options"] = {k: v for k, v in (data.get("options") or {}).items() if k not in ("ocr_workers", "furniture")}
    return data


def release_neutral(name, payload):
    return release_neutral_dict(json.loads(payload)) if Path(name).name == "document.json" else payload


class PDFLinesT11(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lines-", dir=ROOT / ".t11-logs")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, kind="simple", rotation=0):
        path = self.root / f"{kind}-{rotation}.pdf"
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            page.insert_text((40, 50), "Small body", fontsize=10)
            page.insert_text((40, 110), "Large body", fontsize=20)
            page.insert_text((40, 160), "First line\nSecond line", fontsize=11)
            if kind == "columns":
                for x, label in ((40, "Left"), (340, "Right")):
                    for y in (220, 280, 340):
                        page.insert_text((x, y), f"{label} paragraph at {y}.")
            if kind == "table":
                for x in (40, 160, 280):
                    page.draw_line((x, 220), (x, 300))
                for y in (220, 260, 300):
                    page.draw_line((40, y), (280, y))
                for x, y, text in ((50, 240, "A"), (170, 240, "B"),
                                   (50, 280, "C"), (170, 280, "D")):
                    page.insert_text((x, y), text)
            if kind == "image":
                pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 100, 100), False)
                pix.clear_with(180)
                page.insert_image(fitz.Rect(40, 220, 140, 320), pixmap=pix)
            page.set_rotation(rotation)
            doc.save(path)
        return path

    def extract(self, path, options=None):
        workspace = self.root / f"extract-{len(list(self.root.iterdir()))}"
        workspace.mkdir()
        document = Document(path.name, sha256(path))
        pdf.extract(path, workspace, options or Options(mode="native", furniture="off"), document)
        return document

    def test_lines_spans_sizes_and_one_dict_call_with_unchanged_flags(self):
        path = self.source()
        original = fitz.Page.get_text
        calls = []

        def capture(page, option="text", *args, **kwargs):
            calls.append((option, kwargs))
            return original(page, option, *args, **kwargs)

        with patch.object(fitz.Page, "get_text", capture), patch.object(
                pdf, "render_page", side_effect=AssertionError("unexpected render")):
            document = self.extract(path)
        flags = fitz.TEXTFLAGS_BLOCKS & ~fitz.TEXT_PRESERVE_IMAGES
        self.assertEqual(calls, [("blocks", {"flags": flags}), ("dict", {"flags": flags})])
        sizes = set()
        for block in document.pages[0].blocks:
            self.assertIsNone(block.role)
            self.assertTrue(block.lines)
            text = "\n".join("".join(s["text"] for s in line["spans"]) for line in block.lines)
            self.assertEqual(text.split(), block.text.split())
            for line in block.lines:
                self.assertEqual(set(line), {"bbox", "spans"})
                self.assertEqual(len(line["bbox"]), 4)
                for span in line["spans"]:
                    self.assertEqual(set(span), {"text", "size", "flags", "font", "bbox"})
                    self.assertIsInstance(span["size"], float)
                    self.assertIsInstance(span["flags"], int)
                    self.assertIsInstance(span["font"], str)
                    self.assertEqual(len(span["bbox"]), 4)
                    sizes.add(span["size"])
        self.assertTrue({10., 20.}.issubset(sizes))

    def test_number_matching_and_per_block_fail_closed_preserve_document(self):
        path = self.source()
        expected = self.extract(path).to_dict()
        original = fitz.Page.get_text
        for failure in ("missing", "absent_number", "wrong_text", "reversed", "spaces"):
            def altered(page, option="text", *args, **kwargs):
                result = original(page, option, *args, **kwargs)
                if option == "dict":
                    blocks = result["blocks"]
                    if failure == "missing":
                        blocks.pop(0)
                    elif failure == "absent_number":
                        blocks[0].pop("number")
                    elif failure == "wrong_text":
                        blocks[0]["lines"][0]["spans"][0]["text"] = "different"
                    elif failure == "spaces":
                        span = blocks[0]["lines"][0]["spans"][0]
                        span["text"] = "  " + span["text"].replace(" ", "\t  ") + " "
                    blocks.reverse()
                return result
            with self.subTest(failure=failure), patch.object(fitz.Page, "get_text", altered):
                actual = self.extract(path)
                self.assertEqual(actual.to_dict(), expected)
                if failure in ("missing", "absent_number", "wrong_text"):
                    self.assertIsNone(actual.pages[0].blocks[0].lines)
                else:
                    self.assertTrue(actual.pages[0].blocks[0].lines)
                self.assertTrue(all(b.lines for b in actual.pages[0].blocks[1:]))

    def test_optional_fields_and_memory_only_lines(self):
        block = Block("text", "body")
        self.assertIsNone(block.lines)
        self.assertIsNone(block.role)
        document = Document("source", "hash", pages=[model.Page(1, "native", [block])])
        old = baseline_module("model")
        expected = old.Document("source", "hash", pages=[old.Page(1, "native", [old.Block("text", "body")])])
        self.assertEqual(release_neutral_dict(document.to_dict()), release_neutral_dict(expected.to_dict()))
        block.lines = [{"bbox": [1, 2, 3, 4], "spans": []}]
        self.assertEqual(release_neutral_dict(document.to_dict()), release_neutral_dict(expected.to_dict()))
        self.assertTrue(block.lines)  # serialization must not mutate memory
        block.role = "reserved"
        self.assertEqual(document.to_dict()["pages"][0]["blocks"][0]["role"], "reserved")

    def test_table_image_and_ocr_blocks_have_no_lines(self):
        for kind in ("table", "image"):
            with self.subTest(kind=kind):
                document = self.extract(self.source(kind))
                self.assertTrue(any(b.kind == kind for b in document.pages[0].blocks))
                for block in document.pages[0].blocks:
                    if block.kind != "text":
                        self.assertIsNone(block.lines)
                        self.assertIsNone(block.role)
        original = fitz.Page.get_text

        def native_only(page, option="text", *args, **kwargs):
            self.assertNotEqual(option, "dict", "dict on OCR page")
            return original(page, option, *args, **kwargs)

        with patch.object(pdf, "recognize_blocks", return_value=([Block("text", "OCR", engine="tesseract")], [])), \
                patch.object(pdf, "render_page"), patch.object(fitz.Page, "get_text", native_only):
            document = self.extract(self.source(), Options(mode="ocr", tables=False))
        self.assertEqual(document.pages[0].route, "ocr")
        self.assertIsNone(document.pages[0].blocks[0].lines)

    def run_worker(self, path, workspace, extractor, document_type):
        workspace.mkdir()
        request = self.root / "request.json"
        request.write_text(json.dumps({"workspace": str(workspace), "snapshot": str(path),
            "source_name": path.name, "sha256": sha256(path),
            # furniture="off": the stage 6 contract makes "off" reproduce the stage 5 state exactly.
            "options": asdict(Options(mode="native", chunks=True, furniture="off"))}), encoding="utf-8")
        with patch.object(sys, "argv", ["worker", str(request)]), \
                patch.object(worker.time, "monotonic", return_value=100), \
                patch.object(worker, "Document", document_type), patch.object(pdf, "extract", extractor):
            code = worker.main()
        self.assertEqual(code, 0, (workspace / "error.json").read_text() if code else "")

    def test_all_artifacts_byte_identical_to_stage4_and_1_0_0(self):
        current_extract = pdf.extract
        for revision in (BASELINE, "v1.0.0"):
            previous = baseline_module("pdf", revision)
            old_model = baseline_module("model", revision)
            previous.Block = old_model.Block
            for kind, rotation in (("simple", 0), ("columns", 0), ("table", 0),
                                   ("simple", 90), ("simple", 180), ("simple", 270)):
                with self.subTest(revision=revision, kind=kind, rotation=rotation):
                    path = self.source(kind, rotation)
                    before = self.root / f"before-{revision}-{kind}-{rotation}"
                    after = self.root / f"after-{revision}-{kind}-{rotation}"
                    self.run_worker(path, before, previous.extract, old_model.Document)
                    self.run_worker(path, after, current_extract, Document)
                    files = {p.relative_to(before) for p in before.rglob("*") if p.is_file()}
                    self.assertEqual(files, {p.relative_to(after) for p in after.rglob("*") if p.is_file()})
                    for name in sorted(files):
                        self.assertEqual(release_neutral(name, (before / name).read_bytes()),
                                         release_neutral(name, (after / name).read_bytes()), str(name))


if __name__ == "__main__":
    unittest.main()
