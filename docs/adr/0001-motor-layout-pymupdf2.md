# ADR 0001: motor de layout da 3.0.0 (PyMuPDF 2.x, atrás de interface própria)

Ricardo Peres Freoa | Stocche Forbes Advogados · 8/11/2026 · Estado: congelado na E0.

## Decisão

1. **Candidato**: PyMuPDF 2.1 (MuPDF 1.28.6), com `pymupdf.layout` embutido, adaptado em `engines/pymupdf2` atrás de `LayoutEngine`. O motor `classic` (a extração atual) continua referência e fallback. O Markdown do PyMuPDF não é usado como saída: o `render` determinístico próprio é o único produtor de `document.md`.
2. **Sujeição a gate**: o candidato só é promovido pela tabela normativa do pré-registro (E7). Em produção, `--schema 1` usa `classic` até a campanha promover cada lote.
3. **Fixação**: `pymupdf==2.1` e `onnxruntime==1.31.1`, com hashes, em `locks/requirements-full.hashes.lock`. A resolução com a dependência circular `pymupdf` ↔ `pymupdf-layout` ↔ `pymupdf4llm` foi verificada por `uv pip compile --generate-hashes` em Python 3.11, 3.13 e 3.14 (12 pacotes, sem erro).
4. **Execução só em CPU e sem rede**: sessões `onnxruntime` com `providers=["CPUExecutionProvider"]` explícito, `intra_op=1`, `inter_op=1`. O wheel de `onnxruntime` 1.31.1 expõe `AzureExecutionProvider`; ele nunca é selecionado, e o teste de conformidade da E3 confere o provedor de cada sessão e a ausência de rede.
5. **Pesos**: inventário completo com SHA-256 em `0001-pesos.json`: 16 arquivos `.onnx` no wheel, dos quais 3 são carregados no padrão (`layout_rf2.4.1+imf1.onnx`, `feature_imf1.onnx`, `table_grid_model_v4_ep.onnx`). A carga confere o SHA-256 dos três contra o inventário e recusa divergência. Provados por interceptação de `InferenceSession`.

## Licença

- O wheel declara `Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License` (METADATA e `COPYING`). Os pesos estão dentro do wheel e não trazem licença própria: tomados, para fins da distribuição, sob a licença do wheel, AGPL-3.0, compatível com `AGPL-3.0-or-later` do Traslado.
- `pymupdf-layout` e `pymupdf4llm` 2.1 são wheels-esboço de cerca de 1 KB, sem campo de licença; o conteúdo real vem do `pymupdf`.
- **Risco residual declarado**: a declaração de licença é da Artifex para o wheel como um todo; não há documento separado sobre a proveniência dos dados de treino. Vai ao relatório final e à E9 (LICENSE-POLICY).
- `onnxruntime` MIT; `numpy` BSD-3-Clause e correlatas; `networkx` BSD-3-Clause.

## OCR em página escaneada (prova da E0)

Prova reprodutível: `C:\dev\t30-ops\e0\prova_ocr_layout.py`, fixture sintética de duas colunas com nota e fólio; saída em `C:\Traslado\wt30\e0-lab\saida\prova_ocr_layout.json`.

| Entrada | Caixas | Tipos |
|---|---|---|
| Nativa | 5 | 1 `section-header`, 3 `text`, 1 `page-footer` |
| Imagem sem OCR | 0 | nenhum |
| **Rota A**: `Pixmap.pdfocr_tobytes` (camada de texto) e `get_layout()` | 5 | 1 `section-header`, 3 `text`, 1 `page-footer` |
| **Rota B**: `get_textpage_ocr` e `get_layout(textpage=...)` | 0 | parâmetro aceito e sem efeito |

