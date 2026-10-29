# Política de licenças — Traslado 2.x

Ricardo Peres Freoa | Stocche Forbes Advogados · 28/10/2026 (revisão 4, aprovada com ressalvas pelo revisor independente e com as ressalvas incorporadas)

## 1. Regra

Da versão 2.0.0 em diante, o Traslado é distribuído sob a GNU Affero General Public License, versão 3 ou posterior (`AGPL-3.0-or-later`), texto integral no arquivo `LICENCE`. As versões 1.0.0 e 1.1.0 continuam sob MIT: quem as obteve pode seguir usando-as e redistribuindo-as sob MIT; só quem redistribui a 2.x fica sujeito à AGPL.

Premissa declarada: o titular, Ricardo Peres Freoa, afirma ser o único titular dos direitos sobre o código (não há cessão, cláusula de propriedade do escritório ou contribuição de terceiros; o código produzido com assistência de ferramentas de IA foi dirigido e revisado por ele). A troca de licença depende dessa premissa, não do histórico de commits, que não a prova; ela fica registrada como questão aberta do relatório da 2.0.0. O aviso de copyright nomeia a pessoa física (`Copyright (C) 2026 Ricardo Peres Freoa`), não o escritório. Os pacotes 6.0.0rc do Freoa Markdown Converter não foram publicados; o `ocr-cache` com binários existiu só no pacote local rc9 e não integra nenhum artefato da 2.x.

Todo arquivo de código distribuído (`.py`, `.ps1`, `.sh`, `.bat`, `.command`) traz o cabeçalho `SPDX-License-Identifier: AGPL-3.0-or-later` e o aviso de copyright. Todo artefato distribuído acompanha a licença e aponta o código-fonte correspondente: a árvore da tag `v<versão>` do repositório público `https://github.com/rickfreoa/traslado`, que inclui código, scripts de instalação, workflows de build e testes. O sdist é gerado dessa tag e publicado como asset da mesma release, por conveniência; contém `src/`, `scripts/`, `install.ps1`, `install.sh`, `pyproject.toml` e `LICENCE`, mas não substitui a árvore da tag como fonte correspondente. O `setup` e a janela "Sobre" exibem a licença e esse endereço.

## 2. Composição dos artefatos distribuídos

| Artefato | Conteúdo | Bytes de terceiros |
|---|---|---|
| wheel `traslado-<v>-py3-none-any.whl` | pacotes `traslado` e `freoa_v6` (sombra), metadados, `LICENCE` | nenhum |
| sdist `traslado-<v>.tar.gz` | código-fonte, testes, scripts, `LICENCE` | nenhum |
| ZIP `traslado-<v>.zip` | wheel, scripts de instalação, locks com hash, documentação, `LICENCE` | nenhum (o diretório `ocr-cache` não faz parte do ZIP) |
| `install.ps1`, `install.sh` | bootstrap da instalação, com cabeçalho SPDX e apontador para o fonte | nenhum |
| `SHA256SUMS`, `revoked.json` | hashes e estado de revogação | nenhum |

Ferramentas de build (setuptools, wheel) e de teste (markdown-it-py, mdurl, MIT) não integram nenhum artefato de runtime.

## 3. Componentes obtidos pelo instalador no computador de quem instala

O instalador baixa esses componentes das fontes oficiais, com versão e SHA-256 fixados, e os grava numa pasta privada da instalação. O Traslado não os inclui em seus artefatos, mas o instalador, ao copiá-los para a máquina do usuário, participa da cadeia de entrega; por isso a política registra, para cada um, a licença e onde está o fonte correspondente. Para as bibliotecas Python, o fonte correspondente é o sdist da mesma versão no PyPI (`https://pypi.org/project/<nome>/<versão>/#files`), ou, sem sdist, a tag do projeto. As bibliotecas nativas embutidas nos wheels de terceiros (por exemplo freetype, libjpeg, zlib, libxml2, libxslt, libiconv no Pillow, no lxml e no PyMuPDF) seguem as licenças próprias, declaradas pelos mantenedores desses wheels; seu inventário individual é lacuna declarada.

### 3.1 Bibliotecas combinadas ao Traslado no mesmo processo

A compatibilidade com a AGPLv3 importa aqui, porque o conjunto em execução é uma obra combinada.

