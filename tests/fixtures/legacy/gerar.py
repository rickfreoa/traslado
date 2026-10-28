"""Synthetic public-domain fixtures. The author dedicates these texts to CC0."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent


def archive(name, parts):
    with zipfile.ZipFile(ROOT / name, "w") as out:
        for key, value in parts.items():
            out.writestr(zipfile.ZipInfo(key, (2026, 1, 1, 0, 0, 0)), value.encode("utf-8"))


def generate():
    (ROOT / "publico.rtf").write_bytes(
        br"{\rtf1\ansi\ansicpg1252 A\'e7\'e3o inicial{\footnote Nota primeira.} continua.\par "
        br"Segundo trecho{\footnote Nota segunda.} termina.\par "
        br"\trowd\cellx2000\cellx4000\intbl Norte\cell Sul\cell\row "
        br"\pard Fim do relato.\par}")
    namespaces = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                  'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
                  'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
                  'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
                  'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"')
    def odf(name, family, body):
        content = f'<?xml version="1.0"?><office:document-content {namespaces} office:version="1.2"><office:body><office:{family}>{body}</office:{family}></office:body></office:document-content>'
        mime_family = {"text": "text", "spreadsheet": "spreadsheet", "presentation": "presentation"}[family]
        archive(name, {"mimetype": "application/vnd.oasis.opendocument." + mime_family,
                       "content.xml": content,
                       "META-INF/manifest.xml": '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"><manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.' + mime_family + '"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>'})
    def note(key, text, family="footnote"):
        return f'<text:note text:id="{key}" text:note-class="{family}"><text:note-citation>1</text:note-citation><text:note-body><text:p>{text}</text:p></text:note-body></text:note>'
    table = ('<table:table table:name="Dados"><table:table-column table:number-columns-repeated="2"/>'
             '<table:table-row><table:table-cell table:number-columns-spanned="2" office:value-type="string"><text:p>Norte unido</text:p></table:table-cell><table:covered-table-cell/></table:table-row>'
             '<table:table-row><table:table-cell office:value-type="string"><text:p>Leste</text:p></table:table-cell><table:table-cell office:value-type="string"><text:p>Oeste</text:p></table:table-cell></table:table-row></table:table>')
    odf("publico.odt", "text", '<text:h text:outline-level="1">Relato público</text:h><text:p>Ação inicial' + note('a', 'Nota primeira.') + ' continua.</text:p><text:p>Segundo trecho' + note('b', 'Nota final.', 'endnote') + ' termina.</text:p>' + table + '<text:list><text:list-item><text:p>Item inicial</text:p></text:list-item><text:list-item><text:p>Item final</text:p></text:list-item></text:list><text:p>Fim do relato.</text:p>')
    odf("publico.ods", "spreadsheet", table)
    odf("publico.odp", "presentation", '<draw:page draw:name="Página um"><draw:frame presentation:class="title"><draw:text-box><text:p>Relato público</text:p></draw:text-box></draw:frame><draw:frame presentation:class="outline"><draw:text-box><text:p>Ação inicial.</text:p><text:p>Fim do relato.</text:p></draw:text-box></draw:frame></draw:page>')
    archive("publico.epub", {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        "book.opf": '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">publico</dc:identifier><dc:title>Relato público</dc:title><dc:language>pt</dc:language></metadata><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>',
        "chapter.xhtml": '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Relato público</title></head><body><h1>Relato público</h1><p>Ação inicial<a href="https://example.org">link público</a> continua.</p><p>Fim do relato.</p></body></html>'})


if __name__ == "__main__":
    generate()
