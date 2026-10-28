# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import html
import json
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from traslado import advanced
from traslado.model import Block, Document, Options, Page

from markdown_it import MarkdownIt

BYPASSES = [
    '[Documento](\nhttps://example.invalid/a\n)',
    '![Figura\nsecundaria](https://example.invalid/a.png)',
    '![Figura](\nhttps://example.invalid/a.png\n)',
    '![Figura [interna]](https://example.invalid/a.png)',
    '![Figura \\]](https://example.invalid/a.png)',
    '> ![fig]\n>\n> [fig]: https://example.invalid/a.png',
    '> [fig]\n>\n> [fig]: https://example.invalid/a',
    '![fig\nura]\n\n[fig\nura]: https://example.invalid/a.png',
    '![x](https://example.invalid/a.png)',
    '<https://example.invalid/a>',
    '![Figura](https\\://example.invalid/a.png)',
    '![remote][id]\n\n[id]: https://example.invalid/a.png\n[run][js]\n[js]: javascript:alert(1)',
]

def destinations(markdown):
    found = []
    def walk(tokens):
        for token in tokens:
            if token.type in {"image", "link_open"}:
                found.append((token.type, token.attrGet("src" if token.type == "image" else "href")))
            if token.children:
                walk(token.children)
    walk(MarkdownIt("commonmark", {"html": True}).parse(markdown))
    return found

class Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
    def handle_data(self, data):
        self.text.append(data)

class AdvancedRC2(unittest.TestCase):
    def test_all_reproduced_bypasses_have_no_interpreted_destinations(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for content in BYPASSES:
                with self.subTest(content=content):
                    safe = advanced.sanitize(content, root, root, 1, Options())
                    self.assertEqual(destinations(safe), [])

    def test_reference_adjacent_indented_source_is_retained(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            content = '[r]: https://example.invalid/a\n    PAGAMENTO_DEVIDO_R$100\nTexto seguinte.'
            safe = advanced.sanitize(content, root, root, 1, Options())
            self.assertEqual(html.unescape(safe), content)

    def test_mathematical_angles_and_comments_remain_visible(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            content = 'x < y; a<b; x<y>z; 1 <= 2; valor<indice>; <!--SOURCE_SENTINEL-->'
            safe = advanced.sanitize(content, root, root, 1, Options())
            visible = Visible()
            visible.feed(MarkdownIt("commonmark", {"html": True}).render(safe))
            self.assertEqual(''.join(visible.text).strip(), content)

    def test_unknown_html_escaped_and_table_attrs_restricted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            safe = advanced.sanitize('<table onclick="evil()"><tr><td colspan="2">Valor < 100</td></tr></table><script>evil()</script>', root, root, 1, Options())
            self.assertNotIn('onclick', safe)
            self.assertNotIn('<script>', safe)
            self.assertIn('colspan="2"', safe)
            self.assertIn('Valor &lt; 100', safe)

    def test_local_image_reconstructed_and_marker_collision_avoided(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'assets').mkdir()
            Image.new('RGB', (12, 12), 'white').save(root / 'local.png')
            forged = 'FREOAASSETMARKER0X1END FREOAASSETMARKER1X1END &#70;REOAASSETMARKER2X1END'
            safe = advanced.sanitize(forged + '\n\n![Local](local.png)\n![Bad](../local.png)', root, root, 3, Options())
            self.assertEqual(destinations(safe), [('image', 'assets/advanced-3-1.png')])
            self.assertIn(forged, safe)
            self.assertTrue((root / 'assets/advanced-3-1.png').is_file())

    def test_images_disabled_leaves_local_syntax_passive(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            safe = advanced.sanitize('![Local](local.png)', root, root, 1, Options(images=False))
            self.assertEqual(destinations(safe), [])

    def test_refine_retains_raw_model_and_previous_blocks(self):
        import fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / 'assets').mkdir()
            executable = root / 'inert.exe'
            executable.write_text('NOT EXECUTED')
            source = root / 'source.pdf'
            with fitz.open() as pdf:
                pdf.new_page()
                pdf.save(source)
            content = '[r]: https://example.invalid/a\n    SOURCE_SENTINEL'
            def transport(argv, cwd, timeout, log, **kwargs):
                Path(log).write_text('4.0.10')
                if 'parse' in argv:
                    Path(argv[argv.index('-o') + 1]).write_text(content)
                return 0
            # T11 P0: internal line geometry must never leak into previous_blocks (document.json).
            doc = Document('source.pdf', '0' * 64, [Page(1, 'ocr', blocks=[Block('text', 'FALLBACK_SOURCE',
                                                                               lines=[{'bbox': [0, 0, 1, 1], 'spans': []}])])])
            with patch.object(advanced, 'run_bounded', transport):
                advanced.refine(source, root, Options(advanced_kit=str(executable)), doc)
            page = doc.pages[0]
            details = page.blocks[0].details
            self.assertEqual(details['raw_model_markdown'], content)
            self.assertEqual(details['previous_blocks'][0]['text'], 'FALLBACK_SOURCE')
            self.assertNotIn('lines', details['previous_blocks'][0])
            self.assertNotIn('role', details['previous_blocks'][0])
            self.assertNotIn('"lines"', json.dumps(doc.to_dict()))
            self.assertIn('SOURCE_SENTINEL', page.blocks[0].text)
            self.assertIn('model_markdown_links_literalized_formatting_partial', page.warnings)

if __name__ == '__main__':
    unittest.main()
