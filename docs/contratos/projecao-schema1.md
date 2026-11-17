# Contrato da projeção `--schema 1` (3.x)

Ricardo Peres Freoa | Stocche Forbes Advogados · 8/11/2026 · Congelado na E0 da 3.0.0.

## 1. Objeto

`traslado convert --schema 1` gera, a partir do modelo v2, os artefatos do contrato anterior (`document.md`, `document.json`, `document-layout.json`, `manifest.json`, `chunks/chunks.jsonl`, `chunks/NNNN.md`, `LEIA-AVISOS.txt`), em toda a série 3.x. É o único caminho pelo qual a Biblioteca recebe saída da 3.x até a campanha promover cada lote.

Em produção, `--schema 1` usa o motor `classic` até a promoção por lote (plano §4.4): o que a Biblioteca recebe só muda obra a obra, sob gate.

## 2. Critério de identidade (gate da E2)

Sobre o motor `classic`, na amostra congelada: `document.md` **byte a byte idêntico** à 2.0 e `chunks.jsonl` idêntico salvo os campos neutralizados declarados: `document.json.converter_version`, `duration_seconds`, `options.tesseract`, `options.tessdata`; `manifest.json.converter` e os `sha256`/`bytes` decorrentes. Linha de base: `C:\Traslado\wt30\metricas\c0\c0-v200-resumo.json`, 80 obras.

## 3. Invariantes que a Biblioteca exige (levantados por varredura do código)

| Invariante | Consequência de violar |
|---|---|
| `document.md[char_start:char_end] == text`, fatia literal, em pontos de código, sobre os bytes do arquivo com quebra LF | obra `chunks-divergentes`, inapta |
| `index` inteiro, não booleano, único, e linha k com `index` = k | quebra do caminho rápido da busca |
| `text`, `char_start`, `char_end` presentes; `pages`, `blocks`, `bbox` lidos tolerantemente | |
| Em chunk com `pages`, `blocks` presente, com `page`, `char_start`, `char_end`, `bbox` lido por posição | sem `blocks`, a paginação da obra zera sem erro |
| `document.md` não vazio; bytes e `chunks.jsonl` compõem a `geracao` | regravar sem re-selar `fonte.json` torna a obra inapta |
| Chunk acima do limite sem justificativa de tabela atômica | bloqueia publicação e reindexação |
| `chunks/NNNN.md`, 4 dígitos | caminho em resultados de busca |
| `current.json.module` em `traslado` ou `freoa_v6`; `freoa_v6` como módulo de reserva | `conversor()` recusa |
| CLI: `-I -m <módulo> convert <src> -o <out> --mode auto --chunks --language --job-timeout --config`; códigos 0 ok, 3 parcial; saída em `<out>/<subpasta>/document.md` | fila falha |
| `LEIA-AVISOS.txt`, linhas `Documento: <categoria>` e `<rótulo> (N páginas): n, n` | triagem degrada |
| Pacote-sombra `freoa_v6` e aliases `freoa-md` | ficam até a 3.1 (plano §4.4; varredura C0.11) |

## 4. Regras da projeção

1. Só chunks contíguos. Composto no v2 é **dividido** em contíguos ou o conteúdo sai em chunk contíguo do intervalo envelope; o caso entra no relatório de perdas. Nunca emite `spans`.
2. `schema_version: 1` em `chunks.jsonl`, `manifest.json` e `document.json`. Campos v2 não entram.
3. A cada chunk, verifica `text` × intervalo × `chunk_id`. Divergência é erro de integridade e aborta a conversão, sem saída parcial publicada.
4. Página impressa não entra no esquema 1: a Biblioteca continua com seu próprio mapa.
5. Relatório de perdas por documento, `schema1-perdas.json`, fora do pacote da Biblioteca: blocos, seções, relações e rótulos sem representação em v1, contados por tipo. Sem texto de obra.

## 5. Compatibilidade com `biblio-estrutura-1` (r15)

O adaptador `traslado-3` lê `sections` com `char_start`/`char_end` contra `representation.markdown_sha256`. Se esse hash diferir do `md_sha256` do `document.md` publicado, usa `projections.schema1.intervals` para projetar; sem isso, a fonte fica `disponivel: false` (`representacao-divergente`). O comando versionado de leitura é `traslado inspect --schema 2 --sections`. Nenhuma divergência da 3.0 ao contrato r15 nesta data. O artefato v2 da campanha carrega `representation` e, quando a Biblioteca seguir em v1 para a obra, `projections.schema1`.

## 6. Testes de contrato (E2) e de recuperação (E8)

Separados: contrato (identidade, invariantes da §3) e recuperação (por passagem).
