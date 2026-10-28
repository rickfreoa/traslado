import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from traslado.errors import ConversionError, classify
from traslado.model import Options
from traslado.runner import execute
from traslado.safety import Cancelled, check_archive, run_bounded


class ErrorsE3(unittest.TestCase):
    def test_exception_contract(self):
        for exc, expected in [(OSError('read'), 'io'), (TimeoutError('late'), 'timeout'),
                              (MemoryError('memory'), 'resource_limit'), (Cancelled('stop'), 'cancelled'),
                              (zipfile.BadZipFile('zip'), 'malformed'), (RuntimeError('bug'), 'internal')]:
            self.assertEqual(classify(exc).code, expected)
        issue = ConversionError('needs_ocr', 'same message', {'error_pages': [2]})
        self.assertIs(classify(issue), issue)
        self.assertEqual(str(issue), 'same message')

    def test_execute_failure_matrix(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            p = root / 'a.txt'
            p.write_text('sentinel ' * 100)
            for opts in [Options(max_input_bytes=1), Options(max_output_bytes=100)]:
                self.assertEqual(execute(p, root / 'out', opts)['error_code'], 'resource_limit')
            self.assertEqual(execute(p, root / 'out', cancel=lambda: True)['error_code'], 'cancelled')
            self.assertEqual(execute(root / 'missing.txt', root / 'out')['error_code'], 'io')
            with patch('traslado.runner.run_bounded', side_effect=TimeoutError('Tempo limite excedido')):
                r = execute(p, root / 'out')
                self.assertEqual((r['status'], r['error_code'], r['error']), ('timeout', 'timeout', 'Tempo limite excedido'))
            with patch('traslado.runner.run_bounded', side_effect=RuntimeError('bug')):
                self.assertEqual(execute(p, root / 'out')['error_code'], 'internal')
            with patch('traslado.runner.run_bounded', return_value=-1073741801):
                self.assertEqual(execute(p, root / 'out')['error_code'], 'resource_limit')
            for suffix, payload in [('.pdf', b'%PDF-1.7\nCORRUPT'), ('.docx', b'PK\x03\x04invalid')]:
                p = root / ('corrupt' + suffix)
                p.write_bytes(payload)
                expected = 'unsupported' if suffix == '.pdf' and not importlib.util.find_spec('fitz') else 'malformed'
                self.assertEqual(execute(p, root / 'out')['error_code'], expected)

    def test_office_zip_failures(self):
        cases = [('word/document.xml', '<!DOCTYPE x><x/>', 'malformed', Options()),
                 ('../outside', 'x', 'malformed', Options()),
                 ('word/document.xml', 'x' * 2000, 'resource_limit', Options(max_archive_bytes=1000))]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            p = root / 'a.docx'
            for member, payload, expected, opts in cases:
                with zipfile.ZipFile(p, 'w') as z:
                    z.writestr(member, payload)
                with self.assertRaises(ConversionError) as caught:
                    check_archive(p, opts)
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(execute(p, root / 'out', opts)['error_code'], expected)

    def test_pixels_frames_cells_xml_and_zip_bomb(self):
        from PIL import Image
        from docx import Document
        import stat
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            p = root / 'a.png'
            Image.new('RGB', (50, 50)).save(p)
            self.assertEqual(execute(p, root / 'out', Options(mode='native', max_pixels=1000))['error_code'], 'resource_limit')
            # Header is valid; corruption is encountered only while decoding pixels.
            image = Image.frombytes('RGB', (100, 100), os.urandom(30000))
            image.save(p)
            p.write_bytes(p.read_bytes()[:p.stat().st_size // 2])
            self.assertEqual(execute(p, root / 'out', Options(mode='native'))['error_code'], 'malformed')
            p = root / 'a.csv'
            p.write_text('a,b,c\n1,2,3')
            self.assertEqual(execute(p, root / 'out', Options(max_cells=1))['error_code'], 'resource_limit')
            # Ragged rendered grid is checked in the existing renderer, outside E3's edit surface.
            p.write_text('a\nb,c\nd')
            self.assertEqual(execute(p, root / 'out', Options(max_cells=4))['error_code'], 'resource_limit')
            p = root / 'a.docx'
            doc = Document()
            doc.add_paragraph('sentinel')
            doc.save(p)
            with zipfile.ZipFile(p) as z:
                members = {name: z.read(name) for name in z.namelist()}
            members['word/document.xml'] = b'<broken'
            with zipfile.ZipFile(p, 'w') as z:
                for name, data in members.items():
                    z.writestr(name, data)
            self.assertEqual(execute(p, root / 'out')['error_code'], 'malformed')
            with zipfile.ZipFile(p, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr('word/document.xml', b' ' * (11 * 1024 * 1024))
            self.assertEqual(execute(p, root / 'out')['error_code'], 'resource_limit')
            with zipfile.ZipFile(p, 'w') as z:
                info = zipfile.ZipInfo('link')
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, '../outside')
            self.assertEqual(execute(p, root / 'out')['error_code'], 'malformed')

    def test_invalid_deflate_stream_is_malformed(self):
        import zlib
        self.assertEqual(classify(zlib.error('invalid block type')).code, 'malformed')
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for member in ('word/document.xml', '[Content_Types].xml'):
                with self.subTest(member=member):
                    p = root / 'a.docx'
                    with zipfile.ZipFile(p, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                        # The corrupt member goes first, so its raw stream offset is known.
                        z.writestr(member, '<Types>wordprocessingml.document.main+xml</Types>')
                        other = '[Content_Types].xml' if member == 'word/document.xml' else 'word/document.xml'
                        z.writestr(other, '<document/>')
                    data = bytearray(p.read_bytes())
                    offset = 30 + int.from_bytes(data[26:28], 'little') + int.from_bytes(data[28:30], 'little')
                    data[offset] = 0xff  # reserved Deflate block type
                    p.write_bytes(data)
                    self.assertEqual(execute(p, root / 'out')['error_code'], 'malformed')

    def test_typed_process_timeout_and_output_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for kwargs, code in [({'timeout': .1}, 'timeout'),
                                 ({'timeout': 15, 'cancel': lambda: True}, 'cancelled'),
                                 ({'timeout': 15, 'max_log_bytes': 1}, 'resource_limit')]:
                with self.subTest(code=code), self.assertRaises(ConversionError) as caught:
                    run_bounded([sys.executable, '-u', '-c', 'import time; print("sentinel", flush=True); time.sleep(20)'],
                                root, log=root / 'log', **kwargs)
                self.assertEqual(caught.exception.code, code)

    def test_link_is_typed_io(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            p = root / 'a.txt'
            p.write_text('sentinel')
            link = root / 'link.txt'
            try:
                link.symlink_to(p)
            except OSError:
                self.skipTest('Symlink indisponível neste token')
            self.assertEqual(execute(link, root / 'out')['error_code'], 'io')

    def test_encrypted_and_page_limits(self):
        import fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for encrypted in (False, True):
                p = root / 'a.pdf'
                with fitz.open() as pdf:
                    pdf.new_page()
                    pdf.new_page()
                    pdf.save(p, encryption=fitz.PDF_ENCRYPT_AES_256 if encrypted else fitz.PDF_ENCRYPT_NONE,
                             owner_pw='owner', user_pw='password')
                result = execute(p, root / 'out', Options(max_pages=1))
                self.assertEqual(result['error_code'], 'encrypted' if encrypted else 'resource_limit', result)

    def test_partial_missing_ocr_has_pages(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            p = root / 'a.tif'
            Image.new('RGB', (40, 40), 'white').save(p, save_all=True,
                    append_images=[Image.new('RGB', (40, 40), 'black')])
            result = execute(p, root / 'out', Options(tesseract=str(root / 'absent.exe')))
            self.assertEqual(result['status'], 'partial', result)
            self.assertEqual(result['error_code'], 'needs_ocr', result)
            self.assertEqual(result['error_pages'], [1, 2])

    def test_cli_subprocess_exit_codes_and_configuration(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pdf_code = 'malformed' if importlib.util.find_spec('fitz') else 'unsupported'
            cases = [('a.txt', b'text', 0, None), ('a.html', b'<html>text</html>', 3, 'unsupported'),
                     ('a.pdf', b'%PDF-broken', 2, pdf_code)]
            for name, payload, exit_code, error_code in cases:
                p = root / name
                p.write_bytes(payload)
                proc = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'traslado', 'convert', str(p), '-o', str(root / 'out')],
                                      capture_output=True, encoding='utf-8', timeout=90)
                self.assertEqual(proc.returncode, exit_code, proc.stdout + proc.stderr)
                data = json.loads(proc.stdout)
                self.assertEqual(data.get('error_code'), error_code)
            proc = subprocess.run([sys.executable, '-m', 'traslado', 'convert', str(root / 'a.txt'), '-o', str(root / 'out'), '--dpi', '0'],
                                  capture_output=True, encoding='utf-8', timeout=90)
            self.assertEqual(proc.returncode, 2)
            self.assertIn('error_code', json.loads(proc.stderr))
            proc = subprocess.run([sys.executable, '-m', 'traslado', 'convert'],
                                  capture_output=True, encoding='utf-8', timeout=90)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stderr)['error_code'], 'malformed')
            for name in ('Cancelled', 'TimeoutError'):
                script = ('from unittest.mock import patch; from traslado.cli import main; '
                          'from traslado.safety import Cancelled; import sys; '
                          f'p=patch("traslado.runner.run_bounded",side_effect={name}("stop")); p.start(); '
                          'sys.exit(main(sys.argv[1:]))')
                proc = subprocess.run([sys.executable, '-c', script, 'convert', str(root / 'a.txt'), '-o', str(root / 'out')],
                                      capture_output=True, encoding='utf-8', timeout=90)
                self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
                self.assertEqual(json.loads(proc.stdout)['error_code'], 'cancelled' if name == 'Cancelled' else 'timeout')
            # Run the real CLI handler in a subprocess with an injected user interrupt.
            script = "from unittest.mock import patch; from traslado.cli import main; import sys; " \
                     "p=patch('traslado.cli.discover', side_effect=KeyboardInterrupt); p.start(); " \
                     "sys.exit(main(['convert','x','-o','y']))"
            proc = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=90)
            self.assertEqual(proc.returncode, 130)


if __name__ == '__main__':
    unittest.main()
