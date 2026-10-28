# Política de licenças — Traslado 2.x

Ricardo Peres Freoa | Stocche Forbes Advogados · 28/10/2026 (revisão 2)

## 1. Regra

Da versão 2.0.0 em diante, o Traslado é distribuído sob a GNU Affero General Public License, versão 3 ou posterior (`AGPL-3.0-or-later`), texto integral no arquivo `LICENCE`. As versões 1.0.0 e 1.1.0 continuam sob MIT: quem as obteve pode seguir usando-as e redistribuindo-as sob MIT; só quem redistribui a 2.x fica sujeito à AGPL.

Premissa declarada: o titular afirma ser o único titular dos direitos sobre o código (todos os commits são seus e não há cessão, cláusula de propriedade de empregador ou contribuição de terceiros). A troca de licença depende dessa premissa, não do histórico de commits, que não a prova.

Todo arquivo de código em `src/` e `scripts/` traz o cabeçalho `SPDX-License-Identifier: AGPL-3.0-or-later` e o aviso de copyright. Todo artefato distribuído acompanha a licença e aponta o código-fonte correspondente: a tag `v<versão>` do repositório público `https://github.com/rickfreoa/traslado`, cujo conteúdo é idêntico ao sdist da mesma release, e o próprio sdist, publicado junto. O `setup` e a janela "Sobre" exibem a licença e esse endereço.

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

O instalador baixa esses componentes das fontes oficiais, com versão e SHA-256 fixados, e os grava numa pasta privada da instalação. O Traslado não os inclui em seus artefatos, mas o instalador, ao copiá-los para a máquina do usuário, participa da cadeia de entrega; por isso a política registra, para cada um, a licença e onde está o fonte correspondente.

### 3.1 Bibliotecas combinadas ao Traslado no mesmo processo

A compatibilidade com a AGPLv3 importa aqui, porque o conjunto em execução é uma obra combinada.

| Componente | Licença (SPDX) | Compatível com a AGPLv3 |
|---|---|---|
| PyMuPDF 1.28.2 (MuPDF) | `AGPL-3.0-only` ou comercial (Artifex). O Traslado adota a opção AGPL; ninguém deve presumir licença comercial | Sim. A AGPL do PyMuPDF só é atendida porque o Traslado também é AGPL e seu fonte é público |
| Pillow 12.3.0 | `MIT-CMU` | Sim (permissiva) |
| python-docx 1.2.0, openpyxl 3.1.5, python-pptx 1.0.2, et-xmlfile 2.0.0 | `MIT` | Sim |
| lxml 6.1.3 | `BSD-3-Clause` | Sim |
| XlsxWriter 3.2.9 | `BSD-2-Clause` | Sim |
| typing-extensions 4.16.0 | `PSF-2.0` | Sim (a FSF classifica a licença PSF das versões 2.0.1 em diante como compatível com a GPL) |
| firecrawl-anydoc e transitivas (extra `legacy`) | a fixar no lock e no inventário quando o extra entrar (E4) | Só entra se a licença de cada pacote estiver na allowlist da seção 5 |

### 3.2 Programas separados

Executados como processos próprios, sem vínculo no mesmo processo: a compatibilidade de licença com a AGPL não é o critério; o critério é cumprir as condições de cada programa ao entregá-lo.

