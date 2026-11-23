# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""OCR fora da instalação gerenciada: a dica de instalação inclui os idiomas, e o doctor acusa os ausentes.

Origem: instalação em servidor (SF PDF) com Tesseract do sistema sem os seis idiomas padrão. Cada página
escaneada caía em needs_ocr, a dica mandava instalar só o pacote base e o doctor não apontava a falta.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from traslado import cli, doctor, lifecycle

IDIOMAS = "por+eng+spa+ita+fra+deu"


class DicaComIdiomas(unittest.TestCase):
    def dica(self, conteudo: str, languages: str | None = IDIOMAS) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "os-release"
            p.write_text(conteudo, encoding="utf-8")
            return lifecycle.linux_tesseract_hint(str(p), languages=languages)

    def test_pacotes_de_cada_idioma_por_distribuicao(self) -> None:
        casos = {
            "ID=ubuntu\nID_LIKE=debian\n": "tesseract-ocr-{}",
            "ID=fedora\n": "tesseract-langpack-{}",
            "ID=arch\n": "tesseract-data-{}",
            "ID=alpine\n": "tesseract-ocr-data-{}",
        }
        for conteudo, modelo in casos.items():
            texto = self.dica(conteudo)
            for code in IDIOMAS.split("+"):
                self.assertIn(" " + modelo.format(code), texto, (conteudo, texto))

    def test_codigo_composto_no_debian_usa_hifen(self) -> None:
        texto = self.dica("ID=debian\n", "chi_sim")
        self.assertIn("tesseract-ocr-chi-sim", texto)
        self.assertNotIn("chi_sim", texto)
        self.assertIn("tesseract-data-chi_sim", self.dica("ID=arch\n", "chi_sim"))

    def test_sem_pacote_conferido_nomeia_idiomas_sem_inventar(self) -> None:
        for conteudo in ('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n', "ID=nixos\n"):
            texto = self.dica(conteudo)
            self.assertIn("por, eng, spa, ita, fra, deu", texto, texto)
            self.assertNotIn("traineddata", texto)

    def test_sem_idiomas_mantem_a_dica_do_setup(self) -> None:
        self.assertTrue(self.dica("ID=debian\n", None).endswith("sudo apt install tesseract-ocr"))


class CliNeedsOcr(unittest.TestCase):
    def converter(self, plataforma: str) -> dict:
        resultado = {"status": "partial", "error": "OCR necessário", "error_code": "needs_ocr"}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(cli, "discover", return_value=[Path(tmp) / "a.pdf"]), \
                patch.object(cli, "execute", return_value=dict(resultado)), \
                patch.object(sys, "platform", plataforma), \
                patch.object(cli, "linux_tesseract_hint", side_effect=lambda **kw: "DICA:" + kw.get("languages", "")):
            saida = io.StringIO()
            with redirect_stdout(saida):
                cli.main(["convert", "a.pdf", "-o", tmp, "--language", "por+deu"])
        return json.loads(saida.getvalue().strip().splitlines()[-1])

    def test_linux_passa_os_idiomas_pedidos(self) -> None:
        self.assertIn("DICA:por+deu", self.converter("linux")["error"])

    def test_macos_inclui_os_idiomas(self) -> None:
        self.assertIn("brew install tesseract tesseract-lang", self.converter("darwin")["error"])


class DoctorIdiomasAusentes(unittest.TestCase):
    def inspecionar(self, listados: str) -> dict:
        proc = subprocess.CompletedProcess([], 0, stdout=listados.encode(), stderr=b"")
        with patch.object(doctor, "find_tesseract", return_value="/usr/bin/tesseract"), \
                patch.object(doctor, "managed_tessdata_for", return_value=None), \
                patch.object(doctor.subprocess, "run", return_value=proc), \
                patch.object(doctor, "runtime_settings", return_value={
                    "languages": IDIOMAS.split("+"), "data_root": "/tmp/x", "config_path": None,
                    "config_status": "default", "warnings": []}):
            return doctor.inspect()

    def test_acusa_idiomas_padrao_ausentes(self) -> None:
        report = self.inspecionar('List of available languages in "/usr/share/tessdata/" (2):\neng\nosd\n')
        self.assertEqual(report["missing_languages"], ["por", "spa", "ita", "fra", "deu"])
        self.assertTrue(any("Idiomas OCR padrão ausentes" in w and "deu" in w for w in report["warnings"]))

    def test_silencioso_com_todos_os_idiomas(self) -> None:
        report = self.inspecionar("List of available languages (7):\n" + "\n".join(IDIOMAS.split("+") + ["osd"]) + "\n")
        self.assertNotIn("missing_languages", report)
        self.assertFalse(any("Idiomas OCR" in w for w in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
