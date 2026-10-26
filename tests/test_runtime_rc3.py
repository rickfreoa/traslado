from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from freoa_v6 import doctor, ocr, runtime
from freoa_v6.model import Options

class RuntimeRC3(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prefix = patch.object(sys, 'prefix', str(self.root / '.venv'))
        self.prefix.start()

    def tearDown(self):
        self.prefix.stop()
        self.temporary.cleanup()

    def config(self, **changes):
        value = {'schema_version': 1, 'languages': ['por', 'eng'], 'data_root': str(self.root / 'documentos')}
        value.update(changes)
        (self.root / 'freoa-runtime.json').write_text(json.dumps(value), encoding='utf-8')

    def engine(self):
        directory = self.root / 'ocr'
        directory.mkdir(exist_ok=True)
        executable = directory / 'tesseract.exe'
        executable.write_bytes(b'INERT_TEST_ENGINE_NOT_EXECUTED')
        (directory / 'tessdata').mkdir(exist_ok=True)
        return str(executable)

    def test_unconfigured_and_unmanaged_defaults_are_six_languages(self):
        expected = 'por+eng+spa+ita+fra+deu'
        self.assertEqual(Options().language, expected)
        with patch.object(sys, 'prefix', sys.base_prefix):
            self.assertEqual(runtime.default_language(), expected)
            self.assertIsNone(runtime.managed_tesseract())

    def test_valid_manifest_controls_effective_options_and_output_but_never_engine_paths(self):
        executable = self.engine()
        self.config(languages=['fra', 'por'], tesseract=str(self.root / 'attacker.exe'), tessdata='attacker')
        self.assertEqual(Options().language, 'fra+por')
        self.assertEqual(runtime.default_output_path(), self.root / 'documentos/resultados')
        self.assertEqual(ocr.find_tesseract(), executable)
        self.assertEqual(runtime.managed_tessdata_for(executable), str(self.root / 'ocr/tessdata'))
        self.assertEqual(runtime.runtime_settings()['config_status'], 'valid')

    def test_invalid_and_oversized_manifest_use_reported_safe_defaults(self):
        for changes in [ {'languages': ['por', 'por']}, {'languages': ['por;evil']},
                         {'languages': ['rus']}, {'data_root': 'relative'}, {'schema_version': True} ]:
            with self.subTest(changes=changes):
                self.config(**changes)
                self.assertEqual(runtime.runtime_settings()['config_status'], 'invalid')
                self.assertEqual(Options().language, 'por+eng+spa+ita+fra+deu')
                self.assertTrue(runtime.runtime_settings()['warnings'])
        (self.root / 'freoa-runtime.json').write_bytes(b' ' * (runtime.MAX_CONFIG_BYTES + 1))
        self.assertEqual(runtime.runtime_settings()['config_status'], 'invalid')
        (self.root / 'freoa-runtime.json').write_text('[' * 2000 + '0' + ']' * 2000, encoding='utf-8')
        self.assertEqual(runtime.runtime_settings()['config_status'], 'invalid')

    def test_explicit_engine_precedes_managed_and_managed_precedes_path(self):
        managed = self.engine()
        external = self.root / 'external.exe'
        external.write_bytes(b'INERT_EXTERNAL_ENGINE')
        with patch.object(ocr.shutil, 'which', return_value=str(external)):
            self.assertEqual(ocr.find_tesseract(), managed)
            self.assertEqual(ocr.find_tesseract(str(external)), str(external))
        self.assertIsNone(runtime.managed_tessdata_for(str(external)))
        explicit = self.root / 'explicit-data'
        explicit.mkdir()
        self.assertEqual(runtime.managed_tessdata_for(managed, str(explicit)), str(explicit))

    def test_recognize_managed_directory_and_explicit_override_have_matching_argv(self):
        managed = self.engine()
        self.config(languages=['por', 'ita'])
        external = self.root / 'external.exe'
        external.write_bytes(b'INERT_EXTERNAL_ENGINE')
        explicit = self.root / 'explicit-data'
        explicit.mkdir()
        seen = []
        def bounded(argv, cwd, timeout, log, **kwargs):
            if "--list-langs" in argv:
                log.write_text("List of available languages (2):\npor\nita\n")
                return 0
            seen.append(argv)
            base = Path(argv[2])
            base.with_suffix('.txt').write_text('SOURCE_SENTINEL', encoding='utf-8')
            base.with_suffix('.tsv').write_text('conf\ttext\n95\tSOURCE_SENTINEL\n', encoding='utf-8')
            return 0
        with patch.object(ocr, 'run_bounded', bounded):
            for options in [Options(), Options(tesseract=managed, tessdata=str(explicit)), Options(tesseract=str(external))]:
                block, notes = ocr.recognize(self.root / 'not-read-image.png', self.root, options)
                self.assertEqual(block.text, 'SOURCE_SENTINEL')
                self.assertEqual(block.details['language'], 'por+ita')
        self.assertEqual(seen[0][0], managed)
        self.assertEqual(seen[0][seen[0].index('--tessdata-dir') + 1], str(self.root / 'ocr/tessdata'))
        self.assertEqual(seen[1][seen[1].index('--tessdata-dir') + 1], str(explicit))
        self.assertNotIn('--tessdata-dir', seen[2])

    def test_doctor_lists_actual_languages_in_the_same_managed_directory(self):
        managed = self.engine()
        self.config(languages=['por', 'deu'])
        seen = []
        def process(argv, **kwargs):
            seen.append(argv)
            return SimpleNamespace(returncode=0, stdout=b'List of available languages (2):\npor\ndeu\n')
        with patch.object(doctor.subprocess, 'run', process):
            report = doctor.inspect()
        self.assertEqual(report['default_language'], Options().language)
        self.assertEqual(report['languages'], ['por', 'deu'])
        self.assertEqual(report['tesseract'], managed)
        self.assertEqual(report['tessdata'], str(self.root / 'ocr/tessdata'))
        self.assertEqual(seen[0], [managed, '--tessdata-dir', str(self.root / 'ocr/tessdata'), '--list-langs'])

    def test_missing_managed_data_is_visible_and_never_uses_external_default_data(self):
        managed = self.engine()
        (self.root / 'ocr/tessdata').rmdir()
        with self.assertRaisesRegex(ValueError, 'tessdata'):
            runtime.managed_tessdata_for(managed)
        report = doctor.inspect()
        self.assertTrue(any('tessdata' in warning for warning in report['warnings']))

if __name__ == '__main__':
    unittest.main()
