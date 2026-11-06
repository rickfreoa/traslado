# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from traslado import platform as p


class PlatformE2(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_roots_and_interpreters(self):
        env = {'HOME': str(self.root), 'LOCALAPPDATA': str(self.root / 'local')}
        self.assertEqual(p.managed_root(platform='win32', env=env), self.root / 'local/Traslado')
        self.assertEqual(p.legacy_roots(platform='win32', env=env), [self.root / 'local/Freoa/MarkdownConverter'])
        self.assertEqual(p.managed_root(platform='linux', env=env), self.root / '.local/share/traslado')
        self.assertEqual(p.managed_root(platform='linux', env={**env, 'XDG_DATA_HOME': str(self.root / 'xdg')}), self.root / 'xdg/traslado')
        self.assertEqual(p.managed_root(platform='darwin', env=env), self.root / 'Library/Application Support/Traslado')
        for system in ('linux', 'darwin'):
            self.assertEqual(p.legacy_roots(platform=system, env=env), [])
            self.assertEqual(p.release_python(self.root, 'v1', platform=system), self.root / 'releases/v1/.venv/bin/python')
        self.assertEqual(p.release_pythonw(self.root, 'v1', platform='win32').name, 'pythonw.exe')
        with self.assertRaises(ValueError):
            p.release_python(self.root, '../escape')

    def candidate(self, release='v1', **extra):
        (self.root / 'releases' / release).mkdir(parents=True, exist_ok=True)
        data = dict(schema_version=2, release_id=release, gates={'doctor': True, 'fixture': True, 'revocation': 'ok'}, **extra)
        p.write_candidate(self.root, data)
        return data

    def test_promote_rollback_preserves_manifest(self):
        self.candidate(python='/explicit/python', profile='Core')
        first = p.promote(self.root, now='2026-10-28T00:00:00Z')
        self.assertIsNone(first['previous_release_id'])
        self.candidate('v2', python='/other/python', profile='Full')
        second = p.promote(self.root)
        self.assertEqual(second['previous_release_id'], 'v1')
        self.assertFalse((self.root / 'candidate.json').exists())
        restored = p.rollback(self.root)
        self.assertEqual((restored['release_id'], restored['python'], restored['profile']), ('v1', '/explicit/python', 'Core'))
        self.assertEqual(p.rollback(self.root)['release_id'], 'v2')

    def test_failed_rename_preserves_current_and_candidate(self):
        self.candidate()
        p.promote(self.root)
        original = (self.root / 'current.json').read_bytes()
        self.candidate('v2')
        real = os.replace
        def fail(src, dst):
            if Path(dst).name == 'current.json':
                json.loads(Path(src).read_text(encoding='utf-8'))
                raise OSError('injected before rename')
            real(src, dst)
        with patch.object(p.os, 'replace', side_effect=fail), self.assertRaises(OSError):
            p.promote(self.root)
        self.assertEqual((self.root / 'current.json').read_bytes(), original)
        self.assertEqual(p.read_candidate(self.root)['release_id'], 'v2')
        self.assertEqual(list(self.root.glob('.current.json*')), [])

    def test_gates_and_missing_rollback(self):
        for gates in ({}, {'doctor': 1, 'fixture': True}, {'doctor': True, 'fixture': False},
                      {'doctor': True, 'fixture': True, 'revocation': 'blocked'}):
            self.candidate()
            data = p.read_candidate(self.root)
            data['gates'] = gates
            p.write_candidate(self.root, data)
            with self.assertRaises(ValueError):
                p.promote(self.root)
            self.assertIsNone(p.read_current(self.root))
        self.candidate()
        p.promote(self.root)
        with self.assertRaises(ValueError):
            p.rollback(self.root)

    def test_where_invalid_and_legacy(self):
        env = {'LOCALAPPDATA': str(self.root)}
        result = p.where(self.root, platform='win32', env=env)
        self.assertEqual(result['schema'], 'traslado-where-1')
        self.assertIsNone(result['python'])
        self.assertEqual(result['legacy_roots'], [])
        legacy = self.root / 'Freoa/MarkdownConverter'
        legacy.mkdir(parents=True)
        (legacy / 'current.json').write_text('{"release_id":"old"}')
        self.candidate()
        self.assertIsNone(p.where(self.root)['python'])
        p.promote(self.root)
        result = p.where(self.root, platform='win32', env=env)
        self.assertTrue(result['python'].endswith('python.exe'))
        self.assertEqual(result['legacy_roots'][0]['current']['release_id'], 'old')
        (self.root / 'candidate.json').write_text('[]')
        self.assertIsNone(p.read_candidate(self.root))
        self.assertTrue(p.where(self.root)['errors'])

    def test_shortcuts_posix_content_and_cleanup(self):
        for system in ('linux', 'darwin'):
            home = self.root / system
            target = self.root / "space's folder/python"
            paths = p.create_shortcut(target, platform=system, home=home)
            self.assertTrue(all(path.exists() for path in paths))
            if system == 'linux':
                content = paths[0].read_text()
                self.assertIn('-I -m traslado.gui', content)
                self.assertIn(p._desktop_exec(target), content)
                self.assertEqual(paths[1].read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            else:
                with (paths[0] / 'Contents/Info.plist').open('rb') as stream:
                    self.assertEqual(plistlib.load(stream)['CFBundleExecutable'], 'Traslado')
                self.assertIn('-I -m traslado.gui', (paths[0] / 'Contents/MacOS/Traslado').read_text())
                self.assertEqual((paths[0] / 'Contents/Resources/traslado.png').read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(p.remove_shortcuts(['Traslado'], platform=system, home=home), paths)

    def test_windows_shortcuts(self):
        kwargs = dict(platform='win32', home=self.root, desktop_dir=self.root / 'desk', start_menu_dir=self.root / 'menu')
        if sys.platform == 'win32':
            paths = p.create_shortcut(sys.executable, **kwargs)
            for path in paths:
                script = "$s=New-Object -ComObject WScript.Shell; $l=$s.CreateShortcut('" + str(path).replace("'", "''") + "'); @{target=$l.TargetPath;args=$l.Arguments}|ConvertTo-Json -Compress"
                data = json.loads(subprocess.check_output(['powershell.exe', '-NoProfile', '-Command', script], text=True))
                self.assertTrue(data['target'].endswith('pythonw.exe'))
                self.assertEqual(data['args'], '-I -m traslado.gui')
            self.assertEqual(p.remove_shortcuts(['Traslado'], **kwargs), paths)
        else:
            with patch.object(p.subprocess, 'run') as run:
                p.create_shortcut(sys.executable, **kwargs)
            self.assertEqual(run.call_count, 2)
            self.assertIn('-I -m traslado.gui', run.call_args.args[0][-1])

    def test_shortcuts_command_enables_shortcuts_after_no_shortcut(self):
        from traslado import cli
        fake_home = self.root / "fake_home"
        fake_home.mkdir(parents=True, exist_ok=True)
        desk_dir = fake_home / "desk"
        menu_dir = fake_home / "menu"
        managed = self.root / "managed"
        managed.mkdir(parents=True, exist_ok=True)

        rel_py = p.release_python(managed, "v1")
        rel_py.parent.mkdir(parents=True, exist_ok=True)
        rel_py.write_bytes(b"sentinel")

        current_manifest = {
            "schema_version": 2,
            "product": "traslado",
            "release_id": "v1",
            "version": "2.0.1",
            "platform": sys.platform,
            "shortcuts": {
                "enabled": False,
                "home": str(fake_home),
                "dirs": {
                    "desktop_dir": str(desk_dir),
                    "start_menu_dir": str(menu_dir),
                },
            },
        }
        p._atomic(managed / "current.json", current_manifest)

        # Verify initial state: shortcuts.enabled is False and no shortcuts exist
        self.assertFalse(p.read_current(managed)["shortcuts"]["enabled"])
        p.refresh_shortcuts(managed, current_manifest)
        if sys.platform == "win32":
            self.assertFalse((desk_dir / "Traslado.lnk").exists())
            self.assertFalse((menu_dir / "Traslado.lnk").exists())

        env = {
            "HOME": str(fake_home),
            "USERPROFILE": str(fake_home),
            "APPDATA": str(fake_home / "AppData/Roaming"),
            "LOCALAPPDATA": str(fake_home / "AppData/Local"),
        }
        with patch.dict(os.environ, env):
            code = cli.main(["shortcuts", "--root", str(managed)])
            self.assertEqual(code, 0)

        # Item D: Explicit command creates shortcuts and sets shortcuts.enabled = True atomically
        updated = p.read_current(managed)
        self.assertTrue(updated["shortcuts"]["enabled"])
        if sys.platform == "win32":
            self.assertTrue((desk_dir / "Traslado.lnk").exists())
            self.assertTrue((menu_dir / "Traslado.lnk").exists())

        # Verify update and rollback continue respecting the recorded state
        # Rollback to v0 where shortcuts.enabled is False keeps shortcuts disabled
        (managed / "releases" / "v0").mkdir(parents=True, exist_ok=True)
        v0_py = p.release_python(managed, "v0")
        v0_py.parent.mkdir(parents=True, exist_ok=True)
        v0_py.write_bytes(b"sentinel")
        v0_manifest = {
            "schema_version": 2,
            "product": "traslado",
            "release_id": "v0",
            "version": "2.0.0",
            "platform": sys.platform,
            "shortcuts": {
                "enabled": False,
                "home": str(fake_home),
                "dirs": {
                    "desktop_dir": str(desk_dir),
                    "start_menu_dir": str(menu_dir),
                },
            },
        }
        (managed / "state" / "history").mkdir(parents=True, exist_ok=True)
        p._atomic(managed / "state" / "history" / "v0.json", v0_manifest)
        updated["previous_release_id"] = "v0"
        p._atomic(managed / "current.json", updated)

        rolled_back = p.rollback(managed)
        self.assertFalse(rolled_back["shortcuts"]["enabled"])
        self.assertFalse(p.read_current(managed)["shortcuts"]["enabled"])

        # Verify update_release respects the recorded state:
        # When shortcuts are enabled in current.json, -NoShortcut / --no-shortcut is omitted.
        # When shortcuts are disabled, -NoShortcut / --no-shortcut is included.
        from traslado import lifecycle
        import hashlib
        commands = []
        def fake_run(argv, **kw):
            commands.append(list(argv))
            return subprocess.CompletedProcess(argv, 0)

        update_pkg = self.root / "update_pkg"
        update_pkg.mkdir(parents=True, exist_ok=True)
        wheel_name = "traslado-2.0.2-py3-none-any.whl"
        (update_pkg / wheel_name).write_bytes(b"wheel_sentinel")
        script_name = "install.ps1" if sys.platform == "win32" else "install.sh"
        (update_pkg / script_name).write_bytes(b"script_sentinel")
        wh_hash = hashlib.sha256(b"wheel_sentinel").hexdigest()
        sc_hash = hashlib.sha256(b"script_sentinel").hexdigest()
        (update_pkg / "SHA256SUMS").write_text(f"{wh_hash}  {wheel_name}\n{sc_hash}  {script_name}\n", encoding="utf-8")

        no_sc_flag = "-NoShortcut" if sys.platform == "win32" else "--no-shortcut"

        # Update while shortcuts enabled:
        p._atomic(managed / "current.json", updated)
        lifecycle.update_release(root=managed, from_dir=update_pkg, yes=True, run_cmd=fake_run)
        self.assertNotIn(no_sc_flag, commands[-1])

        # Update while shortcuts disabled:
        p._atomic(managed / "current.json", rolled_back)
        lifecycle.update_release(root=managed, from_dir=update_pkg, yes=True, run_cmd=fake_run)
        self.assertIn(no_sc_flag, commands[-1])


if __name__ == '__main__':
    unittest.main()
