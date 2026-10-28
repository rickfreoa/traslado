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
        data = dict(schema_version=2, release_id=release, gates={'doctor': True, 'fixture': True}, **extra)
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


if __name__ == '__main__':
    unittest.main()