| Componente | Licença | Condições e fonte |
|---|---|---|
| Tesseract 5.5.3 (Windows) | `Apache-2.0` | Licença e avisos preservados em `ocr/doc/`; fonte em `github.com/tesseract-ocr/tesseract` na tag `5.5.3` |
| DLLs do binário Windows do Tesseract (Leptonica, ICU, runtimes GCC, bibliotecas de imagem e rede; 55 arquivos fixados por hash) | licenças próprias; 54 não auditadas individualmente | Exceção nominal da seção 5; fontes no instalador oficial UB-Mannheim e nos projetos de origem |
| Modelos `tessdata_fast` | `Apache-2.0` | Licença em `ocr/doc/TESSDATA-LICENSE.txt` |
| Tesseract no Linux e no macOS | do pacote do sistema ou do Homebrew | Instalado pelo usuário pelo gerenciador do sistema; o Traslado só o localiza |
| 7-Zip 26.03 (`7z.exe`, `7z.dll`, `7zr.exe`), só no Windows e só durante a instalação | `LGPL-2.1-or-later` com trechos BSD e a restrição unRAR; `7zr` domínio público | O Traslado não vincula nem embute `7z.dll`; o código unRAR, incompatível com a GPL se combinado, fica num programa separado; licença preservada em `ocr/doc/7ZIP-LICENSE.txt`; fonte em `github.com/ip7z/7zip` na tag `26.03` |
| CPython gerenciado (python-build-standalone) e uv | `PSF-2.0` (com Tcl/Tk, OpenSSL, expat e demais componentes embutidos sob suas licenças); uv `MIT OR Apache-2.0` | Interpretador e instalador, programas separados; licenças nos próprios pacotes |

## 4. Efeitos fora do Traslado

- A Biblioteca chama o Traslado por processo separado e não é distribuída; a AGPL não a alcança.
- A AGPL não proíbe uso comercial por terceiros. Distinguem-se duas obrigações: (a) quem **distribui** o Traslado, modificado ou não, deve entregar ou oferecer o código-fonte correspondente sob a mesma licença (§§ 4-6); (b) quem **modifica** o Traslado e permite que usuários interajam com a versão modificada por rede deve oferecer a esses usuários o fonte dessa versão (§ 13). O § 13 não alcança quem só usa a versão não modificada.
- Se o Freoa IA System vier a ser distribuído com o Traslado embutido ou vinculado no mesmo processo, a questão exige análise própria.

## 5. Gate de licença (E8)

Verificações automáticas, todas com saída de erro e bloqueio da release:

1. `licenses-allowlist.json` (versionado) lista os identificadores SPDX admitidos para bibliotecas combinadas: `AGPL-3.0-only`, `AGPL-3.0-or-later`, `GPL-3.0-or-later`, `LGPL-2.1-or-later`, `LGPL-3.0-or-later`, `MIT`, `MIT-CMU`, `BSD-2-Clause`, `BSD-3-Clause`, `Apache-2.0`, `PSF-2.0`, `ISC`, `Zlib`, `Unicode-3.0`, `Apache-2.0 WITH LLVM-exception`, `0BSD`, `Unlicense`, `BSL-1.0`, `Unicode-DFS-2016`. Os sete identificadores acrescentados vêm dos crates Rust embutidos no wheel do anydoc (SBOM CycloneDX no próprio wheel) e são permissivos e compatíveis com a AGPLv3. `scripts/inventario.py` falha se alguma dependência dos locks de runtime (Full e `legacy`) não estiver no inventário ou tiver licença fora da allowlist.
2. Exceções nominais, no próprio inventário, com motivo e data de revisão: as 54 DLLs nativas do Tesseract Windows não auditadas individualmente (programa separado, bytes iguais aos do instalador oficial). Exceção vencida reprova.
3. `scripts/build_release.py --verificar` lista os membros de wheel, sdist e ZIP e falha se algum não estiver na lista de membros esperados (`release-files.txt` mais os gerados) ou se contiver bytes de PyMuPDF/MuPDF; confere `LICENCE` idêntico ao texto canônico da AGPLv3 (sha256 `8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef`) no wheel, no sdist e no ZIP; confere o cabeçalho SPDX em todo `.py` e `.ps1`/`.sh` distribuído.
4. O fluxo de release só cria o rascunho a partir de tag `v<versão>` existente cujo commit gerou o sdist publicado, e as notas de release apontam `tree/v<versão>`.

`THIRD-PARTY.md` e `THIRD-PARTY-INVENTORY.json` são regenerados para a 2.0.0 antes deste gate; enquanto descreverem a 6.0.0rc ou a licença MIT do código próprio, o gate reprova.
