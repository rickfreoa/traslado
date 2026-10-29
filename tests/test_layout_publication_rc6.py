# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from traslado.model import Options
from traslado.runner import execute

class LayoutPublicationRC6(unittest.TestCase):
    def test_published_readable_pdf_has_exact_map_raw_text_and_chunk_hashes(self):
        import fitz
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);source=root/"Documento ação.pdf"
            paragraph="A preservação da origem permanece disponível em todas as etapas da leitura e da conversão. " * 5
            with fitz.open() as pdf:
                page=pdf.new_page();page.insert_textbox(fitz.Rect(40,40,360,400),paragraph,fontsize=11);pdf.save(source)
            original=source.read_bytes()
            for chunks_enabled in (False,True):
                result=execute(source,root/str(chunks_enabled),Options(chunks=chunks_enabled))
                self.assertIn(result["status"],{"complete","partial"},result)
                output=Path(result["output"]);mdbytes=(output/"document.md").read_bytes();md=mdbytes.decode("utf-8")
                data=json.loads((output/"document.json").read_bytes());mapping=json.loads((output/"document-layout.json").read_bytes())
                self.assertEqual(mapping["markdown_sha256"],hashlib.sha256(mdbytes).hexdigest())
                self.assertEqual(mapping["source_sha256"],hashlib.sha256(original).hexdigest())
                self.assertEqual(mapping["offset_unit"],"unicode_code_points")
                self.assertNotIn("<!-- block:",md);self.assertNotIn("<!-- page:",md)
                self.assertTrue(mapping["blocks"])
                for ref in mapping["blocks"]:
                    block=data["pages"][ref["page"]-1]["blocks"][ref["block"]-1]
                    self.assertEqual(block["engine"],ref["engine"])
                    self.assertTrue(0<=ref["char_start"]<=ref["char_end"]<=len(md))
                self.assertTrue(any("\n" in b["text"] for p in data["pages"] for b in p["blocks"]))
                self.assertTrue(any(b["presentation"]!="literal" for b in mapping["blocks"]))
                if chunks_enabled:
                    chunks=[json.loads(x) for x in (output/"chunks/chunks.jsonl").read_text(encoding="utf-8").splitlines()]
                    self.assertTrue(chunks)
                    for c in chunks:
                        self.assertEqual(c["markdown_sha256"],mapping["markdown_sha256"])
                        self.assertEqual(c["text"],md[c["char_start"]:c["char_end"]])
                        self.assertEqual(c["blocks"], [b for b in mapping["blocks"] if b["char_start"]<c["char_end"] and b["char_end"]>c["char_start"]])
                self.assertEqual(source.read_bytes(),original)
    def test_literal_markdown_spacing_is_preserved_in_source_and_map(self):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw);source=root/"Literais.md"
            original=b"# Titulo\n\n- Item A\n- Item B\n\n```python\nx = 1\ny = 2\n```\n"
            source.write_bytes(original);result=execute(source,root/"out",Options())
            self.assertEqual(result["status"],"complete",result)
            output=Path(result["output"]);md=(output/"document.md").read_text(encoding="utf-8")
            self.assertIn(original.decode().rstrip(),md)
            mapping=json.loads((output/"document-layout.json").read_bytes())
            self.assertTrue(all(r["presentation"]=="literal" for r in mapping["blocks"]))
