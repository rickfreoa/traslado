# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Grava `esperado.json` a partir do código vigente. Só se roda antes da decomposição (E1); depois, o arquivo é
congelado e qualquer mudança nele exige registro no diário."""
from __future__ import annotations

import json
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parents[1] / "src"))
sys.path.insert(0, str(AQUI.parent))

from caracterizacao_e1 import cenarios  # noqa: E402


def main() -> None:
    saida = Path(sys.argv[1]) if len(sys.argv) > 1 else AQUI / "esperado.json"
    dados = cenarios.executar_todos()
    with open(saida, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print(saida)


if __name__ == "__main__":
    main()
