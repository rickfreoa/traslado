# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Fixtures sintéticas públicas para a demonstração de derivabilidade de `--schema 1` (E0): tabela com régua,
imagem embutida, hífen discricionário (U+00AD) e quebras de linha em PDF; DOCX com tabela e imagem; XLSX com
células numéricas e vazias; PPTX com tabela e texto. Uso: gerar_fixtures_derivacao.py DESTINO. Texto inventado.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path


def png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (120, 80), (40, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


def pdf(destino: Path) -> None:
    import fitz
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    linhas = ["A obrigação de indenizar decorre do inadimplemento contratual, e a res-",
              "ponsabilidade civil se apura segundo os critérios da extensão do dano e da",
              "culpa, quando exigida, sem prejuízo das cláusulas de limitação pactuadas.",
              "",
              "O termo inter­nacionalização aparece com hífen discricionário na quebra:",
              "inter­",
              "nacionalização dos contratos de adesão em mercados regulados e abertos."]
    y = 80
    for linha in linhas:
        p.insert_text((72, y), linha, fontsize=10)
        y += 14
    x0, y0, larg, alt = 72, 220, 300, 18
    tabela = [["Cláusula", "Prazo", "Multa"], ["Entrega", "30 dias", "2%"], ["Garantia", "", "10%"]]
    for i in range(len(tabela) + 1):
        p.draw_line((x0, y0 + i * alt), (x0 + larg, y0 + i * alt))
    for j in range(4):
        p.draw_line((x0 + j * 100, y0), (x0 + j * 100, y0 + len(tabela) * alt))
    for i, linha in enumerate(tabela):
        for j, cel in enumerate(linha):
            if cel:
                p.insert_text((x0 + j * 100 + 4, y0 + i * alt + 13), cel, fontsize=9)
    p.insert_image(fitz.Rect(72, 320, 192, 400), stream=png())
    p.insert_text((72, 430), "Texto depois da imagem, ainda no corpo da página, com vírgula, ponto e vírgula; dois-pontos:", fontsize=10)
    p.insert_text((290, 820), "12", fontsize=9)
    doc.save(destino / "deriv-pdf-tabela-imagem.pdf", garbage=4, deflate=True)


def docx(destino: Path) -> None:
    import docx as d
    doc = d.Document()
    doc.add_heading("Capítulo de teste", level=1)
    doc.add_paragraph("Parágrafo com texto inventado para o teste de derivabilidade.")
    t = doc.add_table(rows=2, cols=2)
    for i, linha in enumerate([["A", "1"], ["B", ""]]):
        for j, cel in enumerate(linha):
            t.cell(i, j).text = cel
    doc.add_picture(io.BytesIO(png()))
    doc.add_paragraph("Fim.")
    doc.save(destino / "deriv-docx-tabela-imagem.docx")


def xlsx(destino: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Planilha"
    for linha in [["Item", "Valor", "Nota"], ["a", 10, None], ["b", 2.5, "x"], [None, None, "só nota"]]:
        ws.append(linha)
    wb.save(destino / "deriv-xlsx-celulas.xlsx")


def pptx(destino: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches
    pr = Presentation()
    s = pr.slides.add_slide(pr.slide_layouts[5])
    s.shapes.title.text = "Slide de teste"
    tb = s.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    for i, linha in enumerate([["X", "Y"], ["1", ""]]):
        for j, cel in enumerate(linha):
            tb.cell(i, j).text = cel
    pr.save(destino / "deriv-pptx-tabela.pptx")


def main(argv: list[str]) -> int:
    destino = Path(argv[0])
    destino.mkdir(parents=True, exist_ok=True)
    for f in (pdf, docx, xlsx, pptx):
        f(destino)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