Conclusão: `pymupdf.layout` aceita blocos de OCR **pela camada de texto do PDF** (rota A), não por `textpage=`. Fluxo fixado: OCR de página inteira gerando PDF de uma página com camada de texto → layout sobre esse PDF → eventual novo OCR por região; falha ou indisponibilidade de segmentação mantém o OCR de página inteira com aviso, sem impedir a conversão. Atenção de implementação: em PyMuPDF 2.x, `doc[n]` devolve objeto novo a cada acesso e `layout_information` fica no objeto em que `get_layout` foi chamado.

A prova não mediu qualidade: na fixture, a nota de rodapé foi classificada como `text`, não como nota. Precisão fica para a E7.

## Consequências

- A partir do PyMuPDF 2.x, `onnxruntime`, `numpy`, `networkx`, `pyyaml`, `psutil` e `tabulate` são dependências obrigatórias, inclusive do `classic`; `auditar_libs.py` passa a inspecionar `onnxruntime` nos três sistemas.
- Layout carregável é verificado por job de CI em Windows x64, Ubuntu, macOS arm64 e contêiner mínimo; no computador do titular, verificado nesta E0 (Windows 11, Python 3.14.6).
- Windows Server Core continua limite declarado.

## Teste de viabilidade antes do congelamento (17/11/2026)

Script `C:\Traslado\wt30\e0-lab\spike\spike_e0.py` (fixture sintética pública gerada no próprio script: parágrafo com hifenização na quebra de página, chamada de nota menor e elevada, nota no pé, fólio, `/PageLabels` presente e ausente, 20 páginas de duas colunas). Saídas `classic.json`, `pymupdf2.json` e `q4-pymupdf2.json` na mesma pasta. Windows 11 do titular; `classic` = PyMuPDF 1.28.2 (Python 3.14.6, venv da 2.0.1), `pymupdf2` = PyMuPDF 2.1 com `pymupdf.layout`.

| Pergunta | `classic` | `pymupdf2` | Leitura |
|---|---|---|---|
| Q1: texto por bloco com caixa e caracteres, base para `fragment.text_start/text_end` | sim | sim | viável por construção nos dois; a decisão de juntar é da camada `structure` |
| Q2: chamada de nota observável e localizável | sim: flag de sobrescrito e fonte menor no mesmo span, offset 9–10 em `obrigação1` | idem | viável em nascido digital; em scan não testado |
| Q2b: região de nota e de fólio distinguíveis | não há classe (só geometria) | `footnote` e `page-footer` corretos; a linha isolada com a chamada saiu como `caption`/`section-header` | evidência útil para `layout_footnote_region`; erro de classe em linha curta isolada |
| Q3: `/PageLabels` declarado × ausente | `get_label()` devolve `12`, `13`; sem `/PageLabels` devolve `''` e o catálogo não tem a chave | idem | `declared: null` implementável pela consulta ao catálogo, sem rótulo sintetizado |
| Q4: ms por página | `rawdict` mediana 5,1 (carga baixa); conversão ponta a ponta da 2.0.2: 1,46 s para 22 páginas (~66 ms/página, com partida do processo) | `rawdict` 15,8; `get_layout()` mediana 879 ms, p95 958 ms (CPU a 82% com outras tarefas; primeira medida, sob mais carga: 1.172 ms) | **o layout em toda página nativa custa ~13 vezes a conversão inteira da 2.0.2 por página** |

**Consequência para o gate de desempenho (§8 do pré-registro, ≤ 1,5× a 2.0 em PDF nativo):** com o layout aplicado a toda página, o `pymupdf2` reprova por larga margem, mesmo descontada a carga da máquina. O limiar não muda. Duas saídas compatíveis com o plano, a decidir pelo titular antes da E3: (a) aplicar o layout só a páginas sinalizadas por indicadores baratos do `classic` (duas colunas, candidata a nota, tabela sem borda), medindo o gate sobre o documento inteiro; (b) manter o `pymupdf2` experimental, fora da promoção, se (a) não couber. Medida definitiva só com máquina quiescente (§1.6).