| Componente | Licença (SPDX) | Compatível com a AGPLv3 |
|---|---|---|
| PyMuPDF 1.28.2 (MuPDF) | AGPL-3.0 ou licença comercial da Artifex; o ramo exato (`AGPL-3.0-only` ou `AGPL-3.0-or-later`) é conferido no `COPYING` da versão 1.28.2 ao regenerar o inventário e registrado em `license_selected`. O Traslado adota o ramo AGPL; ninguém deve presumir licença comercial | Sim. Se o ramo for `AGPL-3.0-only`, a obra combinada só pode ser distribuída sob a versão 3 e o "ou posterior" do Traslado não se exerce sobre o conjunto. O Traslado não distribui o PyMuPDF; quem monta a combinação é a instalação no computador do usuário, e quem redistribuir o conjunto deve cumprir a AGPL-3.0 |
| Pillow 12.3.0 | `MIT-CMU` (o THIRD-PARTY.md regenerado usa o mesmo identificador) | Permissiva do tipo HPND/MIT, sem restrição incompatível com a AGPL |
| python-docx 1.2.0, openpyxl 3.1.5, python-pptx 1.0.2, et-xmlfile 2.0.0 | `MIT` | Sim |
| lxml 6.1.3 | `BSD-3-Clause` | Sim |
| XlsxWriter 3.2.9 | `BSD-2-Clause` | Sim |
| typing-extensions 4.16.0 | `PSF-2.0` | Sim (a FSF lista a licença da Python Software Foundation, no texto atual, como compatível com a GPL) |
| firecrawl-anydoc e transitivas (extra `legacy`) | a fixar no lock e no inventário quando o extra entrar (E4) | Só entra se a licença de cada pacote estiver na allowlist da seção 5 |

### 3.2 Programas separados

Executados como processos próprios, sem vínculo no mesmo processo: a compatibilidade de licença com a AGPL não é o critério; o critério é cumprir as condições de cada programa ao entregá-lo.

| Componente | Licença | Condições e fonte |
|---|---|---|
| Tesseract 5.5.3 (Windows) | `Apache-2.0` | Licença e avisos preservados em `ocr/doc/`; fonte em `github.com/tesseract-ocr/tesseract` na tag `5.5.3` |
| DLLs do binário Windows do Tesseract (55 arquivos fixados por nome, tamanho e hash no inventário) | `libtesseract-5.dll`: Apache-2.0, auditada; as outras 54 (Leptonica, ICU, runtimes GCC, bibliotecas de imagem e rede) com licenças próprias, não auditadas individualmente | Desvio assumido do §8 do plano ("compatibilidade de cada dependência"): exceção nominal da seção 5, justificada por serem bytes idênticos aos do instalador oficial e por rodarem em programa separado; fonte de referência: o artefato e a tag fixados em `ocr-assets.lock.json` (release oficial `tesseract-ocr/tesseract` 5.5.3) e os projetos de origem identificados no inventário |
| Modelos `tessdata_fast` | `Apache-2.0` | Licença em `ocr/doc/TESSDATA-LICENSE.txt` |
| Tesseract no Linux e no macOS | do pacote do sistema ou do Homebrew | Instalado pelo usuário pelo gerenciador do sistema; o Traslado só o localiza |
| 7-Zip 26.03 (`7z.exe`, `7z.dll`, `7zr.exe`), só no Windows e só durante a instalação | `LGPL-2.1-or-later` com trechos BSD e a restrição unRAR (o código unRAR não pode ser usado para recriar o compressor RAR); `7zr` conforme `ocr/doc/7ZIP-LICENSE.txt` (cópia do `DOC/License.txt` da tag 26.03, conferido pelo item 3 do gate) | O Traslado não vincula nem embute `7z.dll`; o 7-Zip é extraído num diretório temporário, usado só para extrair o Tesseract e removido ao fim; não permanece na instalação (a E5 testa isso); licença preservada em `ocr/doc/7ZIP-LICENSE.txt`; fonte em `github.com/ip7z/7zip` na tag `26.03` |
| CPython gerenciado (python-build-standalone) e uv | `PSF-2.0` (com Tcl/Tk, OpenSSL, expat e demais componentes embutidos sob suas licenças); uv `MIT OR Apache-2.0` | Interpretador e instalador, programas separados; licenças nos próprios pacotes |

## 4. Efeitos fora do Traslado

