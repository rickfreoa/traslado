from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from traslado.chunks import generate_chunks, write_chunks
from traslado.model import Block, Document, Page
from traslado.render import render


class ChunkTests(unittest.TestCase):
    def doc(self, blocks=None):
        return Document("Cláusula d'Água: \"especial\".pdf", "a" * 64,
                        [Page(1, "ocr", blocks or [Block("text", "# Parte I\n\n" + "Ação válida. " * 150)])])

    def assert_coverage(self, chunks, markdown):
        covered = bytearray(len(markdown))
        previous = -1
        for chunk in chunks:
            start, end = chunk["char_start"], chunk["char_end"]
            self.assertGreater(start, previous)
            self.assertEqual(chunk["text"], markdown[start:end])
            self.assertEqual(chunk["char_count"], len(chunk["text"]))
            self.assertGreater(end, start)
            covered[start:end] = b"\1" * (end - start)
            previous = start
        self.assertTrue(all(covered))

    def test_deterministic_exact_unicode_offsets_no_loss(self):
        document = self.doc()
        md = render(document)
        chunks = generate_chunks(document, md, size_chars=300, overlap_chars=50)
        self.assertEqual(chunks, generate_chunks(document, md, size_chars=300, overlap_chars=50))
        self.assert_coverage(chunks, md)
        self.assertTrue(all(c["char_count"] <= 300 for c in chunks))
        self.assertTrue(any(chunks[i]["char_start"] < chunks[i-1]["char_end"] for i in range(1, len(chunks))))
        self.assertEqual(chunks[0]["markdown_sha256"], hashlib.sha256(md.encode()).hexdigest())
        self.assertTrue(all(c["offset_unit"] == "unicode_code_points" for c in chunks))

    def test_model_table_preserved_whole_above_target(self):
        document = self.doc([Block("text", "Antes " * 70),
                             Block("table", rows=[["célula " * 70, "segunda " * 70]]),
                             Block("text", "Depois " * 70)])
        md = render(document)
        chunks = generate_chunks(document, md, size_chars=300, overlap_chars=60)
        self.assert_coverage(chunks, md)
        table_chunk = [c for c in chunks if any(b["kind"] == "table" for b in c["blocks"])]
        self.assertEqual(len(table_chunk), 1)
        self.assertIn("chunk_atomic_exceeds_target_chars", table_chunk[0]["warnings"])
        self.assertIn("| Coluna 1 | Coluna 2 |", table_chunk[0]["text"])

    def test_native_markdown_table_and_fence_not_blindly_split(self):
        text = "Antes " * 80 + "\n\n| A | B |\n| --- | --- |\n" + "| um | dois |\n" * 70
        text += "\n\n```python\n" + "# falsa seção\n" * 70 + "```\nDepois " * 80
        document = self.doc([Block("text", text)])
        md = render(document)
        chunks = generate_chunks(document, md, size_chars=300, overlap_chars=90)
        self.assert_coverage(chunks, md)
        self.assertEqual(sum("| A | B |" in c["text"] for c in chunks), 1)
        self.assertEqual(sum("```python" in c["text"] for c in chunks), 1)
        self.assertFalse(any(h["title"] == "falsa seção" for c in chunks for h in c["headings_in_chunk"]))

    def test_heading_hierarchy_and_real_provenance_marker_spoof(self):
        text = "# Contrato\n\n" + "A " * 200 + "\n\n## Pagamento\n\n<!-- block:999.1 engine:fake -->\n" + "B " * 400
        document = self.doc([Block("text", text)])
        chunks = generate_chunks(document, render(document), size_chars=300, overlap_chars=30)
        later = [c for c in chunks if c["headings"] and c["headings"][-1]["title"] == "Pagamento"]
        self.assertTrue(later)
        self.assertEqual([h["title"] for h in later[-1]["headings"]], ["Contrato", "Pagamento"])
        self.assertEqual(later[-1]["pages"], [1])
        self.assertEqual(later[-1]["blocks"][0]["engine"], "native")
        self.assertEqual(later[-1]["blocks"][0]["block"], 1)

    def test_partial_and_confidence_in_jsonl_not_precision_claim(self):
        document = self.doc([Block("text", "Trecho " * 200, confidence=0.71)])
        document.status = "partial"
        document.warnings = ["document_warning"]
        document.pages[0].warnings = ["ocr_quality_suspect"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "chunks"
            summary = write_chunks(document, render(document), directory, size_chars=300, overlap_chars=30)
            chunks = [json.loads(line) for line in (directory / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(summary["count"], len(chunks))
            self.assertTrue(all(c["document_status"] == "partial" and c["fidelity"] == "unverified" for c in chunks))
            applicable = [c for c in chunks if c["blocks"]]
            self.assertTrue(all("ocr_quality_suspect" in c["warnings"] for c in applicable))
            self.assertTrue(all("document_warning" in c["warnings"] for c in chunks))
            self.assertEqual(applicable[-1]["blocks"][0]["confidence"], 0.71)
            self.assertEqual(json.loads((directory / "manifest.json").read_text())["count"], len(chunks))

    def test_chunk_md_safe_title_and_local_image_derivative(self):
        document = self.doc([Block("image", asset="assets/page1.png")])
        md = render(document)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "chunks"
            write_chunks(document, md, directory)
            jsonl = json.loads((directory / "chunks.jsonl").read_text(encoding="utf-8"))
            chunk_md = (directory / "0001.md").read_text(encoding="utf-8")
            self.assertEqual(jsonl["text"], md)
            self.assertIn("](../assets/page1.png)", chunk_md)
            self.assertIn('source_name: "Cláusula d\'Água: \\"especial\\".pdf"', chunk_md)

    def test_wrong_rendered_bytes_rejected(self):
        document = self.doc()
        with self.assertRaises(ValueError):
            generate_chunks(document, render(document) + "alteração")

    def test_config_types_and_ranges(self):
        document = self.doc()
        for size, overlap in ((True, 0), (200, 20), (300, True), (300, -1), (300, 300), (200001, 0)):
            with self.subTest(size=size, overlap=overlap), self.assertRaises(ValueError):
                generate_chunks(document, render(document), size_chars=size, overlap_chars=overlap)

    def test_extreme_overlap_terminates_and_covers(self):
        document = self.doc([Block("text", "abc def\n\n" * 100)])
        md = render(document)
        chunks = generate_chunks(document, md, size_chars=256, overlap_chars=255)
        self.assertLess(len(chunks), len(md))
        self.assert_coverage(chunks, md)

    def test_quota_cleans_only_generated_files_no_existing_overwrite(self):
        document = self.doc()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "chunks"
            with self.assertRaises(ValueError):
                write_chunks(document, render(document), directory, max_output_bytes=20)
            self.assertFalse(directory.exists())
            directory.mkdir()
            keep = directory / "keep.txt"
            keep.write_text("preserve")
            with self.assertRaises(ValueError):
                write_chunks(document, render(document), directory)
            self.assertEqual(keep.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
