"""P6-3: cerca ou <table> sem fechamento deixa de proteger até o fim do documento; vale até o fim do bloco de origem."""
from __future__ import annotations

import hashlib
import unittest
import unittest.mock

from traslado import chunks
from traslado.chunks import generate_chunks
from traslado.model import Block, Document, Page
from traslado.render import layout, render


class UnclosedTests(unittest.TestCase):
    def doc(self, *texts):
        return Document("Obra.pdf", "a" * 64, [Page(i, "ocr", [Block("text", t)]) for i, t in enumerate(texts, 1)])

    def gerar(self, document, size=600, overlap=60):
        md = render(document)
        return md, generate_chunks(document, md, size_chars=size, overlap_chars=overlap)

    def cobertura(self, ch, md):
        coberto = bytearray(len(md))
        for c in ch:
            self.assertEqual(c["text"], md[c["char_start"]:c["char_end"]])
            coberto[c["char_start"]:c["char_end"]] = b"\1" * c["char_count"]
        self.assertTrue(all(coberto))
        self.assertEqual(ch[0]["markdown_sha256"], hashlib.sha256(md.encode()).hexdigest())

    def blocos(self, document):
        return [(r["char_start"], r["char_end"]) for r in layout(document).refs]

    def check_nao_fechado(self, aberto, tipo, size=600):
        d = self.doc("Antes " * 40 + "\n\n" + aberto, "Depois " * 3000)
        md, ch = self.gerar(d, size)
        self.cobertura(ch, md)  # o Markdown e os offsets não mudam
        fim0 = self.blocos(d)[0][1]
        # antes: um átomo até o fim do documento; depois: no máximo até o fim do bloco que abriu
        self.assertIn(len(md), [b for _, b in chunks._protected(md, [])])
        u = []
        atomos = chunks._protected(md, [], blocks=self.blocos(d), unclosed=u)
        self.assertEqual([x["kind"] for x in u], [tipo])
        self.assertLessEqual(u[0]["end"], fim0)
        self.assertNotIn(len(md), [b for _, b in atomos])
        self.assertLessEqual(max(c["char_count"] for c in ch), size)
        avisados = [c for c in ch if "chunk_unclosed_" + tipo in c["warnings"]]
        self.assertTrue(avisados)
        self.assertTrue(all(c["char_start"] < fim0 for c in avisados))
        self.assertFalse(any("chunk_unclosed_" + tipo in c["warnings"] for c in ch if c["char_start"] >= fim0))

    def test_cerca_sem_fechamento_para_no_bloco(self):
        self.check_nao_fechado("```text\n" + "linha sem fechar\n" * 15, "fence")

    def test_tabela_html_sem_fechamento_para_no_bloco(self):
        self.check_nao_fechado("<table><tr><td>célula</td></tr>\n" + "linha da tabela\n" * 15, "table")

    def test_cerca_fechada_gigante_continua_atomica(self):
        d = self.doc("Antes " * 40 + "\n\n```\n" + "x\n" * 3000 + "```\n", "Depois " * 500)
        md, ch = self.gerar(d)
        self.cobertura(ch, md)
        gigantes = [c for c in ch if c["char_count"] > 600]
        self.assertEqual(len(gigantes), 1)
        self.assertGreaterEqual(gigantes[0]["char_count"], 6000)
        self.assertIn("chunk_atomic_exceeds_target_chars", gigantes[0]["warnings"])
        self.assertFalse(any(w.startswith("chunk_unclosed_") for c in ch for w in c["warnings"]))

    def test_fixtures_da_sonda_com_blocos(self):
        for nome, aberto in (("cerca", "```text\n"), ("tabela", "<table><tr><td>x</td></tr>\n")):
            d = self.doc("# t\n\n" + aberto + "conteudo normal\n" * 200, "conteudo normal\n" * 10000)
            md, ch = self.gerar(d, 6000, 600)
            self.cobertura(ch, md)
            self.assertLessEqual(max(c["char_count"] for c in ch), 6000, nome)

    def test_documento_de_bloco_unico_mantem_o_comportamento_anterior(self):
        # sem fronteira provável (o bloco vai até o fim) a proteção continua até o fim, mas o aviso aparece
        d = self.doc("# t\n\n```text\n" + "conteudo normal\n" * 2000)
        md, ch = self.gerar(d, 6000, 600)
        self.assertGreater(ch[0]["char_count"], 20000)  # a cauda do bloco único continua um átomo
        self.assertIn("chunk_unclosed_fence", ch[0]["warnings"])

    def test_sem_construcao_aberta_o_resultado_nao_muda(self):
        d = self.doc("Texto " * 100 + "\n\n```\ncodigo\n```\n\n<table><tr><td>x</td></tr></table>\n\n" + "fim " * 400, "Mais " * 800)
        md = render(d)
        u = []
        com = chunks._protected(md, [], blocks=self.blocos(d), unclosed=u)
        self.assertEqual(com, chunks._protected(md, []))
        self.assertEqual(u, [])

    def test_fechamento_em_bloco_posterior_continua_valendo(self):
        # a fronteira prova a origem, não a ausência de fechamento: cerca e tabela que fecham adiante seguem pareadas
        for abre, fecha in (("```text\n" + "x\n" * 30, "```\n"), ("<table><tr><td>x</td></tr>\n" + "y\n" * 30, "</table>\n")):
            d = self.doc(abre, fecha + "tail " * 3000)
            md = render(d)
            u = []
            self.assertEqual(chunks._protected(md, [], blocks=self.blocos(d), unclosed=u), chunks._protected(md, []))
            self.assertEqual(u, [])
            _, ch = self.gerar(d)
            self.assertFalse(any(w.startswith("chunk_unclosed_") for c in ch for w in c["warnings"]))

    def test_duas_construcoes_abertas_em_blocos_diferentes(self):
        d = self.doc("a " * 50 + "\n\n<table><tr><td>x</td></tr>\n" + "t\n" * 20, "meio " * 200, "```text\n" + "c\n" * 20, "fim " * 2000)
        md, ch = self.gerar(d)
        self.cobertura(ch, md)
        u = []
        chunks._protected(md, [], blocks=self.blocos(d), unclosed=u)
        self.assertEqual([x["kind"] for x in u], ["table", "fence"])
        self.assertLessEqual(max(c["char_count"] for c in ch), 600)

    def test_depois_do_corte_a_varredura_recomeca(self):
        tabela = "| A | B |\n| --- | --- |\n" + "| um | dois |\n" * 70
        d = self.doc("```text\n" + "c\n" * 20, "Antes " * 30 + "\n\n" + tabela + "\nDepois " * 200)
        md, ch = self.gerar(d)
        self.cobertura(ch, md)
        com_tabela = [c for c in ch if "| um | dois |" in c["text"]]
        self.assertEqual(len(com_tabela), 1)  # a tabela nativa depois do corte segue inteira
        self.assertIn(tabela.rstrip("\n"), com_tabela[0]["text"])

    def test_cerca_fechada_em_bloco_posterior_nao_vira_abertura(self):
        # auditoria: a tabela fica aberta, mas a cerca dentro dela fecha no bloco seguinte e conserva o pareamento
        d = self.doc("<table>\n```text\nx\n", "y\n```\n\n" + "tail " * 4000)
        md, ch = self.gerar(d)
        self.cobertura(ch, md)
        u = []
        chunks._protected(md, [], blocks=self.blocos(d), unclosed=u)
        self.assertEqual([x["kind"] for x in u], ["table"])
        self.assertLessEqual(max(c["char_count"] for c in ch), 600)
        self.assertFalse(any("chunk_unclosed_fence" in c["warnings"] for c in ch))
        self.assertTrue(any("chunk_unclosed_table" in c["warnings"] for c in ch))

    def test_duas_abertas_no_mesmo_bloco_sao_registradas(self):
        d = self.doc("<table>\n```text\nx\n", "so prosa " * 2000)
        md, ch = self.gerar(d)
        self.cobertura(ch, md)
        u = []
        chunks._protected(md, [], blocks=self.blocos(d), unclosed=u)
        self.assertEqual(sorted(x["kind"] for x in u), ["fence", "table"])
        for tipo in ("fence", "table"):
            self.assertTrue(any("chunk_unclosed_" + tipo in c["warnings"] for c in ch), tipo)
        self.assertLessEqual(max(c["char_count"] for c in ch), 600)

    def test_fechamento_so_dentro_de_cerca_nao_conta_como_fechamento(self):
        # auditoria: o unico </table> esta dentro de cerca; a tabela e capada e a seguinte nao fica sem registro
        d = self.doc("<table>\n```text\n</table>\n```\n", "<table>\n" + "cell " * 800, "tail " * 1000)
        md, ch = self.gerar(d)
        self.cobertura(ch, md)
        u = []
        chunks._protected(md, [], blocks=self.blocos(d), unclosed=u)
        self.assertEqual([x["kind"] for x in u], ["table", "table"])
        fim1 = self.blocos(d)[1][1]
        self.assertTrue(all(x["end"] <= fim1 for x in u))
        self.assertLessEqual(max(c["char_count"] for c in ch if c["char_start"] >= fim1), 600)  # a cauda volta a ser segmentada

    def test_custo_linear_na_varredura_e_nos_avisos(self):
        # auditoria: a varredura revisitava a cauda a cada abertura e os avisos eram O(chunks x aberturas).
        # Conta leituras dos registros `unclosed` (deterministico) e o tempo de `_protected`.
        import time
        leituras = [0]

        class Registro(dict):
            def __getitem__(self, chave):
                leituras[0] += 1
                return super().__getitem__(chave)

        original = chunks._protected

        def contando(markdown, atomic, **kw):
            res = original(markdown, atomic, **kw)
            if kw.get("unclosed") is not None:
                kw["unclosed"][:] = [Registro(x) for x in kw["unclosed"]]
            return res

        def medir(blocos):
            d = self.doc(*["<table>\n" + "x " * 150] * blocos)
            md = render(d)
            leituras[0] = 0
            with unittest.mock.patch.object(chunks, "_protected", contando):
                ch = generate_chunks(d, md, size_chars=256, overlap_chars=0)
            self.assertGreaterEqual(sum("chunk_unclosed_table" in c["warnings"] for c in ch), blocos)
            blocos_ref = [(r["char_start"], r["char_end"]) for r in layout(d).refs]
            tempos = []
            for _ in range(3):
                t = time.perf_counter()
                chunks._protected(md, [], blocks=blocos_ref, unclosed=[])
                tempos.append(time.perf_counter() - t)
            return leituras[0], min(tempos)

        (l1, t1), (l2, t2) = medir(200), medir(800)
        self.assertLessEqual(l2, 5 * l1)  # linear: 4x; era 16x
        self.assertLess(t2, 8 * t1)


if __name__ == "__main__":
    unittest.main()
