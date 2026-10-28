"""Generate the original Traslado document/arrow icon using only stdlib."""
from pathlib import Path
import struct
import zlib


def generate(path):
    rows = []
    for y in range(256):
        row = bytearray()
        for x in range(256):
            color = (24, 55, 78, 255)
            if 58 <= x < 186 and 32 <= y < 224:
                color = (239, 246, 250, 255)
            if 78 <= x < 164 and any(a <= y < a + 7 for a in (64, 86, 108)):
                color = (24, 55, 78, 255)
            if (82 <= x < 172 and 158 <= y < 174) or (150 <= x < 196 and abs(y - 166) <= (196 - x)):
                color = (0, 154, 144, 255)
            row.extend(color)
        rows.append(b'\0' + row)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    payload = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 256, 8, 6, 0, 0, 0))
    payload += chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b'')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


if __name__ == '__main__':
    generate(Path(__file__).resolve().parents[1] / 'src/traslado/assets/traslado.png')
