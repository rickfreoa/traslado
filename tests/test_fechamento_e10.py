# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""Fechamento pré-release: dica do Tesseract por distribuição e classificação tipada de erros do zipfile."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import io
import zipfile

from traslado import cli, lifecycle
from traslado.errors import classify
from traslado import platform as plat
import json
from unittest.mock import patch


class DicaTesseract(unittest.TestCase):
    def dica(self, conteudo: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "os-release"
            p.write_text(conteudo, encoding="utf-8")
            return lifecycle.linux_tesseract_hint(str(p))

    def test_comando_da_distribuicao_e_nunca_o_da_outra(self) -> None:
        casos = {
            'ID=debian\nVERSION_ID="12"\n': "sudo apt install tesseract-ocr",
            'ID=ubuntu\nID_LIKE=debian\n': "sudo apt install tesseract-ocr",
            'ID=fedora\n': "sudo dnf install tesseract",
            'ID_LIKE="rhel centos fedora"\nID="rocky"\n': "sudo dnf install tesseract",
            'ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n': "sudo zypper install tesseract-ocr",
            'ID=arch\n': "sudo pacman -S tesseract",
            'ID=alpine\n': "sudo apk add tesseract-ocr",
        }
        for conteudo, comando in casos.items():
            texto = self.dica(conteudo)
            self.assertIn(comando, texto, conteudo)
            outros = {c for c in casos.values() if c != comando}
            self.assertFalse(any(o in texto for o in outros), (conteudo, texto))

    def test_id_decide_antes_de_id_like(self) -> None:
        self.assertIn("apt", self.dica('ID_LIKE=fedora\nID=debian\n'))

    def test_desconhecida_ou_ausente_sem_comando_inventado(self) -> None:
        for texto in (self.dica("ID=nixos\n"), lifecycle.linux_tesseract_hint("/nao/existe/os-release")):
            self.assertNotIn("sudo", texto)
            self.assertIn("gerenciador de pacotes", texto)

    def test_cli_usa_a_mesma_regra(self) -> None:
        self.assertIs(cli.linux_tesseract_hint, lifecycle.linux_tesseract_hint)


class ZipTipado(unittest.TestCase):
    def test_zipfile_nao_suportado_e_criptografado_nao_sao_internal(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.xml", "<a/>")
        dados = bytearray(buf.getvalue())
        central = bytes(dados).index(b"PK\x01\x02")
        for pos in (8, central + 10):  # método de compressão inexistente: cabeçalho local e diretório central
            dados[pos:pos + 2] = (99).to_bytes(2, "little")
        with zipfile.ZipFile(io.BytesIO(bytes(dados))) as z:
            with self.assertRaises(NotImplementedError) as ctx:
                z.read("a.xml")
        self.assertEqual(classify(ctx.exception).code, "unsupported")
        self.assertEqual(classify(NotImplementedError("zip file version 10.8")).code, "unsupported")
        self.assertEqual(classify(RuntimeError("File <ZipInfo filename='a'> is encrypted, password required for extraction")).code, "encrypted")
        self.assertEqual(classify(NotImplementedError("outro")).code, "internal")


class PodaConcorrente(unittest.TestCase):
    def raiz(self, tmp: str) -> Path:
        root = Path(tmp) / "raiz"
        for rid in ("atual", "candidata", "orfa"):
            (root / "releases" / rid).mkdir(parents=True)
        plat._atomic(root / "current.json", {"release_id": "atual"})
        plat._atomic(root / "candidate.json", {"release_id": "candidata"})
        return root

    def test_candidata_de_outra_instalacao_protegida(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lifecycle, "release_in_use", return_value=False):
            root = self.raiz(tmp)
            lifecycle.prune_releases(root)
            self.assertTrue((root / "releases/candidata").is_dir())
            self.assertTrue((root / "releases/atual").is_dir())
            self.assertFalse((root / "releases/orfa").exists())
            # Candidata com gate reprovado no candidate.json (limpeza do instalador) ou pelo próprio setup: removível.
            plat._atomic(root / "candidate.json", {"release_id": "candidata", "gates": {"doctor": True, "fixture": False}})
            self.assertTrue(lifecycle.remove_unreferenced_release(root, root / "releases/candidata"))
            (root / "releases/candidata").mkdir()
            plat._atomic(root / "candidate.json", {"release_id": "candidata", "gates": {"doctor": True, "fixture": True}})
            self.assertFalse(lifecycle.remove_unreferenced_release(root, root / "releases/candidata"))
            self.assertTrue(lifecycle.remove_unreferenced_release(root, root / "releases/candidata", candidate_reprovada=True))

    def test_lock_de_promocao_ocupado_adia_pela_marca(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lifecycle, "release_in_use", return_value=False):
            root = self.raiz(tmp)
            (root / "state").mkdir(exist_ok=True)
            (root / "state/promote.lock").write_text("123\n")
            self.assertFalse(lifecycle.remove_unreferenced_release(root, root / "releases/orfa"))
            self.assertTrue((root / "releases/orfa").is_dir())
            marca = json.loads((root / "state/pending-removals/orfa.json").read_text())
            self.assertEqual(marca["release_id"], "orfa")
            (root / "state/promote.lock").unlink()
            lifecycle.prune_releases(root, pending_only=True)
            self.assertFalse((root / "releases/orfa").exists())


if __name__ == "__main__":
    unittest.main()
