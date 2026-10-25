"""Causal Windows bootstrap probes; network is never used by this suite.

Set FREOA_OCR_TEST_CACHE to a verified cache for the native full integration
case. Missing real binary assets are a reported skip, never a synthetic PASS.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import shutil
import unittest
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / 'scripts/bootstrap_ocr.ps1'
LOCK = PACKAGE / 'ocr-assets.lock.json'
POWERSHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == 'nt' and POWERSHELL.is_file(), 'Windows PowerShell required; native bootstrap NOT_RUN')
class OcrBootstrapRC3(unittest.TestCase):
    def setUp(self):
        # Keep disposable probes in the caller's workspace, including restricted
        # Windows runners whose inherited system TEMP may be read-only.
        base = Path(os.environ.get('FREOA_OCR_TEST_TEMP', str(Path.cwd() / 'work/ocr-test-temporary'))).resolve()
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        self.root = base / ('freoa-ocr-rc3-' + uuid.uuid4().hex)
        # mkdtemp(0700) in Python 3.14 installs a restrictive Windows ACL that
        # excludes this runner's restricted token. Ordinary mkdir inherits ACLs.
        self.root.mkdir()
        self.lock = json.loads(LOCK.read_text(encoding='utf-8'))

    def tearDown(self):
        if self.root.resolve().parent != self.base or not self.root.name.startswith('freoa-ocr-rc3-'):
            raise RuntimeError('Cleanup outside explicitly-owned probe root')
        shutil.rmtree(self.root)

    def ps(self, command, timeout=90):
        return subprocess.run([str(POWERSHELL), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
                               "$ErrorActionPreference='Stop';" + command], capture_output=True, timeout=timeout)

    def install(self, destination, cache=None, languages=None, lock=None):
        arguments = '-Destination ' + quoted(destination) + ' -Offline'
        if cache is not None:
            arguments += ' -CacheDirectory ' + quoted(cache)
        if languages is not None:
            arguments += ' -Languages ' + quoted(languages)
        if lock is not None:
            arguments += ' -LockPath ' + quoted(lock)
        return self.ps('& ' + quoted(SCRIPT) + ' ' + arguments + ' | ConvertTo-Json -Compress -Depth 8')

    def failed_with(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr.decode('utf-8', errors='replace'))

    def test_existing_destination_refused_and_preserved(self):
        destination = self.root / 'existing'
        destination.mkdir()
        sentinel = destination / 'original.txt'
        sentinel.write_text('ORIGINAL', encoding='utf-8')
        self.failed_with(self.install(destination, self.root / 'unused-cache'), 'destino ja existe')
        self.assertEqual(sentinel.read_text(), 'ORIGINAL')
        self.assertFalse((self.root / 'unused-cache').exists())

    def test_relative_destination_refused_before_any_asset_use(self):
        self.failed_with(self.install('relative-ocr-dir', self.root / 'cache'), 'destino deve ser absoluto')
        self.assertFalse((self.root / 'cache').exists())

    def test_unknown_language_refused_before_download(self):
        destination = self.root / 'new'
        self.failed_with(self.install(destination, self.root / 'cache', 'eng,../evil'), 'idiomas invalidos')
        self.assertFalse(destination.exists())
        self.assertFalse((self.root / 'cache').exists())

    def test_missing_offline_cache_never_creates_destination(self):
        destination = self.root / 'new'
        self.failed_with(self.install(destination, self.root / 'cache'), 'cache offline incompleto')
        self.assertFalse(destination.exists())
        self.assertEqual(list((self.root / 'cache').iterdir()), [])

    def test_poisoned_same_size_cached_executable_refused_before_execution(self):
        cache = self.root / 'cache'
        cache.mkdir()
        specification = self.lock['assets'][0]
        bad = cache / specification['name']
        with bad.open('wb') as output:
            output.write(b'UNTRUSTED_EXECUTABLE')
            output.truncate(specification['bytes'])
        self.failed_with(self.install(self.root / 'new', cache), 'SHA256 divergente')
        self.assertFalse((self.root / 'new').exists())
        with bad.open('rb') as source:
            self.assertEqual(source.read(20), b'UNTRUSTED_EXECUTABLE')

    def test_escaping_archive_member_lock_refused(self):
        self.lock['tesseract_members'][0]['path'] = '../ESCAPE.exe'
        lock = self.root / 'bad-lock.json'
        lock.write_text(json.dumps(self.lock), encoding='utf-8')
        self.failed_with(self.install(self.root / 'new', self.root / 'cache', lock=lock), 'membro invalido')
        self.assertFalse((self.root / 'cache').exists())

    def test_switch_archive_members_refused_before_cache_or_extractor(self):
        for name in ('-o/private/output', 'doc/-x.txt'):
            with self.subTest(member=name):
                self.lock['tesseract_members'][0]['path'] = name
                lock = self.root / 'switch-lock.json'
                lock.write_text(json.dumps(self.lock), encoding='utf-8')
                destination = self.root / 'new'
                self.failed_with(self.install(destination, self.root / 'cache', lock=lock), 'membro invalido')
                self.assertFalse(destination.exists())
                self.assertFalse((self.root / 'cache').exists())

    def test_junction_parent_refused_without_touching_target(self):
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'sentinel.txt'
        sentinel.write_text('KEEP', encoding='utf-8')
        link = self.root / 'junction'
        created = self.ps('New-Item -ItemType Junction -Path ' + quoted(link) + ' -Value ' + quoted(outside) + ' | Out-Null')
        self.assertEqual(created.returncode, 0, created.stderr)
        try:
            self.failed_with(self.install(link / 'new', self.root / 'cache'), 'links/junctions nao permitidos')
            self.assertEqual(sentinel.read_text(), 'KEEP')
            self.assertFalse((outside / 'new').exists())
        finally:
            link.rmdir()

    def test_http_asset_rejected_before_network(self):
        asset = dict(self.lock['assets'][0])
        asset['url'] = 'http://example.invalid/UNTRUSTED.exe'
        command = '. ' + quoted(SCRIPT) + '; $asset=' + quoted(json.dumps(asset)) + ' | ConvertFrom-Json; '
        command += 'Save-OcrPinnedAsset $asset ' + quoted(self.root) + ' $false'
        self.failed_with(self.ps(command), 'origem HTTPS invalida')
        self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipUnless(os.environ.get('FREOA_OCR_TEST_CACHE'), 'Real binary cache not supplied; full native integration NOT_RUN')
    def test_native_offline_full_comma_languages_no_global_environment_change(self):
        destination = self.root / 'full runtime with spaces'
        command = '$beforePath=$env:PATH; $beforeTess=$env:TESSDATA_PREFIX; '
        command += '$r=& ' + quoted(SCRIPT) + ' -Destination ' + quoted(destination)
        command += ' -CacheDirectory ' + quoted(os.environ['FREOA_OCR_TEST_CACHE']) + " -Languages 'por,eng,spa,ita,fra,deu' -Offline; "
        command += "@{result=$r;pathSame=($beforePath -eq $env:PATH);tessSame=($beforeTess -eq $env:TESSDATA_PREFIX)} | ConvertTo-Json -Compress -Depth 8"
        actual = self.ps(command)
        self.assertEqual(actual.returncode, 0, actual.stderr)
        receipt = json.loads(actual.stdout.decode('utf-8-sig'))
        self.assertTrue(receipt['pathSame'])
        self.assertTrue(receipt['tessSame'])
        self.assertTrue(receipt['result']['Ready'])
        self.assertEqual(receipt['result']['Languages'], ['por', 'eng', 'spa', 'ita', 'fra', 'deu'])
        self.assertEqual(Path(receipt['result']['Tesseract']), destination / 'tesseract.exe')
        self.assertFalse((destination / '.bootstrap-tools').exists())
        self.assertTrue((destination / 'doc/TESSDATA-LICENSE.txt').is_file())
        self.assertTrue((destination / 'doc/7ZIP-LICENSE.txt').is_file())
        for language in receipt['result']['Languages']:
            self.assertTrue((destination / 'tessdata' / (language + '.traineddata')).is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
