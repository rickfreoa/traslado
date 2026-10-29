# Histórico de versões

Ricardo Peres Freoa | Stocche Forbes Advogados

## 2.0.0

- **Licença**: código próprio sob `AGPL-3.0-or-later` (`LICENCE`), cabeçalhos SPDX em todos os fontes; `THIRD-PARTY.md`, inventário e política de licenças (`LICENSE-POLICY.md`) atualizados.
- **Instalação sem administrador e sem Python prévio** em Windows, Linux e macOS (one-liner, atalho clicável ou `traslado setup`): uv e Python 3.13 gerenciados e fixados por SHA-256, dependências com `--require-hashes`, release candidata em `releases/<id>/` promovida atomicamente a `current.json` só com doctor, fixture e revogação aprovados; `-NoPromote`/`--no-promote` qualifica a candidata lado a lado sem promover.
- **Autenticidade em dois canais**: `SHA256SUMS` e GitHub Attestations do `release.yml`; no modo remoto, digests conferidos na API de releases antes de instalar qualquer coisa (sem exigir Python no sistema); revogação por `revoked.json` atestado, com sequência monotônica e validade de 14 dias. Exceção offline só explícita (`--allow-offline-promotion` com `TRASLADO_PROMOCAO_OFFLINE=rascunho|teste`).
- **Ciclo de vida pelo CLI**: `where`, `update`, `rollback`, `uninstall` (preserva sempre `data_root`; no Windows, o que o próprio processo prende é removido logo após a saída dele), `shortcuts`.
- **CI** em Windows, Ubuntu e macOS, Python 3.11 a 3.14, perfis Core, Full e legacy contra o wheel instalado; mutação, contenção, E2E em contêineres Debian e Fedora mínimos, Windows e macOS, auditoria das bibliotecas dinâmicas carregadas; release em rascunho com bytes idênticos aos testados no CI; PyPI por trusted publishing só após publicação manual.
- **GUI**: prévia quebra visualmente sequências longas sem espaço (o texto não muda).
- Limite conhecido: no Windows Server Core a extensão nativa do PyMuPDF não carrega (DLL de sistema ausente); a instalação falha fechada, sem promover.

- Extra `legacy` com anydoc 0.2.4 para DOC, RTF, ODT, ODS, ODP, PPT e EPUB,
  somente com ativação explícita no CLI/TOML. XLS permanece não suportado.
- Adaptador local no worker contido, erros tipados, notas, tabelas e proveniência
  experimental em documento/manifesto; doctor informa disponibilidade.
- Fixtures sintéticas públicas, gabaritos estruturais e mutações determinísticas.
  A GUI ainda não oferece seleção do motor legado.
- Detecção pelo conteúdo para PDF, OOXML, imagens cadastradas e HTML; extensão divergente gera `format_extension_mismatch`, com despacho pelo formato detectado. OLE2, RTF, ODF e EPUB são identificados para diagnóstico e recusados pelo perfil padrão.
- Erros tipados no worker, no resultado por arquivo e no stderr do CLI, com `error_pages` para OCR indisponível; códigos de saída e mensagens anteriores preservados.
- Distribuição e módulo `traslado`, com aliases de módulo `freoa_v6` na série 2.x.
- Comandos `traslado` e `traslado-gui`; aliases `freoa-md` e `freoa-md-gui` mantidos.
- Artefatos e avisos de conversão preservados; doctor informa o módulo.

## 1.1.0

### Conversão
- **DOCX: notas de rodapé e de fim** como notas Markdown (`[^n]`), ligadas às definições de `footnotes.xml` e `endnotes.xml`, com famílias distintas e separadores ignorados. Vale também para `.doc`, `.rtf` e `.odt` que chegam como `.docx`. Avisos por espécie de perda remanescente substituem o aviso agregado da 1.0.0: `docx_note_reference_unresolved`, `docx_note_definition_unreferenced`, `docx_note_duplicate_or_missing_id`, `docx_note_complex_content_flattened`, `docx_headers_footers_not_extracted`, `docx_comments_not_extracted`.
- **OCR com geometria**: um bloco por parágrafo do Tesseract, com caixa em coordenadas da folha (escala, origem e rotação do raster), confiança média por bloco e sequência de palavras idêntica à do TXT da mesma execução; divergência recai no texto sem geometria, com aviso `ocr_geometry_fallback`. O TSV é lido sem tratamento de aspas, e palavra sem escore conserva a caixa e fica fora da média. Efeito no Markdown das páginas com OCR: mesmas palavras, um parágrafo por linha.
- **PDF nativo: linhas e spans** (caixa, tamanho de fonte, flags) sob os blocos, só em `document-layout.json`; o refino avançado não recebe essa geometria.
- **Mobiliário de página** (`furniture = "mark"`, padrão; `--furniture off|mark`): cabeçalhos corridos, rodapés e fólios classificados com `role: furniture` nos chunks, nas faixas de borda da página exibida; romanos só quando canônicos. Nenhum texto é omitido.
- **Folha escaneada com carimbo**: imagem única cobrindo ao menos metade da folha, com pouca área de texto visível ou camada invisível, vai a OCR.
- **OCR concorrente** por folha (`ocr_workers`, 1 a 4, padrão 4; `--ocr-workers`), com fila limitada, ordem determinística e resultado idêntico ao serial. Em cinco obras escaneadas (18 a 134 folhas), o tempo total caiu de 833 s para 321 s. O Tesseract roda com `OMP_THREAD_LIMIT=1`, na sessão do worker, encerrado com ele.
- **Registro único de conversores**: despacho por extensão, descoberta e `doctor` leem o mesmo cadastro; indisponibilidade vira recusa com motivo.

### Distribuição
- Versão única em `src/freoa_v6/__init__.py`; `converter_version` do documento deriva dela.
- CI em Windows e Linux, perfis Core e Full, Python 3.11 a 3.14; release por tag cria apenas rascunho, depois da matriz verde no mesmo commit.
- Seleção explícita dos arquivos distribuídos (`release-files.txt`), wheel construído da seleção, ZIP determinístico, `SHA256SUMS`, attestation e varredura de bytes de PyMuPDF/MuPDF no pacote.
- Inventário de terceiros confrontado com locks, downloads, membros extraídos, modelos e licenças.
- Windows: busca do Tesseract no PATH sem `shutil.which` (PATHEXT vazio usa o padrão; entradas relativas e o diretório corrente excluídos).

### Fora desta versão
- Ordem de leitura por calhas (`layout = gutters`): nas folhas de referência, as folhas fora de ordem passaram de 20 para 30. Não incluída.
- Omissão de mobiliário (`furniture = omit`): exige mapa de paginação independente do texto.
- Licença do perfil Full (PyMuPDF): questão aberta; consulte THIRD-PARTY.md.

## 1.0.0

Primeira versão pública (antes Freoa Markdown Converter 6.0.0rc9). Ver a seção "Novidades da 1.0.0" do README.
