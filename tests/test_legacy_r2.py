"""Revisão E4, rodada 2: ZIP com cabeçalho local divergente é malformed no adaptador legado."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from traslado.errors import ConversionError
from traslado.model import Document, Options

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"


@unittest.skipUnless(importlib.util.find_spec("anydoc") is not None, "extra legacy ausente")
class LegacyR2(unittest.TestCase):
    def test_corrupt_local_header_is_malformed(self):
        from traslado.legacy import extract
        data = bytearray((FIXTURES / "publico.odt").read_bytes())
        # Corrompe o nome do primeiro membro no cabeçalho local, mantendo o diretório central intacto.
        name_len = int.from_bytes(data[26:28], "little")
        data[30:30 + name_len] = b"X" * name_len
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "corrompido.odt"
            source.write_bytes(bytes(data))
            with self.assertRaises(ConversionError) as caught:
                extract(source, Path(raw), Options(legacy=True), Document(source.name, "a" * 64))
            self.assertEqual(caught.exception.code, "malformed")


if __name__ == "__main__":
    unittest.main()
