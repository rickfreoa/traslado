"""Independent published-byte and image derivative contracts for rc4."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from freoa_v6.advanced import sanitize
from freoa_v6.chunks import write_chunks
from freoa_v6.model import Block, Document, Options, Page
from freoa_v6.render import render
from freoa_v6.runner import execute


class ChunkTransportRC4(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="freoa-chunk-transport-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_published_markdown_bytes_hash_offsets_and_manifest_agree(self):
        source = self.root / "Unicode ação😀.txt"
        original = ("Unicode ação😀中文\nPrimeiro parágrafo.\n\nSegundo parágrafo.\n" * 30).encode("utf-8")
        source.write_bytes(original)
        result = execute(source, self.root / "out", Options(chunks=True, chunk_size_chars=300, chunk_overlap_chars=50))
        self.assertEqual(result["status"], "complete", result)
        package = Path(result["output"])
        raw = (package / "document.md").read_bytes()
        markdown = raw.decode("utf-8")
        actual_hash = hashlib.sha256(raw).hexdigest()
        manifest = json.loads((package / "manifest.json").read_bytes())
        entry = next(e for e in manifest["files"] if e["path"] == "document.md")
        self.assertEqual(entry["sha256"], actual_hash)
        self.assertEqual(entry["bytes"], len(raw))
        chunks = [json.loads(line) for line in (package / "chunks/chunks.jsonl").read_bytes().decode("utf-8").splitlines()]
        summary = json.loads((package / "chunks/manifest.json").read_bytes())
        self.assertEqual(summary["markdown_sha256"], actual_hash)
        self.assertEqual(summary["count"], len(chunks))
        covered = bytearray(len(markdown))
        for chunk in chunks:
            start, end = chunk["char_start"], chunk["char_end"]
            self.assertEqual(chunk["markdown_sha256"], actual_hash)
            self.assertEqual(chunk["source_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(chunk["offset_unit"], "unicode_code_points")
            self.assertEqual(chunk["text"], markdown[start:end])
            self.assertEqual(chunk["char_count"], end - start)
            covered[start:end] = b"\1" * (end - start)
        self.assertTrue(all(covered))
        self.assertEqual(source.read_bytes(), original)

    def test_literal_fenced_and_inline_code_image_paths_remain_exact(self):
        fence = "```text\n![literal](assets/code.png)\n" + "LITERAL_SENTINEL " * 40 + "\n```"
        inline = "Prefix `![inline](assets/inline.png)` suffix " + "texto " * 30
        document = Document("Code.md", "a" * 64, [Page(1, "native", [
            Block("text", fence + "\n\n" + inline), Block("image", asset="assets/actual.png")])])
        chunks = self.root / "chunks"
        write_chunks(document, render(document), chunks, size_chars=256, overlap_chars=100)
        bodies = [(p.read_bytes().decode("utf-8")) for p in sorted(chunks.glob("*.md"))]
        joined = "\n".join(bodies)
        self.assertIn(fence, joined)
        self.assertIn("`![inline](assets/inline.png)`", joined)
        self.assertNotIn("![literal](../assets/code.png)", joined)
        self.assertNotIn("![inline](../assets/inline.png)", joined)
        self.assertIn("![Imagem da página 1](../assets/actual.png)", joined)

    def test_real_advanced_sanitize_exported_image_links_work_in_chunk_directory(self):
        from PIL import Image
        model = self.root / "model"
        package = self.root / "package"
        model.mkdir(); package.mkdir(); (package / "assets").mkdir()
        Image.new("RGB", (20, 20), "red").save(model / "diagram.png")
        passive = sanitize("DIAGRAM_SENTINEL\n\n![diagram](diagram.png)", model, package, 1, Options())
        document = Document("Advanced.pdf", "b" * 64, [Page(1, "advanced", [
            Block("text", passive, engine="mineru-kit-4.0.10",
                  details={"markdown_policy": "literal_links_and_references_local_images_only"})])])
        write_chunks(document, render(document), package / "chunks")
        body = (package / "chunks/0001.md").read_bytes().decode("utf-8")
        self.assertTrue((package / "assets/advanced-1-1.png").is_file())
        self.assertIn("![Imagem reconstruída](../assets/advanced-1-1.png)", body)
        self.assertNotIn("![Imagem reconstruída](assets/advanced-1-1.png)", body)
        self.assertTrue(((package / "chunks") / "../assets/advanced-1-1.png").resolve().is_file())

    def test_full_page_illustration_suppression_is_explicit_and_previews_restore_asset(self):
        import fitz
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (900, 1200), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 100, 800, 1100), outline="black", width=15)
        draw.line((100, 100, 800, 1100), fill="red", width=12)
        stream = io.BytesIO(); image.save(stream, "PNG")
        source = self.root / "Illustration.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=450, height=600)
            page.insert_image(page.rect, stream=stream.getvalue())
            for i in range(12):
                page.insert_text((30, 30 + i * 24), f"NATIVE_SENTINEL_{i:02d} Original native text survives.", fontsize=10)
            pdf.save(source)
        original = source.read_bytes()
        for previews in (False, True):
            with self.subTest(previews=previews):
                result = execute(source, self.root / ("out" + str(previews)), Options(scan_previews=previews))
                self.assertIn(result["status"], {"complete", "partial"}, result)
                package = Path(result["output"])
                document = json.loads((package / "document.json").read_bytes())
                self.assertEqual(document["pages"][0]["route"], "native")
                assets = [b for b in document["pages"][0]["blocks"] if b["kind"] == "image"]
                self.assertEqual(len(assets), int(previews))
                if not previews:
                    summary = (package / "LEIA-AVISOS.txt").read_bytes().decode("utf-8")
                    self.assertIn("Imagens de página inteira", summary)
                    self.assertIn("Podem incluir ilustrações", summary)
                else:
                    self.assertTrue((package / assets[0]["asset"]).is_file())
                    self.assertTrue(assets[0]["details"]["lossy_preview"])
                self.assertIn("NATIVE_SENTINEL_11", (package / "document.md").read_bytes().decode("utf-8"))
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
