# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Decide se o release pode seguir, a partir das execuções de ci.yml de um mesmo commit.

Uso: gh api --paginate "repos/<repo>/actions/workflows/ci.yml/runs?head_sha=<sha>&per_page=100" | python scripts/ci_gate.py
Lê todas as páginas (objetos JSON concatenados) da entrada padrão e imprime uma palavra: aguardar, liberar ou recusar.
O laço de espera (60 min, passo de 30 s) e o prazo ficam no workflow; este script só decide.
"""
from __future__ import annotations

import json
import sys

ABERTOS = {"queued", "in_progress", "waiting", "requested", "pending"}


def execucoes(texto: str) -> list[dict]:
    """Aceita um objeto, uma lista ou vários objetos concatenados (gh api --paginate)."""
    decodificador, i, runs = json.JSONDecoder(), 0, []
    while True:
        while i < len(texto) and texto[i].isspace():
            i += 1
        if i >= len(texto):
            return runs
        pagina, i = decodificador.raw_decode(texto, i)
        runs += pagina.get("workflow_runs", []) if isinstance(pagina, dict) else pagina


def decidir(runs: list[dict]) -> str:
    if not runs or any(r.get("status") in ABERTOS for r in runs):
        return "aguardar"
    # Só libera sobre dados completos: estado desconhecido, sem data ou sem id não podem esconder uma falha.
    if any(r.get("status") != "completed" or not (r.get("run_started_at") or r.get("created_at"))
           or not isinstance(r.get("id"), int) for r in runs):
        return "recusar"
    # run_started_at muda quando um run antigo é reexecutado; id desempata.
    ultimo = max(runs, key=lambda r: (r.get("run_started_at") or r["created_at"], r["id"]))
    return "liberar" if ultimo.get("conclusion") == "success" else "recusar"


if __name__ == "__main__":
    print(decidir(execucoes(sys.stdin.read())))
