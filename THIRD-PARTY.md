# Dependências e licenças — 2.0.0

Licença do código próprio: AGPL-3.0-or-later

Este arquivo descreve dependências do projeto; não declara que toda redistribuição seja autorizada ou que um subprocesso elimine obrigações de licença. A licença `AGPL-3.0-or-later` declarada para o código próprio do Traslado 2.0.0 não substitui as licenças de bibliotecas, executáveis e modelos de terceiros. Confirme as licenças nos artefatos resolvidos antes de distribuir o conjunto.

| Componente | Uso/perfil | Fonte e condição observada |
|---|---|---|
| Traslado 2.0.0 | Código de autoria própria | `AGPL-3.0-or-later` (`LICENCE`, SHA-256 `8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef`). |
| Python / Tcl-Tk | Ambiente e interface local | [Python](https://www.python.org/about/legal/) (`PSF-2.0`) e [Tcl/Tk](https://www.tcl-lang.org/software/tcltk/license.html) (`TCL`). |
| PyMuPDF 1.28.2 | PDF, Full ou pdf | [Licenciamento oficial](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright): `AGPL-3.0-only` ou licença comercial Artifex (o `COPYING` da 1.28.2 não traz "ou posterior"; ramo adotado `AGPL-3.0-only`, de modo que quem redistribuir a combinação a distribui sob a AGPL-3.0). |
| Pillow 12.3.0 | Imagens/PDF, extras | [Licença MIT-CMU / HPND e notices](https://github.com/python-pillow/Pillow/blob/main/LICENSE). |
| lxml 6.1.3 | XML/Office, Full | `BSD-3-Clause`. |
| python-docx 1.2.0 | DOCX, Office/Full | [MIT](https://github.com/python-openxml/python-docx/blob/master/LICENSE). |
| openpyxl 3.1.5 | XLSX, Office/Full | [MIT](https://openpyxl.readthedocs.io/en/stable/). |
| python-pptx 1.0.2 | PPTX, Office/Full | [MIT](https://github.com/scanny/python-pptx/blob/master/LICENSE). |
| et-xmlfile 2.0.0 | XLSX, Office/Full | `MIT`. |
| typing-extensions 4.16.0 | Tipagem, Office/Full | `PSF-2.0`. |
| XlsxWriter 3.2.9 | PPTX/XLSX, Office/Full | `BSD-2-Clause`. |
| Tesseract 5.5.3.20260724 x64 | OCR local automático no perfil Full Windows | Binário do [release oficial](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3), `Apache-2.0` para o código Tesseract. O bootstrap extrai os arquivos para a versão privada do app; não executa o instalador NSIS nem o desinstalador do Tesseract compartilhado. |
| tessdata_fast | Modelos por, eng, spa, ita, fra, deu | Commit `87416418657359cb625c412a48b6e1d6d41c29bd`, seis hashes SHA256 em `ocr-assets.lock.json`. [README e licença do repositório](https://github.com/tesseract-ocr/tessdata_fast/tree/87416418657359cb625c412a48b6e1d6d41c29bd): `Apache-2.0`, modelos LSTM para Tesseract 4/5; não suportam engine legado OEM 0/2. Licença preservada em `ocr/doc/TESSDATA-LICENSE.txt`. |
| 7-Zip 26.03 e 7zr | Extração portátil durante instalação | [Release](https://github.com/ip7z/7zip/releases/tag/26.03) e [licenças primárias](https://github.com/ip7z/7zip/blob/26.03/DOC/License.txt): 7z/7z.dll incluem `LGPL-2.1-or-later`, trechos `BSD-3-Clause` e restrições unRAR; 7zr reduzido declara domínio público. O bootstrap usa arquivos extraídos e preserva `ocr/doc/7ZIP-LICENSE.txt`; não executa o instalador 7-Zip. |
| DLLs do binário Windows OCR | Dependências nativas do release Tesseract | 55 DLLs selecionadas do arquivo oficial, com nome/tamanho/SHA256 no lock e inventário. Incluem Leptonica, runtimes GCC, ICU e bibliotecas de imagem/rede. A licença do Tesseract não cobre automaticamente essas bibliotecas; ver exceção nominal e limitações abaixo. |
| setuptools / wheel | Ferramentas de build | Versões fixadas no `pyproject.toml`; dependências transitivas/metadata de instalação exigem inventário do ambiente resolvido. |

O projeto não distribui MinerU, DocVortex, PaddleOCR, Torch, VLM ou modelos de visão. O MinerU inspecionado foi versão 4.0.10, commit `ed50cc15bc2c9bfb00520dadfe61979866e62236`. Sua [licença própria](https://github.com/opendatalab/MinerU/blob/ed50cc15bc2c9bfb00520dadfe61979866e62236/LICENSE.md) é baseada em Apache 2.0 com termos adicionais, incluindo limiares comerciais, atribuição em serviços online e término por descumprimento. Não deve ser descrita como Apache 2.0 puro. Os modelos associados e o motor externo DocVortex não foram incorporados; revisão de seus pesos/licenças permanece pendente para um adaptador futuro.

Links remetem às fontes primárias. `requirements-full.hashes.lock` (e `locks/requirements-full.hashes.lock`) fixa nove dependências Python do runtime Full e seus hashes PyPI. Separadamente, `ocr-assets.lock.json` fixa dez downloads OCR/extrator/licença, os membros extraídos e os seis modelos. Os bytes do executável Tesseract e do arquivo 7-Zip coincidiram com os digests dos releases oficiais e com manifests independentes do Microsoft winget. Os SHA256 dos modelos foram calculados sobre downloads do commit fixo e confrontados com seus IDs de blob Git oficiais; não se afirma existir um segundo checksum SHA256 publicado dos modelos.

O instalador Tesseract SHA256 é `bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4`; o archive 7-Zip x64 é `0859c524b8a63551848f0c246abddcb1d0b7b656b0fbfe879f8d85e61a9e6edd`; 7zr é `ad4c82fadcbdf93c03b4fc440f300509c7d60c5c2f4d183e35d9d70d6957037d`. Proveniência independente fixada: [winget Tesseract, commit 3c56bb3](https://raw.githubusercontent.com/microsoft/winget-pkgs/3c56bb363c7700349f1d7afc4ffb26c24d5f061a/manifests/t/tesseract-ocr/tesseract/5.5.3/tesseract-ocr.tesseract.installer.yaml) e [winget 7-Zip, commit c23c145](https://raw.githubusercontent.com/microsoft/winget-pkgs/c23c145e9295dfd42541b28a996da7e23ffb0029/manifests/7/7zip/7zip/26.03/7zip.7zip.installer.yaml).

São controles de identidade, não auditoria integral de licenças. O cache redistribuído contém binários oficiais originais e pesos; preservação de atribuições, licenças, notices e obrigações de acesso a fontes deve acompanhar redistribuição. O [manual libstdc++](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html), por exemplo, descreve GPL com exceção de runtime. A identificação das demais famílias por nome de DLL não autentica a revisão exata de cada componente compilado. O inventário registra essa cobertura como parcial, com revisão de licenças nativas/transitivas e fontes correspondentes registrada como exceção nominal com data de revisão em `licenses-allowlist.json`.

As ferramentas de build têm somente versões exatas no `pyproject.toml`. Python, Tcl/Tk, builds, DLLs transitivas das wheels e todas as revisões/fontes nativas não têm SBOM completo nesta versão 2.0.0. A extração privada por subprocesso não elimina obrigações AGPL/comerciais do PyMuPDF, nem os termos específicos do MinerU e de seus modelos.

## Dependências exclusivas de testes

`markdown-it-py==4.0.0`, `mdurl==0.1.2` e `setuptools==82.0.1` têm licença `MIT` conforme os arquivos LICENSE das distribuições usadas na validação; o `setuptools` serve só ao teste do emissor de revogação instalado sem isolamento de build (PEP 639 exige 77 ou posterior). O extra `test` e `requirements-test.hashes.lock` os declaram explicitamente; não são instalados no perfil Full pelo bootstrap Windows. Os artefatos de teste não são bibliotecas do runtime.

## Lacunas declaradas (2.0.0)

`scripts/inventario.py` confronta `THIRD-PARTY-INVENTORY.json` e `licenses-allowlist.json` com os locks Python (`requirements-full.lock`, `requirements-full.hashes.lock`, `locks/requirements-legacy.hashes.lock`), `ocr-assets.lock.json`, os membros extraídos pelo bootstrap, as DLLs e os modelos, e falha se uma declaração não bater com outra (nome, versão, hash, tamanho, URL, campo de licença presente e permitido na allowlist SPDX). A cobertura é **consistência entre declarações** mais os controles que o bootstrap aplica na instalação (tamanho e SHA-256 do lock, origem HTTPS permitida, extração privada, conferência dos membros extraídos) e `pip --require-hashes`. Não lê os arquivos instalados nem seus textos de licença, e não consome recibos de instalação; ver a primeira lacuna. O que segue é lacuna conhecida, declarada em `"lacunas"` no inventário e nunca silenciada:

- **Sem recibos de instalação.** Nada confronta, no computador do usuário, os bytes efetivamente instalados (DLLs, modelos, textos de licença, wheels de terceiros) com o inventário; `ocr-runtime.json` guarda versão do motor, idiomas e hash do lock, não os hashes por membro. Adulterar um arquivo instalado ou seu texto de licença não é detectado pelo `scripts/inventario.py`.
- **OCR só em windows-x64.** `ocr-assets.lock.json` cobre apenas `windows-x64`. Em Linux e macOS não há lock: o CI Linux/macOS Full instala o Tesseract do pacote do sistema (`apt` / `brew`), registra versão e idiomas no log e falha se o motor faltar; o binário e os modelos do sistema não têm hash fixado nem entram neste inventário.
- **DLLs nativas.** 54 das 55 DLLs do Tesseract Windows têm hash e origem verificados, mas licença e fontes correspondentes não auditadas individualmente (exceção nominal com data de revisão em `licenses-allowlist.json`).
- **Perfil Python histórico.** A resolução inventariada é a de CPython 3.14.6 em Windows; 3.11 a 3.13 e Linux/macOS usam os mesmos locks com hash, sem inventário próprio.
- **PyMuPDF/MuPDF.** Nenhum ZIP ou wheel distribuído contém seus bytes (verificado no build e no workflow de release); o extra Full o instala no ambiente do usuário sob `AGPL-3.0-only`.
- **Sem SBOM completo** para interpretador, Tcl/Tk, ferramentas de build e bibliotecas nativas embutidas em wheels de terceiros.

### Motor legado opcional

`firecrawl-anydoc` **0.2.4**, licença **MIT**, extra `legacy` (`locks/requirements-legacy.hashes.lock`). A conversão usa somente `anydoc._anydoc`, localmente, sem OCR hospedado. Os crates Rust embutidos e suas licenças estão no SBOM CycloneDX do próprio wheel: `firecrawl_anydoc-0.2.4.dist-info/sboms/anydoc-python.cyclonedx.json`.
