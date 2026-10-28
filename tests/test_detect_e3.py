"""Content signatures and end-to-end dispatch, using synthetic public fixtures."""
import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

from traslado.detect import sniff
from traslado.model import Options
from traslado.runner import discover, execute


class DetectionE3(unittest.TestCase):
    def test_signatures_and_conservative_html(self):
        cases = [(b' ' * 100 + b'%PDF-1.7', '.pdf'),
                 (b'\x89PNG\r\n\x1a\n', '.png'), (b'\xff\xd8\xff\xe0', '.jpg'),
                 (b'II*\0\x08\0\0\0', '.tif'), (b'MM\0*\0\0\0\x08', '.tif'), (b'BM', None),
                 (b'RIFF1234WEBP', '.webp'), (b'GIF89a', None),
                 (b'\xef\xbb\xbf \r\n<!DOCTYPE HTML>', '.html'),
                 (b' <HTML lang="pt">', '.html'), (b'<htmlish>', None),
                 (b'text mentions <html>', None), (b' ' * 4096 + b'<html>', None),
                 (b'plain text', None), (b'a,b\n1,2', None)]
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / 'input.bin'
            for payload, expected in cases:
                with self.subTest(payload=payload[:30]):
                    p.write_bytes(payload)
                    self.assertEqual(sniff(p), expected)

    def test_unsupported_signatures_override_supported_extension(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for fmt, payload in [('ole2', bytes.fromhex('D0CF11E0A1B11AE1')), ('.rtf', b'{\\rtf1 test')]:
                p = root / 'input.docx'
                p.write_bytes(payload)
                result = execute(p, root / 'out')
                self.assertEqual(result['error_code'], 'unsupported', result)
                self.assertIn(fmt, result['error'])
            for mime, fmt in [('application/epub+zip', '.epub'),
                              ('application/vnd.oasis.opendocument.text', '.odt'),
                              ('application/vnd.oasis.opendocument.spreadsheet', '.ods'),
                              ('application/vnd.oasis.opendocument.presentation', '.odp')]:
                with zipfile.ZipFile(p, 'w') as z:
                    z.writestr('mimetype', mime)
                result = execute(p, root / 'out')
                self.assertEqual(result['error_code'], 'unsupported', result)
                self.assertIn(fmt, result['error'])

    def test_supported_formats_dispatch_by_content(self):
        import fitz
        from docx import Document
        from openpyxl import Workbook
        from pptx import Presentation
        from PIL import Image
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pdf = fitz.open()
            pdf.new_page().insert_text((50, 50), 'Content dispatch sentinel 12345')
            pdf.save(root / 'original.pdf')
            pdf.close()
            doc = Document()
            doc.add_paragraph('Content dispatch sentinel 12345')
            doc.save(root / 'original.docx')
            wb = Workbook()
            wb.active['A1'] = 'Content dispatch sentinel 12345'
            wb.save(root / 'original.xlsx')
            prs = Presentation()
            slide = prs.slides.add_slide(prs.slide_layouts[5])
            slide.shapes.title.text = 'Content dispatch sentinel 12345'
            prs.save(root / 'original.pptx')
            (root / 'original.html').write_text('<html><p>Content dispatch sentinel 12345</p></html>')
            formats = ['.pdf', '.docx', '.xlsx', '.pptx', '.html']
            for suffix, pil_format in [('.png', 'PNG'), ('.jpg', 'JPEG'), ('.tif', 'TIFF'), ('.bmp', 'BMP'), ('.webp', 'WEBP')]:
                Image.new('RGB', (40, 40), 'white').save(root / ('original' + suffix), pil_format)
                formats.append(suffix)
            for suffix in formats:
                with self.subTest(suffix=suffix):
                    original = root / ('original' + suffix)
                    # Same basename for comparable headings; the title retains the actual filename.
                    wrong_suffix = '.jpg' if suffix == '.png' else '.xlsx' if suffix == '.docx' else '.docx'
                    wrong = root / ('renamed' + wrong_suffix)
                    wrong.write_bytes(original.read_bytes())
                    opts = Options(mode='native', chunks=True)
                    correct = execute(original, root / 'out', opts)
                    changed = execute(wrong, root / 'out', opts)
                    self.assertIn(changed['status'], {'complete', 'partial'}, changed)
                    self.assertEqual(changed['warning_summary']['format_extension_mismatch'], 1)
                    folder = Path(changed['output'])
                    data = json.loads((folder / 'document.json').read_text(encoding='utf-8'))
                    warning = f'format_extension_mismatch: extensão {wrong.suffix} com conteúdo {sniff(wrong)}'
                    self.assertIn(warning, data['warnings'])
                    notice = (folder / 'LEIA-AVISOS.txt').read_text(encoding='utf-8')
                    self.assertEqual(notice.count('format_extension_mismatch'), 1)
                    self.assertIn(warning, notice)
                    self.assertNotIn('error_code', changed)
                    def body(result):
                        lines = (Path(result['output']) / 'document.md').read_text(encoding='utf-8').splitlines()
                        return '\n'.join(line for line in lines[1:] if not line.startswith('> Este resultado requer revisão.')).strip()
                    self.assertEqual(body(correct), body(changed))
                    self.assertIn('format_extension_mismatch', (folder / 'chunks/chunks.jsonl').read_text(encoding='utf-8'))

    def test_directory_keeps_extension_selection(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'yes.docx').write_bytes(b'%PDF-1.7')
            (root / 'ignored.bin').write_bytes(b'%PDF-1.7')
            self.assertEqual(discover(root, root.parent / 'output'), [root / 'yes.docx'])

    def test_alias_extensions_do_not_emit_mismatch(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for suffix, fmt in [('.jpeg', 'JPEG'), ('.tiff', 'TIFF')]:
                p = root / ('alias' + suffix)
                Image.new('RGB', (40, 40), 'white').save(p, fmt)
                result = execute(p, root / 'out', Options(mode='native'))
                self.assertEqual(result['status'], 'partial', result)
                self.assertNotIn('format_extension_mismatch', result['warning_summary'])
            p = root / 'alias.htm'
            p.write_text('<html><p>sentinel</p></html>')
            result = execute(p, root / 'out')
            self.assertNotIn('format_extension_mismatch', result['warning_summary'])

    def test_content_types_can_identify_missing_main_part(self):
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / 'a.xlsx'
            with zipfile.ZipFile(p, 'w') as z:
                z.writestr('[Content_Types].xml', '<Types>wordprocessingml.document.main+xml</Types>')
            self.assertEqual(sniff(p), '.docx')
            expected = 'missing_part' if importlib.util.find_spec('docx') else 'unsupported'
            self.assertEqual(execute(p, Path(raw) / 'out')['error_code'], expected)


if __name__ == '__main__':
    unittest.main()
