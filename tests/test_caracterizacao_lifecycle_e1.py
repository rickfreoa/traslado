# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""E1 da 3.0.0: caracterização de `traslado.lifecycle`.

`tests/caracterizacao_e1/esperado.json` foi gravado pelo código anterior à decomposição. Cada cenário tem de
reproduzi-lo; a superfície da fachada só pode crescer (nenhum nome anterior some)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

try:
    from tests.caracterizacao_e1 import cenarios
except ImportError:  # execução a partir de tests/
    from caracterizacao_e1 import cenarios

ESPERADO = json.loads((Path(__file__).resolve().parent / "caracterizacao_e1" / "esperado.json").read_text(encoding="utf-8"))


class CaracterizacaoLifecycleE1(unittest.TestCase):
    maxDiff = None

    def test_cenarios_cobertos(self):
        self.assertEqual(sorted(cenarios.CENARIOS), sorted(ESPERADO))

    def test_superficie_nao_encolhe(self):
        atual = set(cenarios.executar("h_superficie")["nomes"])
        self.assertEqual(sorted(set(ESPERADO["h_superficie"]["nomes"]) - atual), [])


def _gerar(nome):
    def teste(self):
        obtido = json.loads(json.dumps(cenarios.executar(nome), ensure_ascii=False))
        self.assertEqual(obtido, ESPERADO[nome])
    return teste


for _nome in cenarios.CENARIOS:
    if _nome != "h_superficie":
        setattr(CaracterizacaoLifecycleE1, "test_" + _nome, _gerar(_nome))


if __name__ == "__main__":
    unittest.main()
