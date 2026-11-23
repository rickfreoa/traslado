# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
"""E1 da 3.0.0: atalho com Área de Trabalho redirecionada (questão aberta herdada da 2.0.1).

O manifesto grava `shortcuts.home` também para o usuário real. `refresh_shortcuts` repassa esse `home`, e
`create_shortcut` o trata como injetado. Quando o `home` gravado é o perfil real do Windows, os destinos têm
de vir de SHGetKnownFolderPath (Desktop redirecionado ao OneDrive, por exemplo), não de `home\\Desktop`;
quando é um perfil temporário de teste, nunca do SO. Correção presente desde 4ec6446 (2.0.2); este teste
a fixa. Nenhum teste aqui escreve fora do diretório temporário.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from traslado import platform as plat

PROFILE = "5E6C858F-0E22-4760-9AFE-EA3317B67173"
DESKTOP = "B4BFCC3A-DB2C-424C-B029-7FE99A87C641"
PROGRAMS = "A77F5D77-2E2B-44C3-A6A2-ABA601054A51"


class AtalhoDesktopRedirecionado(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="trasl-desk-")
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.perfil = base / "Users" / "titular"
        self.redirecionado = base / "OneDrive" / "Área de Trabalho"
        self.menu = base / "Roaming" / "Programs"
        self.pastas = {PROFILE: self.perfil, DESKTOP: self.redirecionado, PROGRAMS: self.menu}
        # Simula Windows só dentro de traslado.platform: _locations compara `platform == sys.platform == 'win32'`.
        for alvo in (mock.patch.object(plat, "sys", types.SimpleNamespace(platform="win32")),
                     mock.patch.object(plat, "_windows_known_folder", side_effect=lambda fid: self.pastas.get(fid))):
            alvo.start()
            self.addCleanup(alvo.stop)

    def test_home_gravado_igual_ao_perfil_real_usa_known_folders(self):
        desk, menu, _, _ = plat._locations("win32", self.perfil, None, None, None, None, injected=True)
        self.assertEqual((desk, menu), (self.redirecionado, self.menu))

    def test_perfil_temporario_nunca_consulta_o_desktop_do_so(self):
        temporario = Path(self._tmp.name) / "perfil-teste"
        desk, menu, _, _ = plat._locations("win32", temporario, None, None, None, None, injected=True)
        self.assertEqual(desk, temporario / "Desktop")
        self.assertEqual(menu, temporario / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs")

    def test_known_folder_indisponivel_cai_no_home(self):
        self.pastas[DESKTOP] = None
        desk, menu, _, _ = plat._locations("win32", self.perfil, None, None, None, None, injected=True)
        self.assertEqual((desk, menu), (self.perfil / "Desktop", self.menu))

    def test_diretorio_injetado_prevalece(self):
        outro = Path(self._tmp.name) / "injetado"
        desk, menu, _, _ = plat._locations("win32", self.perfil, outro, None, None, None, injected=True)
        self.assertEqual((desk, menu), (outro, self.menu))

    def test_refresh_shortcuts_cria_lnk_no_desktop_redirecionado(self):
        root = Path(self._tmp.name) / "managed"
        manifesto = {"release_id": "r1", "platform": "win32",
                     "shortcuts": {"enabled": True, "home": str(self.perfil),
                                   "dirs": {"desktop_dir": None, "start_menu_dir": None,
                                            "applications_dir": None, "icons_dir": None}}}
        scripts = []
        with mock.patch.object(subprocess, "run", side_effect=lambda argv, **kw: scripts.append(argv[-1])):
            plat.refresh_shortcuts(root, manifesto)
        destinos = [s.split("CreateShortcut('", 1)[1].split("')", 1)[0] for s in scripts]
        self.assertEqual(destinos, [str(self.redirecionado / "Traslado.lnk"), str(self.menu / "Traslado.lnk")])
        self.assertFalse((self.perfil / "Desktop").exists())

    def test_remove_shortcuts_do_desktop_redirecionado(self):
        self.redirecionado.mkdir(parents=True)
        (self.redirecionado / "Traslado.lnk").write_bytes(b"lnk")
        removidos = plat.remove_shortcuts(["Traslado"], platform="win32", home=self.perfil)
        self.assertEqual(removidos, [self.redirecionado / "Traslado.lnk"])


@unittest.skipUnless(sys.platform == "win32", "consulta real ao SHGetKnownFolderPath")
class AtalhoDesktopRealWindows(unittest.TestCase):
    """Só leitura: confere que o perfil real devolvido pelo SO leva ao Desktop do SO, sem criar atalho."""

    def test_perfil_real_resolve_desktop_do_so(self):
        perfil = plat._windows_known_folder(PROFILE)
        desktop = plat._windows_known_folder(DESKTOP)
        self.assertIsNotNone(perfil)
        self.assertIsNotNone(desktop)
        desk, _, _, _ = plat._locations("win32", perfil, None, None, None, None, injected=True)
        self.assertEqual(desk, desktop)


if __name__ == "__main__":
    unittest.main()
