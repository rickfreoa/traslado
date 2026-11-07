# SPDX-License-Identifier: AGPL-3.0-or-later
"""2.0.1: o verificador de composição da release trata as fixtures do fuzz (ZIPs ilegíveis) sem quebrar nem cegar o gate."""
import importlib.util
import io
import sys
import unittest
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
FUZZ = RAIZ / 'tests' / 'fixtures' / 'fuzz'


def carregar():
    sys.path.insert(0, str(RAIZ / 'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('build_release_c1', RAIZ / 'scripts' / 'build_release.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


def zip_flag5(membro: str) -> bytes:
    """ZIP com um membro cujo bit 5 (dados corrigidos) está ligado nos cabeçalhos local e central."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(membro, b'x' * 64)
    dados = bytearray(buf.getvalue())
    for assinatura, deslocamento in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        i = dados.find(assinatura)
        dados[i + deslocamento] |= 0x20
    return bytes(dados)


class ScannerFuzzC1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = carregar()
        cls.docx = (FUZZ / 'finding-docx-0d70b5901ed1b479.docx').read_bytes()

    def test_premissa_flag5_ilegivel(self):
        with zipfile.ZipFile(io.BytesIO(zip_flag5('a.xml'))) as z, self.assertRaises(NotImplementedError):
            z.read('a.xml')

    def test_fixture_do_fuzz_e_opaca_no_sdist(self):
        nome = 'traslado-2.0.1/tests/fixtures/fuzz/finding-docx-0d70b5901ed1b479.docx'
        self.assertEqual(self.b.membros_proibidos(nome, self.docx, 'traslado-2.0.1.tar.gz!'), [])

    def test_zip_ilegivel_fora_das_fixtures_e_achado(self):
        achados = self.b.membros_proibidos('traslado/assets/qualquer.docx', zip_flag5('word/document.xml'))
        self.assertEqual(len(achados), 1, achados)
        self.assertIn('ilegível', achados[0])

    def test_nome_mupdf_em_fixture_ilegivel_continua_achado(self):
        achados = self.b.membros_proibidos('pkg/tests/fixtures/fuzz/x.docx', zip_flag5('fitz/_mupdf.pyd'))
        self.assertTrue(any(a.endswith(': nome') for a in achados), achados)


    def test_deflate_corrompido_dentro_e_fora_das_fixtures(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('word/document.xml', bytes(range(256)) * 64)
        dados = bytearray(buf.getvalue())
        inicio = 30 + len('word/document.xml')
        for i in range(inicio + 8, inicio + 40):
            dados[i] ^= 0xFF
        dados = bytes(dados)
        with zipfile.ZipFile(io.BytesIO(dados)) as z, self.assertRaises(Exception):
            z.read('word/document.xml')
        self.assertEqual(self.b.membros_proibidos('pkg/tests/fixtures/fuzz/y.docx', dados), [])
        achados = self.b.membros_proibidos('pkg/qualquer.docx', dados)
        self.assertEqual(len(achados), 1, achados)
        self.assertIn('ilegível', achados[0])


    def test_fixtures_reais_cabem_no_teto_e_nao_citam_mupdf(self):
        for p in sorted(FUZZ.iterdir()):
            nome = 'traslado-2.0.1/tests/fixtures/fuzz/' + p.name
            self.assertEqual(self.b.membros_proibidos(nome, p.read_bytes()), [], p.name)

    def test_fixture_grande_ou_com_mupdf_e_achado(self):
        nome = 'pkg/tests/fixtures/fuzz/z.docx'
        grande = zip_flag5('a.xml') + b'\0' * (self.b.FIXTURE_FUZZ_MAX + 1)
        self.assertTrue(any('excede' in a for a in self.b.membros_proibidos(nome, grande)))
        com_mupdf = zip_flag5('a.xml') + b'\0libmupdf\0'
        self.assertTrue(any(a.endswith(': conteúdo') for a in self.b.membros_proibidos(nome, com_mupdf)))


if __name__ == '__main__':
    unittest.main()
