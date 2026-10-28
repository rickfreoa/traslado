# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
LAUNCHER_BYTES = (SOURCE / 'ABRIR.ps1').read_bytes()
LAUNCHER_SHA256 = hashlib.sha256(LAUNCHER_BYTES).hexdigest()
LAST_PROBE = None
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'

def normalized(data):
    return data.decode('utf-8-sig').replace('\r\r\n', '\n').replace('\r\n', '\n')

def predecessor():
    equivalent = LAUNCHER_BYTES.replace(b' -Encoding UTF8', b'')
    # Prefer the actual frozen predecessor in the review workspace. Else use a
    # portable causal mutant changing only the two explicit decoding flags.
    for parent in SOURCE.parents:
        frozen = parent / 'work/rc3-final-frozen/freoa-md-converter-v6/ABRIR.ps1'
        if frozen.is_file():
            raw = frozen.read_bytes()
            if normalized(raw) == normalized(equivalent):
                return raw, 'original frozen predecessor'
    return equivalent, 'equivalent predecessor removing only two UTF8 flags'

@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5.1 launcher')
class LauncherRC3(unittest.TestCase):
    def test_real_ps51_unicode_json_predecessor_diverges_and_fixed_paths_are_exact(self):
        global LAST_PROBE
        self.assertEqual(LAUNCHER_BYTES.count(b' -Encoding UTF8'), 2)
        old_bytes, origin = predecessor()
        with tempfile.TemporaryDirectory(prefix='freoa-launcher-review-') as raw:
            base = Path(raw).resolve()
            install = base / 'managed'
            data = base / 'documentos-áé-José'
            data.mkdir()
            release_id = '6.0.0rc3_' + '1' * 32
            release = install / 'releases' / release_id
            executable = release / '.venv/Scripts/pythonw.exe'
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b'INERT_PYTHONW_MUST_NEVER_EXECUTE')
            identity = 'a' * 32
            record = {'product': 'freoa-md-converter', 'install_id': identity,
                      'release_id': release_id, 'data_root': str(data)}
            owner = {'product': 'freoa-md-converter', 'install_id': identity,
                     'owner_label': 'áé-José'}
            for name, value in [('current.json', record), ('.freoa-installation.json', owner)]:
                payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
                self.assertFalse(payload.startswith(b'\xef\xbb\xbf'))
                (install / name).write_bytes(payload)
            wrapper = base / 'capture-launch.ps1'
            wrapper.write_text('''param([string]$Launcher,[string]$InstallRoot,[string]$Capture)
$ErrorActionPreference='Stop'
function Start-Process {
    param([string]$FilePath,[object[]]$ArgumentList,[string]$WorkingDirectory,[string]$WindowStyle)
    $taskCaptured=@{
        stub_called=$true; target=$FilePath; arguments=$ArgumentList;
        working_directory=$WorkingDirectory; window_style=$WindowStyle;
        owner_label=$taskOwner.owner_label;
        default_codepage=[Text.Encoding]::Default.CodePage;
        powershell_version=$PSVersionTable.PSVersion.ToString()
    }
    [IO.File]::WriteAllText($Capture,($taskCaptured | ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
}
. $Launcher -InstallRoot $InstallRoot
''', encoding='utf-8-sig')
            captured = {}
            for label, script_bytes in [('predecessor', old_bytes), ('corrected', LAUNCHER_BYTES)]:
                script = base / (label + '-ABRIR.ps1')
                script.write_bytes(script_bytes)
                capture = base / (label + '-capture.json')
                process = subprocess.run([PS, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(wrapper),
                    '-Launcher', str(script), '-InstallRoot', str(install), '-Capture', str(capture)],
                    capture_output=True, timeout=30)
                self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', errors='replace'))
                captured[label] = json.loads(capture.read_bytes().decode('utf-8', errors='strict'))
                self.assertTrue(captured[label]['stub_called'])
                self.assertTrue(captured[label]['powershell_version'].startswith('5.1'))
                self.assertEqual(Path(captured[label]['target']), executable)
                self.assertIn(base, executable.resolve().parents)
                self.assertEqual(captured[label]['arguments'], ['-I', '-m', 'freoa_v6', 'gui'])
            self.assertEqual(captured['corrected']['working_directory'], str(data))
            self.assertEqual(captured['corrected']['owner_label'], 'áé-José')
            if captured['predecessor']['default_codepage'] != 65001:
                self.assertNotEqual(captured['predecessor']['working_directory'], str(data))
                self.assertNotEqual(captured['predecessor']['owner_label'], 'áé-José')
            LAST_PROBE = {'launcher_sha256': LAUNCHER_SHA256,
                          'predecessor_sha256': hashlib.sha256(old_bytes).hexdigest(),
                          'predecessor_origin': origin, 'expected_working_directory': str(data),
                          'expected_owner_label': 'áé-José', 'captured': captured,
                          'real_ps51': True, 'real_process_start': False,
                          'shortcut_execution': 'NOT_RUN', 'json_inputs_utf8_without_bom': True}

if __name__ == '__main__':
    unittest.main()