- A Biblioteca chama o Traslado por processo separado e não é distribuída; a AGPL não a alcança.
- A AGPL não proíbe uso comercial por terceiros. Distinguem-se duas obrigações: (a) quem **distribui** o Traslado, modificado ou não, deve entregar ou oferecer o código-fonte correspondente sob a mesma licença (§§ 4-6); (b) quem **modifica** o Traslado e permite que usuários interajam com a versão modificada por rede deve oferecer a esses usuários o fonte dessa versão (§ 13). O § 13 não alcança quem só usa a versão não modificada.
- Se o Freoa IA System vier a ser distribuído com o Traslado embutido ou vinculado no mesmo processo, a questão exige análise própria.

## 5. Gate de licença (E8)

Verificações automáticas, todas com saída de erro e bloqueio da release:

1. **Allowlist.** `licenses-allowlist.json` (versionado) lista os identificadores SPDX admitidos para bibliotecas combinadas: `AGPL-3.0-only`, `AGPL-3.0-or-later`, `GPL-3.0-or-later`, `LGPL-2.1-or-later`, `LGPL-3.0-or-later`, `MIT`, `MIT-CMU`, `BSD-2-Clause`, `BSD-3-Clause`, `Apache-2.0`, `PSF-2.0`, `ISC`, `Zlib`, `Unicode-3.0`, `Apache-2.0 WITH LLVM-exception`, `0BSD`, `Unlicense`, `BSL-1.0`, `Unicode-DFS-2016`. Os sete últimos vêm dos crates Rust embutidos no wheel do anydoc (SBOM CycloneDX no próprio wheel), são permissivos e compatíveis com a AGPLv3.
2. **Inventário.** Para cada pacote de todos os locks de runtime com hash (`requirements-*.hashes.lock`, um por plataforma e perfil, inclusive `legacy`), `THIRD-PARTY-INVENTORY.json` deve ter entrada com versão igual à do lock e `license_expression` SPDX não nulo. Expressão com `OR` passa se ao menos um ramo estiver na allowlist e o ramo adotado constar em `license_selected` (PyMuPDF: `AGPL-3.0-only`). Expressão com `AND` exige todos os ramos na allowlist. `scripts/inventario.py` aplica essas regras.
3. **Exceções nominais.** No próprio inventário, com caminho, hash, motivo e data de revisão: as 54 DLLs nativas não auditadas do Tesseract Windows. Exceção vencida reprova. Componentes não Python (Tesseract, tessdata, 7-Zip, uv, CPython gerenciado) devem constar do inventário com versão, URL e SHA-256 iguais aos de `ocr-assets.lock.json` e dos scripts de instalação, e com o arquivo de licença preservado quando o instalador o grava; o `License.txt` do 7-Zip 26.03 é conferido por hash contra o da tag.
4. **Composição.** `scripts/build_release.py --verificar` lista os membros de wheel, sdist e ZIP e falha se algum não estiver na lista esperada: `release-files.txt`, mais os membros gerados enumerados no próprio script (`INSTALL-PAYLOAD.json`, `dist/traslado-<v>-py3-none-any.whl`, `PKG-INFO` e metadados do sdist e do wheel). Falha também se algum membro tiver nome que case com `fitz`, `pymupdf` ou `mupdf` (sem distinção de caixa) ou bytes com as assinaturas de MuPDF já verificadas pelo script.
5. **Licença, cabeçalhos e apontador.** `LICENCE` idêntico ao texto canônico da AGPLv3 (sha256 `8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef`) no wheel, no sdist e no ZIP; cabeçalho SPDX em todo `.py`, `.ps1`, `.sh`, `.bat` e `.command` distribuído; `METADATA` do wheel e `PKG-INFO` do sdist com `License-Expression: AGPL-3.0-or-later` e `Project-URL` de fonte para `https://github.com/rickfreoa/traslado`; `pyproject.toml` com `license` e `license-files` correspondentes; `install.ps1`, `install.sh` e o README do ZIP contendo `https://github.com/rickfreoa/traslado/tree/v<versão>`.
6. **Documentação.** `THIRD-PARTY.md` e o README contêm a linha exata `Licença do código próprio: AGPL-3.0-or-later`; `THIRD-PARTY.md` e o inventário declaram a mesma versão que `traslado.__version__`; a ocorrência de `6.0.0rc` em `THIRD-PARTY.md` ou no inventário reprova.
7. **Fonte correspondente.** O fluxo de release só cria o rascunho a partir de tag `v<versão>` existente; o sdist é gerado dessa tag e publicado na mesma release; as notas apontam `tree/v<versão>`.
