"""P6-7: em prosa extraída de PDF/OCR (pymupdf, tesseract) a cerca só pareia dentro do próprio bloco.

Caso real: ruído de OCR iniciado em `~~~` abria uma cerca fechada centenas de páginas adiante e gerava
chunks de 0,6 a 0,7 milhão de caracteres. O Markdown não muda; só a segmentação.
"""
from __future__ import annotations

import time
import unittest

from traslado.chunks import generate_chunks
from traslado.model import Block, Document, Page
from traslado.render import render

PROSA = "Texto corrido de doutrina sobre o tema, com palavras suficientes para encher a linha. " * 4


class ProseFenceTests(unittest.TestCase):
    def doc(self, texts, engine="pymupdf", name="Obra.pdf"):
        return Document(name, "a" * 64, [Page(i, "native", [Block("text", t, engine=engine)])
                                         for i, t in enumerate(texts, 1)])

    def gerar(self, document, size=600, overlap=60):
        md = render(document)
        ch = generate_chunks(document, md, size_chars=size, overlap_chars=overlap)
        for c in ch:
            self.assertEqual(c["text"], md[c["char_start"]:c["char_end"]])
        self.assertEqual(ch[0]["char_start"], 0)
        self.assertEqual(ch[-1]["char_end"], len(md))
        for a, b in zip(ch, ch[1:]):
            self.assertLessEqual(b["char_start"], a["char_end"])
        return md, ch

    def test_ruido_de_ocr_nao_pareia_com_bloco_distante(self):
        textos = ["~~~ ruído\n" + PROSA] + [PROSA] * 30 + ["~~~\n" + PROSA]
        for engine in ("pymupdf", "tesseract"):
            md, ch = self.gerar(self.doc(textos, engine))
            self.assertLessEqual(max(c["char_count"] for c in ch), 600)
            self.assertIn("chunk_unclosed_fence", ch[0]["warnings"])

    def test_cerca_fechada_no_mesmo_bloco_continua_inteira(self):
        codigo = "```\n" + "linha de código\n" * 60 + "```"
        md, ch = self.gerar(self.doc([PROSA, PROSA + "\n" + codigo + "\n" + PROSA, PROSA]))
        a = md.index("```")
        b = md.index("```", a + 3) + 3
        self.assertTrue(any(c["char_start"] <= a and b <= c["char_end"] for c in ch))
        self.assertFalse(any("chunk_unclosed_fence" in c["warnings"] for c in ch))

    def test_fonte_nao_pdf_mantem_pareamento_entre_blocos(self):
        textos = ["```\n" + PROSA] + [PROSA] * 5 + ["```"]
        md, ch = self.gerar(self.doc(textos, engine="native", name="Obra.docx"), size=60000, overlap=60)
        self.assertEqual(len(ch), 1)
        md, ch = self.gerar(self.doc(textos, engine="native", name="Obra.docx"), size=600, overlap=60)
        self.assertTrue(any(c["char_count"] > 600 for c in ch))  # cerca entre blocos permanece atômica

    def test_mesmo_markdown(self):
        textos = ["~~~ ruído\n" + PROSA, PROSA, "~~~"]
        documento = self.doc(textos, "pymupdf")
        antes = render(documento)
        self.assertEqual(antes, render(self.doc(textos, "native")))
        md, ch = self.gerar(documento)
        self.assertEqual(md, antes)
        self.assertEqual(render(documento), antes)

    def test_custo_linear_com_muitas_aberturas(self):
        # Cada abertura em prosa sem fechamento no bloco examina só o próprio bloco: 4x blocos, ~4x tempo
        # (quadrático seria 16x). Mesmo critério de test_chunks_unclosed_p6.
        from traslado import chunks
        from traslado.render import layout

        def medir(blocos):
            d = self.doc(["~~~ x\n" + PROSA for _ in range(blocos)] + ["~~~"])
            md = render(d)
            refs = layout(d).refs
            spans = [(r["char_start"], r["char_end"]) for r in refs]
            tempos = []
            for _ in range(3):
                t = time.perf_counter()
                chunks._protected(md, [], blocks=spans, unclosed=[], confined=spans)
                tempos.append(time.perf_counter() - t)
            ch = generate_chunks(d, md, size_chars=6000, overlap_chars=600)
            self.assertLessEqual(max(c["char_count"] for c in ch), 6000)
            return min(tempos)

        t1, t2 = medir(500), medir(2000)
        self.assertLess(t2, 8 * t1)

if __name__ == "__main__":
    unittest.main()
