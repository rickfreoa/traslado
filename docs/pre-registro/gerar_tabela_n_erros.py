# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Gera a tabela normativa "n x erros admitidos" do pre-registro da 3.0.0 (sec. 5 do plano)."""
import json
import math
import sys
from pathlib import Path

Z = 1.959963984540054  # quantil bicaudal de 95%
LINHAS = {
    "pagina_impressa_confirmada": {"ponto": 0.99, "wilson": 0.97, "minimo": 300},
    "ligacao_chamada_nota": {"ponto": 0.98, "wilson": 0.95, "minimo": 200},
    "mobiliario_omitido": {"ponto": 0.99, "wilson": 0.97, "minimo": 500},
}
N_ADICIONAIS = (250, 300, 400, 500, 750, 1000, 1500, 2000, 3000, 5000)


def wilson_inferior(acertos: int, n: int) -> float:
    p = acertos / n
    denominador = 1 + Z * Z / n
    centro = p + Z * Z / (2 * n)
    margem = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return (centro - margem) / denominador


def erros_admitidos(n: int, ponto: float, wilson: float) -> int:
    admitidos = -1
    for erros in range(n + 1):
        acertos = n - erros
        if acertos / n >= ponto and wilson_inferior(acertos, n) >= wilson:
            admitidos = erros
        else:
            break
    return admitidos


def tabela() -> dict:
    saida = {"z": Z, "metodo": "maior e tal que (n-e)/n >= ponto e Wilson95_inferior(n-e, n) >= wilson; -1 = inatingivel em n"}
    for nome, linha in LINHAS.items():
        ns = sorted({linha["minimo"], *(n for n in N_ADICIONAIS if n >= linha["minimo"])})
        saida[nome] = {**linha, "erros_admitidos": {str(n): erros_admitidos(n, linha["ponto"], linha["wilson"]) for n in ns}}
    return saida


if __name__ == "__main__":
    texto = json.dumps(tabela(), indent=1, ensure_ascii=False) + "\n"
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_bytes(texto.encode("utf-8"))
    print(texto, end="")
