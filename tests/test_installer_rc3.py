# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
SCRIPT = SOURCE / 'scripts/install_windows.py'
source_bytes = SCRIPT.read_bytes()
INSTALLER_SHA256 = hashlib.sha256(source_bytes).hexdigest()
spec = importlib.util.spec_from_file_location('freoa_installer_test_target', SCRIPT)
installer = importlib.util.module_from_spec(spec)
exec(compile(source_bytes, str(SCRIPT), 'exec'), installer.__dict__)
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

def json_file(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')

@unittest.skipUnless(os.name == 'nt', 'Windows installer')
class InstallerRC3(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='freoa-install-review-')
        self.base = Path(self.temporary.name).resolve()
        self.package, self.root, self.data, self.desktop = [self.base / name for name in ['package', 'managed', 'data', 'desktop']]
        for folder in [self.package, self.root, self.data, self.desktop]:
            folder.mkdir()
        (self.package / 'scripts').mkdir()
        for name in ['shortcut_windows.ps1', 'installation_processes.ps1']:
            shutil.copyfile(SOURCE / 'scripts' / name, self.package / 'scripts' / name)
        (self.package / 'dist').mkdir()
        self.wheel = self.package / f'dist/traslado-{installer.VERSION}-py3-none-any.whl'
        self.wheel.write_bytes(b'INERT_WHEEL_NEVER_INSTALLED')
        payload_files = [self.wheel, *sorted((self.package / 'scripts').iterdir())]
        json_file(self.package / 'INSTALL-PAYLOAD.json', {'product': installer.PRODUCT, 'version': installer.VERSION,
            'files': [{'path': p.relative_to(self.package).as_posix(), 'sha256': installer.digest(p)} for p in payload_files]})
        self.identity = 'a' * 32
        json_file(self.root / '.freoa-installation.json', {'product': installer.PRODUCT, 'schema_version': 1, 'install_id': self.identity})
        self.old = self.root / 'releases' / ('6.0.0rc2_' + 'b' * 32)
        (self.old / '.venv/Scripts').mkdir(parents=True)
        self.old_python = self.old / '.venv/Scripts/python.exe'
        self.old_python.write_bytes(b'INERT_OLD_INTERPRETER_NEVER_EXECUTED')
        (self.old / '.venv/Scripts/pythonw.exe').write_bytes(b'INERT_OLD_GUI_NEVER_EXECUTED')
        (self.old / 'app.txt').write_text('OLD_APPLICATION_SENTINEL')
        record = {'product': installer.PRODUCT, 'install_id': self.identity, 'release_id': self.old.name}
        files = installer.inventory(self.old)
        json_file(self.old / '.freoa-release.json', dict(record, files=files))
        json_file(self.root / 'current.json', record)
        self.previous = (self.root / 'current.json').read_bytes()
        self.link = self.desktop / 'Freoa Markdown Converter.lnk'
        self.calls = []
        self.args = SimpleNamespace(package=self.package, root=self.root, data_root=self.data,
            shortcut_directory=self.desktop, profile='Core', skip_ocr=True, languages=['por', 'eng'],
            no_shortcut=True, legacy_root=None, launch=False)

    def tearDown(self):
        self.temporary.cleanup()

    def fake_run(self, argv, **kwargs):
        values = [str(x) for x in argv]
        self.calls.append(values)
        if any(Path(value).name == 'installation_processes.ps1' for value in values):
            return '[]'
        if 'venv' in values:
            venv = Path(argv[-1])
            (venv / 'Scripts').mkdir(parents=True)
            for name in ['python.exe', 'pythonw.exe']:
                (venv / 'Scripts' / name).write_bytes(b'INERT_NEW_INTERPRETER')
        if values[-1] == 'doctor':
            return json.dumps({'capabilities': {name: {'available': True} for name in ['fitz', 'PIL', 'docx', 'openpyxl', 'pptx']}, 'languages': ['por', 'eng']})
        return None

    def fake_shortcut(self, action, path, script, **kwargs):
        if action == 'Inspect':
            return {'recognized': True, 'target': str(self.old / '.venv/Scripts/pythonw.exe'), 'arguments': '-I -m traslado gui'}
        Path(path).write_bytes(b'NEW_SHORTCUT_SENTINEL')
        return {'recognized': True}

    def install(self, run=None, shortcut=None):
        with contextlib.redirect_stdout(io.StringIO()), patch.object(installer, 'run', run or self.fake_run), patch.object(installer, 'shortcut', shortcut or self.fake_shortcut):
            return installer.install(self.args)

    def assert_old_preserved(self):
        self.assertEqual((self.root / 'current.json').read_bytes(), self.previous)
        self.assertTrue(self.old_python.exists())

    def test_current_directory_error_releases_own_lock(self):
        (self.root / 'current.json').unlink()
        (self.root / 'current.json').mkdir()
        with self.assertRaises(OSError):
            self.install()
        self.assertFalse((self.root / '.installation-lock').exists())
        self.assertTrue(self.old.exists())

    def test_corrupted_payload_preserves_old_version_without_executing_runtime(self):
        self.wheel.write_bytes(b'CORRUPTED_PAYLOAD')
        with self.assertRaisesRegex(ValueError, 'Payload mudou'):
            self.install()
        self.assert_old_preserved()
        self.assertFalse((self.root / '.installation-lock').exists())
        self.assertFalse(any('venv' in argv for argv in self.calls))
        self.assertFalse(any(argv[0] == str(self.old_python) for argv in self.calls))

    def test_source_data_overlap_rejected_before_lock_or_pointer_change(self):
        self.args.data_root = self.package
        with self.assertRaisesRegex(ValueError, 'separadas'):
            self.install()
        self.assert_old_preserved()
        self.assertFalse((self.root / '.installation-lock').exists())

    def test_actual_com_foreign_shortcut_is_preserved_even_if_syntax_matches(self):
        target = self.base / 'unrelated/.venv/Scripts/pythonw.exe'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'INERT_EXTERNAL_TARGET_NEVER_EXECUTED')
        proc = subprocess.run([PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SOURCE / 'scripts/shortcut_windows.ps1'),
            '-Action', 'Create', '-ShortcutPath', str(self.link), '-Target', str(target), '-WorkingDirectory', str(self.data)], capture_output=True, timeout=30, check=True)
        info = json.loads(proc.stdout.decode('utf-8-sig'))
        self.assertTrue(info['recognized'])
        original_link = self.link.read_bytes()
        self.args.no_shortcut = False
        with self.assertRaisesRegex(ValueError, 'outra instalação'):
            self.install(shortcut=lambda *args, **kwargs: info)
        self.assert_old_preserved()
        self.assertEqual(self.link.read_bytes(), original_link)
        self.assertFalse((self.root / '.installation-lock').exists())

    def test_shortcut_replace_failure_rolls_back_previous_state(self):
        self.link.write_bytes(b'OLD_SHORTCUT_SENTINEL')
        self.args.no_shortcut = False
        replace = installer.os.replace
        def fail(source, target):
            if Path(target) == self.link:
                raise OSError('SYNTHETIC_SHORTCUT_FAILURE')
            return replace(source, target)
        with patch.object(installer.os, 'replace', fail), self.assertRaises(OSError):
            self.install()
        self.assert_old_preserved()
        self.assertEqual(self.link.read_bytes(), b'OLD_SHORTCUT_SENTINEL')
        self.assertFalse((self.root / '.installation-lock').exists())

    def test_junction_cleanup_refuses_outside_delete_but_releases_own_lock(self):
        outside = self.base / 'outside'
        outside.mkdir()
        document = outside / 'USER_DOCUMENT.txt'
        document.write_text('OUTSIDE_USER_SENTINEL')
        junction = None
        def run(argv, **kwargs):
            nonlocal junction
            if 'venv' in [str(x) for x in argv]:
                folder = Path(argv[-1])
                folder.mkdir()
                junction = folder / 'junction'
                def quote(value):
                    return "'" + str(value).replace("'", "''") + "'"
                subprocess.run([PS, '-NoProfile', '-Command', 'New-Item -ItemType Junction -Path ' + quote(junction) + ' -Target ' + quote(outside) + ' -ErrorAction Stop | Out-Null'], capture_output=True, timeout=30, check=True)
                raise RuntimeError('SYNTHETIC_NEW_RELEASE_FAILURE')
            return self.fake_run(argv, **kwargs)
        try:
            # Python 3.11 não tem Path.is_junction: a junction é recusada pelo teste de reparse point (mesma recusa, outra mensagem)
            with self.assertRaisesRegex(ValueError, 'Link/junction' if hasattr(Path, 'is_junction') else 'Reparse point'):
                self.install(run=run)
            self.assertFalse((self.root / '.installation-lock').exists())
            self.assert_old_preserved()
            self.assertEqual(document.read_text(), 'OUTSIDE_USER_SENTINEL')
        finally:
            if junction and junction.exists():
                # Nonrecursive removal of our own fixture junction, not its target.
                self.assertIn(self.base, junction.absolute().parents)
                junction.rmdir()

    def test_postcommit_receipt_failure_returns_actual_installed_state_with_warning(self):
        atomic = installer.atomic_json
        def fail(path, data):
            if Path(path).name == 'last-installation.json':
                raise OSError('SYNTHETIC_RECEIPT_FAILURE')
            return atomic(path, data)
        with patch.object(installer, 'atomic_json', fail):
            result = self.install()
        self.assertEqual(result['status'], 'installed_with_warnings')
        self.assertTrue(any('recibo' in message for message in result['warnings']))
        self.assertFalse(self.old.exists())
        self.assertEqual(json.loads((self.root / 'current.json').read_text())['version'], installer.VERSION)
        self.assertFalse((self.root / '.installation-lock').exists())

    def test_postcommit_launch_failure_is_saved_as_installed_with_warning(self):
        self.args.launch = True
        with patch.object(installer.subprocess, 'Popen', side_effect=OSError('SYNTHETIC_LAUNCH_FAILURE')):
            result = self.install()
        self.assertEqual(result['status'], 'installed_with_warnings')
        receipt = json.loads((self.root / 'last-installation.json').read_text())
        self.assertEqual(receipt['status'], 'installed_with_warnings')
        self.assertTrue(any('abra pelo atalho' in message for message in result['warnings']))

    def test_active_previous_runtime_refused_before_new_install_calls(self):
        def active(argv, **kwargs):
            if any(Path(str(value)).name == 'installation_processes.ps1' for value in argv):
                return '[{"pid":123,"executable":"synthetic-active-runtime"}]'
            return self.fake_run(argv, **kwargs)
        with self.assertRaisesRegex(ValueError, 'Feche a janela'):
            self.install(run=active)
        self.assert_old_preserved()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / '.installation-lock').exists())

    def test_inventory_and_preservation_include_nested_marker_named_document(self):
        nested = self.old / 'user/.freoa-release.json'
        nested.parent.mkdir()
        nested.write_text('USER_MARKER_NAMED_DOCUMENT')
        items = installer.inventory(self.old)
        self.assertIn('user/.freoa-release.json', [item['path'] for item in items])
        self.assertNotIn('.freoa-release.json', [item['path'] for item in items])
        record = json.loads((self.root / 'current.json').read_text())
        self.assertIn(self.base, self.old.resolve().parents)
        preserved = installer.remove_managed_release(self.root, record, self.data)
        self.assertEqual(len(preserved), 1)
        self.assertEqual(Path(preserved[0]).read_text(), 'USER_MARKER_NAMED_DOCUMENT')

    def test_preexisting_concurrent_lock_is_never_deleted(self):
        lock = self.root / '.installation-lock'
        lock.write_text('OTHER_INSTALLER_SENTINEL')
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(lock.read_text(), 'OTHER_INSTALLER_SENTINEL')
        self.assert_old_preserved()

    def test_oversized_current_is_rejected_before_unbounded_bytes_read(self):
        (self.root / 'current.json').write_bytes(b' ' * (4 * 1024 * 1024 + 1))
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded bytes read')), self.assertRaisesRegex(ValueError, 'Registro excessivo'):
            self.install()
        self.assertTrue(self.old_python.exists())
        self.assertFalse((self.root / '.installation-lock').exists())

    def test_cli_doctor_and_error_json_stay_utf8_when_parent_pipe_is_cp1252(self):
        if os.environ.get('T11_EXIGE_INSTALADO'):  # modo instalado: nada de injetar o código-fonte; conferir a origem do pacote
            origin = 'import sys,pathlib,traslado;assert pathlib.Path(traslado.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve()),traslado.__file__;'
        else:
            origin = 'import sys;sys.path.insert(0,' + repr(str(SOURCE / 'src')) + ');'
        prefix = origin + 'sys.stdout.reconfigure(encoding="cp1252");sys.stderr.reconfigure(encoding="cp1252");from traslado.cli import main;'
        proc = subprocess.run([sys.executable, '-I', '-c', prefix + 'raise SystemExit(main(["doctor"]))'], capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doctor = json.loads(proc.stdout.decode('utf-8', errors='strict'))
        self.assertIn('licença', doctor['license_notice'])
        bad = self.base / 'bad.toml'
        bad.write_text('[wrong]\nvalue=1\n', encoding='utf-8')
        argv = ['convert', str(self.base / 'not-read.txt'), '-o', str(self.data), '--config', str(bad)]
        proc = subprocess.run([sys.executable, '-I', '-c', prefix + 'raise SystemExit(main(' + repr(argv) + '))'], capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        error = json.loads(proc.stderr.decode('utf-8', errors='strict'))
        self.assertIn('seção', error['error'])

    @unittest.skipIf(sys.prefix == sys.base_prefix, 'Real managed-runtime probe needs a venv interpreter')
    def test_actual_process_helper_sees_running_venv_child(self):
        child = subprocess.Popen([sys.executable, '-I', '-c', 'import time;time.sleep(30)'], creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            proc = subprocess.run([PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SOURCE / 'scripts/installation_processes.ps1'), '-ReleaseRoot', str(Path(sys.prefix).parent)], capture_output=True, timeout=30)
            if (proc.returncode and b'Get-CimInstance' in proc.stderr
                    and b'PermissionDenied' in proc.stderr):
                self.skipTest('CIM process enumeration unavailable: permission denied')
            proc.check_returncode()
            active = json.loads(proc.stdout.decode('utf-8-sig'))
            self.assertTrue(any(item['pid'] == child.pid for item in active), active)
        finally:
            child.terminate()
            child.wait(timeout=10)

if __name__ == '__main__':
    unittest.main()
