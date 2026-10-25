"""Readable Markdown is a presentation of raw extraction, with exact RAG spans."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from freoa_v6.chunks import generate_chunks, write_chunks
from freoa_v6.advanced import sanitize
from freoa_v6.model import Block, Document, Options, Page
from freoa_v6.render import layout, render, table_markdown


class ReadableMarkdownRC6(unittest.TestCase):
    def document(self, text: str, *, engine="tesseract", name="Livro.pdf"):
        return Document(name, "a" * 64, [Page(1, "ocr", [Block("text", text, engine=engine)])])

    def test_commonmark_reading_has_no_generated_metadata_between_paragraphs(self):
        from markdown_it import MarkdownIt
        document = Document("Livro.pdf", "b" * 64, [
            Page(1, "native", [Block("text", "Primeiro parágrafo.", engine="pymupdf")],
                 warnings=["ocr_quality_suspect"]),
            Page(2, "ocr", [Block("text", "Segundo parágrafo.", engine="tesseract")],
                 warnings=["ocr_low_confidence"])], warnings=["document_warning"], status="partial")
        markdown = render(document)
        html = MarkdownIt("commonmark", {"html": True}).render(markdown)
        self.assertNotIn("<!--", markdown)
        self.assertNotIn("ocr_low_confidence", html)
        self.assertNotIn("ocr_quality_suspect", html)
        self.assertNotIn("document_warning", html)
        self.assertEqual(markdown.count("LEIA-AVISOS.txt"), 1)
        self.assertIn("<p>Primeiro parágrafo.</p>\n<p>Segundo parágrafo.</p>", html)
        chunks = generate_chunks(document, markdown)
        self.assertEqual(chunks[0]["pages"], [1, 2])
        self.assertEqual(set(chunks[0]["warnings"]),
                         {"ocr_quality_suspect", "ocr_low_confidence", "document_warning"})

    def test_long_wrapped_prose_joins_but_raw_document_is_unchanged(self):
        first = "Este parágrafo contém uma frase extensa, extraída de uma página digitalizada com linhas físicas"
        second = "que continuam no mesmo período do texto original."
        document = self.document(first + "\n" + second + "\n\nNovo parágrafo.")
        before = json.dumps(document.to_dict(), sort_keys=True, ensure_ascii=False)
        effective = layout(document)
        self.assertIn(first + " " + second + "\n\nNovo parágrafo.", effective.markdown)
        self.assertEqual(effective.refs[0]["presentation"], "conservative_pdf_ocr_line_join")
        self.assertEqual(json.dumps(document.to_dict(), sort_keys=True, ensure_ascii=False), before)

    def test_soft_hyphen_wrap_is_explicit_but_ordinary_hyphens_are_preserved(self):
        document = self.document("A responsabili\u00ad\ndade civil permanece.\n\nA cláusula de boa-\nfé permanece.")
        markdown = render(document)
        self.assertIn("A responsabilidade civil permanece.", markdown)
        self.assertIn("boa-\nfé", markdown)
        self.assertNotIn("boa-fé", markdown)

    def test_native_geometry_join_retains_two_distinct_source_spans(self):
        first = "A descrição das obrigações contidas neste documento tem uma redação longa, suficiente para constituir um parágrafo inequívoco"
        second = "que prossegue no bloco seguinte da mesma coluna e da mesma página."
        document = Document("Fonte.pdf", "f" * 64, [Page(1, "native", [
            Block("text", first, [40, 80, 400, 130], engine="pymupdf"),
            Block("text", second, [41, 134, 398, 160], engine="pymupdf")])])
        effective = layout(document)
        self.assertIn(first + " " + second, effective.markdown)
        self.assertEqual([ref["block"] for ref in effective.refs], [1, 2])
        for ref, expected in zip(effective.refs, (first, second)):
            self.assertEqual(effective.markdown[ref["char_start"]:ref["char_end"]], expected)
        self.assertIn("native_geometry_continuation", effective.refs[1]["presentation"])

    def test_native_geometry_join_rejects_columns_gaps_hyphens_and_page_guessing(self):
        first = "A descrição das obrigações contidas neste documento tem uma redação longa, suficiente para constituir um parágrafo inequívoco"
        second = "que prossegue em outro trecho sem evidência suficiente para eliminar o limite."
        for box, ending, page in (([440, 134, 798, 160], "", 1),
                                  ([41, 180, 398, 220], "", 1),
                                  ([41, 134, 398, 160], "-", 1),
                                  ([41, 134, 398, 160], ".", 1),
                                  ([41, 134, 398, 160], "", 2)):
            with self.subTest(box=box, ending=ending, page=page):
                left = Block("text", first + ending, [40, 80, 400, 130], engine="pymupdf")
                right = Block("text", second, box, engine="pymupdf")
                pages = [Page(1, "native", [left, right])] if page == 1 else [Page(1, "native", [left]), Page(2, "native", [right])]
                self.assertIn(first + ending + "\n\n" + second,
                              render(Document("Fonte.pdf", "f" * 64, pages)))

    def test_comma_and_long_uppercase_continuations_are_readable(self):
        first = "Uma linha física suficientemente longa possui palavras e referências,"
        following = "Segundo o autor mencionado no texto, há uma continuação longa que pertence ao período."
        markdown = render(self.document(first + "\n" + following))
        self.assertIn(first + " " + following, markdown)

    def test_short_poetry_and_page_boundaries_are_not_guessed(self):
        poem = "no silêncio da noite\nsob o céu sem estrelas\na palavra permanece"
        document = Document("Livro.pdf", "c" * 64, [
            Page(1, "ocr", [Block("text", poem, engine="tesseract")]),
            Page(2, "ocr", [Block("text", "continuação ambígua da página", engine="tesseract")])])
        self.assertIn(poem + "\n\ncontinuação ambígua", render(document))
        self.assertTrue(all(ref["presentation"] == "literal" for ref in layout(document).refs))

    def test_structured_or_ambiguous_paragraphs_are_preserved(self):
        long = "Uma linha bastante extensa com muitas palavras que não deve perder a estrutura do documento original"
        for structure in ("- primeiro item", "1. primeiro item", "## Um título", "> uma citação",
                          "```text", "| A | B |", "<table>", "    código indentado",
                          "Nome  Valor", "quebra explícita  "):
            raw = long + "\n" + structure + "\ncontinuação com palavras suficientes"
            with self.subTest(structure=structure):
                self.assertIn(raw, render(self.document(raw)))

    def test_alphabetical_and_roman_list_items_do_not_collapse(self):
        text = "Uma descrição suficientemente extensa com muitas palavras para reproduzir o falso positivo de refluxo"
        for first, second in (("a)", "b)"), ("a.", "b."), ("i)", "ii)"),
                              ("I)", "II)"), ("iv.", "v."), ("A)", "B)")):
            raw = first + " " + text + "\n" + second + " " + text
            with self.subTest(first=first, second=second):
                effective = layout(self.document(raw))
                self.assertIn(raw, effective.markdown)
                self.assertEqual(effective.refs[0]["presentation"], "literal")

    def test_inline_code_anywhere_preserves_literal_soft_hyphen_and_wraps(self):
        prefix = "Uma descrição suficientemente extensa com palavras comuns antes do código literal "
        for code in ("`com\u00ad\nando`", "`https://host.example/\ncaminho`",
                     "``literal ` com\u00ad\nando``"):
            raw = prefix + code + " seguido por texto comum que não permite alterar o código."
            document = self.document(raw)
            before = json.dumps(document.to_dict(), sort_keys=True, ensure_ascii=False)
            with self.subTest(code=code):
                effective = layout(document)
                self.assertIn(raw, effective.markdown)
                self.assertEqual(effective.refs[0]["presentation"], "literal")
                self.assertEqual(json.dumps(document.to_dict(), sort_keys=True, ensure_ascii=False), before)

    def test_inline_code_prevents_native_geometry_join(self):
        first = "A descrição das obrigações contidas neste documento tem uma redação longa, suficiente para constituir um parágrafo com `literal` e texto subsequente"
        second = "que prossegue no bloco seguinte da mesma coluna e da mesma página."
        document = Document("Fonte.pdf", "f" * 64, [Page(1, "native", [
            Block("text", first, [40, 80, 400, 130], engine="pymupdf"),
            Block("text", second, [41, 134, 398, 160], engine="pymupdf")])])
        effective = layout(document)
        self.assertIn(first + "\n\n" + second, effective.markdown)
        self.assertNotIn("native_geometry_continuation", effective.refs[1]["presentation"])

    def test_long_uppercase_heading_stays_literal_before_prose(self):
        heading = "DISPOSIÇÕES GERAIS SOBRE AS OBRIGAÇÕES CONTRATUAIS E OS DIREITOS DAS PARTES RELACIONADOS COM A EXECUÇÃO DO DOCUMENTO ORIGINAL"
        prose = "a descrição seguinte contém palavras suficientes para reproduzir o falso positivo de refluxo automático."
        self.assertIn(heading + "\n" + prose, render(self.document(heading + "\n" + prose)))
        document = Document("Fonte.pdf", "f" * 64, [Page(1, "native", [
            Block("text", heading, [40, 80, 400, 130], engine="pymupdf"),
            Block("text", prose, [41, 134, 398, 160], engine="pymupdf")])])
        markdown = render(document)
        self.assertIn(heading + "\n\n" + prose, markdown)
        self.assertNotIn("# " + heading, markdown)

    def test_non_pdf_text_and_advanced_markdown_are_never_reflowed(self):
        raw = "Uma linha muito extensa com várias palavras e tamanho suficiente para parecer uma quebra de prosa\ncontinua aqui com bastante texto\nsoft\u00ad\nhyphen"
        for name, engine in (("Original.md", "native"), ("Original.txt", "native"),
                             ("Original.docx", "python-docx"), ("Original.pdf", "mineru-kit-4.0.10"),
                             ("Original.txt", "pymupdf")):
            with self.subTest(name=name, engine=engine):
                effective = layout(self.document(raw, name=name, engine=engine))
                self.assertIn(raw, effective.markdown)
                self.assertEqual(effective.refs[0]["presentation"], "literal")

    def test_exact_unicode_spans_and_chunks_after_presentation_change(self):
        raw = ("Ação😀 e obrigações contratuais possuem uma descrição extensa nesta linha física da página\n"
               "que continua com palavras no mesmo período, sem alterar o conteúdo substantivo.\n\n") * 30
        document = self.document(raw)
        effective = layout(document)
        markdown = effective.markdown
        ref = effective.refs[0]
        expected = raw.replace("página\nque", "página que").rstrip()
        self.assertEqual(markdown[ref["char_start"]:ref["char_end"]], expected)
        chunks = generate_chunks(document, markdown, size_chars=300, overlap_chars=50)
        covered = bytearray(len(markdown))
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        for chunk in chunks:
            start, end = chunk["char_start"], chunk["char_end"]
            self.assertEqual(chunk["text"], markdown[start:end])
            self.assertEqual(chunk["markdown_sha256"], digest)
            covered[start:end] = b"\1" * (end - start)
            for block in chunk["blocks"]:
                self.assertEqual(block["page"], 1)
                self.assertEqual(block["engine"], "tesseract")
                self.assertEqual(block["char_start"], ref["char_start"])
                self.assertEqual(block["char_end"], ref["char_end"])
        self.assertTrue(all(covered))
        with self.assertRaises(ValueError):
            generate_chunks(document, markdown.replace("página que", "página\nque", 1))

    def test_identical_and_marker_looking_blocks_keep_their_actual_provenance(self):
        poison = "<!-- page:999 route:fake -->\n<!-- block:999.1 engine:fake -->\nTEXTO_SENTINELA"
        document = Document("Fonte.md", "d" * 64, [
            Page(1, "native", [Block("text", poison)]),
            Page(2, "native", [Block("text", poison)])])
        effective = layout(document)
        self.assertEqual([r["page"] for r in effective.refs], [1, 2])
        self.assertLess(effective.refs[0]["char_end"], effective.refs[1]["char_start"])
        for ref in effective.refs:
            self.assertEqual(effective.markdown[ref["char_start"]:ref["char_end"]], poison)
        chunks = generate_chunks(document, effective.markdown, size_chars=256, overlap_chars=30)
        self.assertEqual(sorted({p for c in chunks for p in c["pages"]}), [1, 2])

    def test_whole_tables_fences_and_image_derivatives_survive_clean_layout(self):
        fence = "```text\n<!-- page:999 route:fake -->\n![literal](assets/literal.png)\n" + "literal " * 70 + "\n```"
        table = Block("table", rows=[["célula " * 70, "valor " * 70]])
        document = Document("Fonte.md", "e" * 64, [Page(1, "native", [
            Block("text", "Introdução " * 40), table, Block("text", fence),
            Block("image", asset="assets/real.png")])])
        markdown = render(document)
        chunks = generate_chunks(document, markdown, size_chars=300, overlap_chars=50)
        self.assertEqual(sum(table_markdown(table.rows) in c["text"] for c in chunks), 1)
        self.assertEqual(sum(fence in c["text"] for c in chunks), 1)
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "chunks"
            write_chunks(document, markdown, directory, size_chars=300, overlap_chars=50)
            bodies = "\n".join(p.read_text(encoding="utf-8") for p in directory.glob("*.md"))
            self.assertIn("![literal](assets/literal.png)", bodies)
            self.assertNotIn("![literal](../assets/literal.png)", bodies)
            self.assertIn("![Imagem da página 1](../assets/real.png)", bodies)

    def test_generated_review_link_rebases_but_literal_link_and_code_do_not(self):
        literal = "Fonte [avisos](LEIA-AVISOS.txt) e `LEIA-AVISOS.txt`."
        document = self.document(literal, engine="native", name="Fonte.md")
        document.status = "partial"
        document.pages[0].blocks.append(Block("image", asset="assets/real.png"))
        markdown = render(document)
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "chunks"
            write_chunks(document, markdown, directory)
            body = (directory / "0001.md").read_text(encoding="utf-8")
            jsonl = json.loads((directory / "chunks.jsonl").read_text(encoding="utf-8"))
            self.assertIn("[os avisos de conversão](../LEIA-AVISOS.txt)", body)
            self.assertIn(literal, body)
            self.assertIn("![Imagem da página 1](../assets/real.png)", body)
            self.assertEqual(jsonl["text"], markdown)
            self.assertEqual(jsonl["markdown_sha256"], hashlib.sha256(markdown.encode("utf-8")).hexdigest())

    def test_long_advanced_block_rebases_exported_image_without_touching_literal_code(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            model, package = root / "model", root / "package"
            model.mkdir(); package.mkdir(); (package / "assets").mkdir()
            Image.new("RGB", (20, 20), "red").save(model / "diagram.png")
            source = ("BEFORE_ADVANCED_SENTINEL " * 90 + "\n\n![diagram](diagram.png)\n\n"
                      + "AFTER_ADVANCED_SENTINEL " * 90
                      + "\n\n```text\n![literal-fence](diagram.png)\n```"
                      + "\n\n`![literal-inline](diagram.png)`")
            passive = sanitize(source, model, package, 1, Options())
            literal_native = "![Imagem reconstruída](assets/advanced-1-1.png)"
            document = Document("Fonte.pdf", "e" * 64, [Page(1, "advanced", [
                Block("text", passive, engine="mineru-kit-4.0.10"),
                Block("text", literal_native, engine="native")])])
            markdown = render(document)
            self.assertGreater(len(passive), 4000)
            write_chunks(document, markdown, package / "chunks", size_chars=256, overlap_chars=30)
            entries = [json.loads(line) for line in (package / "chunks/chunks.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertGreater(len(entries), 10)
            digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
            covered = bytearray(len(markdown))
            advanced_ref = layout(document).refs[0]
            image_start = markdown.index(literal_native)
            image_end = image_start + len(literal_native)
            for chunk in entries:
                start, end = chunk["char_start"], chunk["char_end"]
                self.assertEqual(chunk["text"], markdown[start:end])
                self.assertEqual(chunk["markdown_sha256"], digest)
                covered[start:end] = b"\1" * (end - start)
                if start < image_end and end > image_start:
                    self.assertLessEqual(start, image_start)
                    self.assertGreaterEqual(end, image_end)
                    self.assertTrue(start > advanced_ref["char_start"] or end < advanced_ref["char_end"])
                    body = (package / "chunks" / f"{chunk['index']:04d}.md").read_text(encoding="utf-8")
                    self.assertIn("![Imagem reconstruída](../assets/advanced-1-1.png)", body)
            self.assertTrue(all(covered))
            bodies = "\n".join(p.read_text(encoding="utf-8") for p in (package / "chunks").glob("*.md"))
            self.assertIn("```text\n![Imagem reconstruída](assets/advanced-1-2.png)\n```", bodies)
            self.assertIn("`![Imagem reconstruída](assets/advanced-1-3.png)`", bodies)
            self.assertNotIn("(../assets/advanced-1-2.png)", bodies)
            self.assertNotIn("(../assets/advanced-1-3.png)", bodies)
            # An identical-looking link outside the advanced source span remains
            # literal, while the actual export resolves relative to chunks/.
            self.assertIn(literal_native, bodies)
            self.assertTrue((package / "chunks/../assets/advanced-1-1.png").resolve().is_file())


if __name__ == "__main__":
    unittest.main()
