# Pré-registro dos gates de qualidade da 3.0.0

Ricardo Peres Freoa | Stocche Forbes Advogados · 8/11/2026 · Etapa E0. Vale a partir do hash registrado no diário e não se altera depois da primeira medição de candidata. Toda correção posterior é adendo datado, sem efeito sobre decisão já tomada.

## 1. Regras gerais

1. Decisões isoladas e nesta ordem: extração (chunker e busca fixos), chunking (extração e busca fixos), combinação.
2. Máximo de 3 tentativas por gate. Esgotadas, sai só capacidade opcional (motor novo, `omit`, RapidOCR, MCP, `embed_text`, GUI nova) e seus dependentes; os gates rodam de novo. Limiar nunca se afrouxa.
3. Integridade, compatibilidade da Biblioteca, contenção de conteúdo, revogação, reversão, licença, empacotamento, instalação e publicação não saem do escopo.
4. Divisão calibração/teste **por obra ou documento**, determinística: ordena por SHA-256 do arquivo; o primeiro terço é calibração, o restante é teste. Limiar calibrado só em calibração; decisão só em teste. Toda referência a "n" nas tabelas é do conjunto de teste.
5. Nenhum modelo hospedado recebe conteúdo de obra. O gate privado roda por scripts locais e devolve só agregados e códigos de erro. Falha não reproduzível em corpus público exclui a obra por identificador, com contagem no relatório.
6. Medições de identidade e de "zero página desaparecida" só com máquina quiescente e timeout de OCR folgado (`--ocr-timeout 600`).

## 2. Corpus

### 2.1 Camada A: redistribuível, entra no relatório atestável
Gerada por script versionado em `benchmarks/corpus-a/`, com gabarito produzido junto do PDF, por construção. Texto de atos oficiais (Lei 9.610/98, art. 8º, IV) ou texto sintético. Fenômenos: duas colunas; notas longas e contínuas; rótulos romano→arábico; tabelas com e sem borda, com estrato jurídico sem borda; scans degradados; cabeçalho corrido confundível com título.

### 2.2 Camada B: terceiros, só local, com manifesto de SHA-256
Revisão fixada e hash conferido antes de rodar. O relatório público cita só identificadores, hashes e escores.
- `opendataloader-bench` (código Apache-2.0; 200 PDFs e gabarito de origem DP-Bench): NID, TEDS, MHS.
- `olmOCR-bench` (ODC-BY-1.0), categorias `multi_column`, `old_scans`, `table_tests`, `headers_footers`: testes pass/fail.

### 2.3 Exclusão
OmniDocBench fica fora: a §5 do plano exige licença compatível e a ficha do dataset declara "for research purposes only and not for commercial use". Exclusão por condição da própria §5, registrada no diário. Docling só como comparador em ambiente separado, fora da distribuição.

### 2.4 Mínimos por estrato (teste)
100 páginas de duas colunas; 50 tabelas, das quais 20 jurídicas sem borda; 100 páginas escaneadas; 30 páginas com notas; 10 documentos com rótulos romano→arábico; 30 páginas com cabeçalho corrido confundível. Abaixo do mínimo, o estrato não é avaliável.

### 2.5 Manifesto
`docs/pre-registro/corpus-manifesto.json`: por arquivo, camada, estrato, SHA-256, origem, revisão, licença e divisão. Congelado junto deste documento.

## 3. Gabarito local (máquina do titular, scripts locais)

- **Página impressa, nascido digital:** só entram no denominador as páginas em que o rótulo declarado e o número visível na faixa de margem coincidem por busca literal determinística. `declared_label_agreement` é métrica separada.
- **Notas:** obras disponíveis em DOCX são exportadas a PDF pelo Word com estrutura marcada, ou com marcador único injetado numa cópia de trabalho; o par chamada↔nota vem dessa marcação, independente do motor. Pares ambíguos são excluídos e contados. Fonte dos pares: as obras em DOCX da amostra congelada T11; "experimental por falta de casos" é resultado legítimo.
- Em todo gabarito derivado de PDF marcado, o motor roda sobre cópia **sem árvore de estrutura e sem `/PageLabels`**.
- **Scans derivados:** os mesmos PDFs de gabarito conhecido, rasterizados e degradados de forma controlada (ruído, inclinação, compressão). Habilitam a funcionalidade só nesse domínio. Várias degradações da mesma página contam uma vez.
- **Scans reais:** a contagem exata de edições pareadas exige ler originais da Biblioteca viva, vedado na E0. Declaração de saída: **na 3.0, em scan, página impressa sai `inferred`, nunca `confirmed`, e ligação de notas fica experimental**, ainda que o mínimo em scans derivados seja atingido.
- **Recuperação:** gabarito T11 elevado a passagem por busca literal determinística da citação registrada. Consulta sem citação fica fora do gate de passagem e é contada à parte.
- **Ordem, títulos, tabelas e mobiliário na Biblioteca:** só relatório agregado, sem efeito bloqueante. A medida com referência vem do corpus público.

## 4. Métricas (todas em [0, 1], maior é melhor)

| Métrica | Variante fixada | Fonte |
|---|---|---|
| NID | `rapidfuzz.fuzz.ratio/100` após normalização de espaços; NID-S sem blocos de tabela | `opendataloader-bench` |
| TEDS | APTED, `1 − dist/max(nós)`; TEDS-S sem conteúdo de célula; gabarito sem tabela: excluído | `opendataloader-bench` |
| MHS | APTED sobre árvore plana; MHS-S só topologia; gabarito sem título: excluído | `opendataloader-bench` |
| CER | `1 − Levenshtein/len(gabarito)`, piso 0, sobre texto normalizado em NFC | própria |
| Ordem | proporção de testes `order` aprovados | modelo `olmOCR-bench` |
| Mobiliário | proporção de testes `absent` aprovados | modelo `olmOCR-bench` |
| Precisão de página | emitidas `confirmed` corretas ÷ emitidas `confirmed` | própria |
| Cobertura de página | emitidas `confirmed` corretas ÷ páginas com fólio | própria |
| Precisão de nota | ligações `confirmed` corretas ÷ ligações `confirmed` | própria |
| Recall de nota | ligações `confirmed` corretas ÷ pares do gabarito | própria |
| Precisão de mobiliário | blocos omitidos que são mobiliário ÷ blocos omitidos | própria |

Empate e direção: a média por estrato é a média simples por documento, ignorando documento sem métrica aplicável.

## 5. Tabela normativa (cumulativa)

| Fenômeno | Precisão pontual | Wilson 95% inferior | Cobertura/recall | Mínimo |
|---|---|---|---|---|
| Página impressa `confirmed`, nascido digital | ≥ 0,99 | ≥ 0,97 | ≥ 0,90 das páginas com fólio | 300 |
| Página impressa `confirmed`, escaneado (derivado e real, cada um) | ≥ 0,99 | ≥ 0,97 | ≥ 0,90 | 300 |
| Ligação chamada↔nota, nascido digital | ≥ 0,98 | ≥ 0,95 | recall ≥ 0,85 | 200 |
| Ligação chamada↔nota, escaneado (derivado e real, cada um) | ≥ 0,98 | ≥ 0,95 | recall ≥ 0,85 | 200 |
| Mobiliário omitido (`omit` como padrão) | ≥ 0,99 | ≥ 0,97 | — | 500 blocos |
| NID, MHS, TEDS, CER | não-regressão ≤ 0,01 por estrato; melhora mínima = menor entre +0,05 e 50% da distância até 1, na métrica que motiva a promoção | — | — | §2.4 |
| Casos críticos nominados | 100% | — | — | todos |

Erros admitidos por n: `docs/pre-registro/tabela-n-erros.json`, gerado por `gerar_tabela_n_erros.py` (prevalece o mais exigente entre ponto e Wilson; n=300 → 3, n=200 → 3, n=500 → 5). Para n fora da tabela, roda-se o gerador. A promoção do motor de layout exige a melhora mínima no estrato "duas colunas + notas". Métrica sem linha de base aplicável usa os pisos absolutos.

## 6. Casos críticos nominados (Camada A, fixtures por construção)

C1 duas colunas não intercaladas · C2 nota de rodapé contínua na página seguinte não fundida ao corpo · C3 rótulo romano→arábico sem deslocamento · C4 cabeçalho corrido igual ao título não emitido como título · C5 tabela sem borda com célula mesclada não perdida · C6 página em branco preservada na contagem · C7 hífen de fim de linha não corrompido em palavra composta · C8 scan degradado não perde página · C9 falha do layout cai no fallback com aviso, sem impedir a conversão · C10 obra sem fólio não recebe página `confirmed`.

## 7. Recuperação

Toda consulta do gabarito acertada na 2.0 continua acertada no nível da passagem. Recall@10 e MRR totais não inferiores, por estrato. Melhora reportada, não exigida. Na E8, dois modos: Biblioteca inalterada sobre `--schema 1` (bloqueante) e consumidor de teste com `embed_text` (só reporta).

## 8. Desempenho

Mediana, p95 e pico de memória por página, só no hardware do titular: ≤ 1,5× a 2.0 em PDF nativo e ≤ 2× em escaneado.

## 9. Integridade (absoluta)

100% de hashes e referências internas válidos; zero offset inválido; zero página desaparecida; contrato `--schema 1` 100% aprovado; comando real da Biblioteca aprovado.

## 10. Linha de base

2.0 `classic` nas camadas A e B e no gabarito local, depois do congelamento deste documento e antes da primeira candidata. Identidade da amostra congelada: `C:\Traslado\wt30\metricas\c0\c0-v200-resumo.json`.

## 11. Adendo de 17/11/2026, anterior ao congelamento: emenda da E0 (pdf-craft, D3)

Fonte: `C:\Biblioteca\_sistema\traslado30\emenda-e0-pdfcraft.md` (debate de 09/11/2026, ACEITO_UNANIMIDADE, rodada 4). Nenhum limiar da §5 muda.

### 11.1 Casos críticos nominados da emenda (100%)

Fixtures sintéticas públicas da Camada A, **separadas** das amostras de 300 e 200 da §5: não as diluem nem as compõem. Cada fixture pré-registra o resultado esperado verificável (ligações âncora→nota, abstenções, junções esperadas e proibidas, rótulos e estado, blocos de mobiliário). O 100% vale para esses exemplos e não autoriza inferência de generalização, que continua a cargo das amostras.

C11 numeração de notas reiniciada por página · C12 mesma marca para notas distintas · C13 notas por capítulo · C14 asterisco e cruz como marca · C15 chamada achatada `palavra.12`, com negativos obrigatórios que **não** podem virar chamada: art., arts., inc. IV, § 1º, par., al., fls., cf., v., vol., p., pp., n., ordinais 1º/2ª · C16 chamada repetida para a mesma nota (n:1) · C17 nota de vários parágrafos · C18 nota que continua na página seguinte sem número · C19 reclassificação de nota para corpo, preservando marca e ordem · C20 parágrafo entre páginas e entre colunas com hifenização · C21 parágrafo interrompido por figura · C22 fórmula em display como fronteira · C23 fólio decorado (`p. 12`, `Página 12`, `— 12 —`) · C24 `/PageLabels` ausente (`declared` nulo), declarado e divergente do fólio · C25 mobiliário repetido × texto substantivo repetido · C26 degeneração por repetição no OCR.

### 11.2 Diagnósticos (só relatório, sem limiar)

Falsas ligações, falsas junções e falsas remoções; precisão e recall por forma e por apresentação de marca (`mark.form`, `mark.presentation`); página impressa de trecho situado em bloco com fragmentos, como estrato da linha de página impressa.

### 11.3 Contrato e validação

O contrato emendado (`docs/contratos/modelo-v2.schema.json`, `chunks-v2.schema.json`) é validado no CI por `scripts/conferir_exemplos_v2.py`: o exemplo válido passa, cada um dos 22 inválidos falha com o código declarado em `docs/contratos/exemplos/esperado.json`, e os exemplos versionados coincidem byte a byte com os do gerador.

### 11.4 Linha de base da E2

A 2.0.2 (16/11/2026) sucede a 2.0.1 em produção. Em 17/11/2026, nas 1.875 obras da Biblioteca convertidas pela série 2.x, `document.md` reconstruído e `chunks.jsonl` foram idênticos entre 2.0.1, 2.0.2 e o publicado; a extração não mudou entre as duas versões. A identidade byte a byte da E2 passa a ser medida contra a 2.0.2, com a amostra C0 rodada de novo pela 2.0.2 antes da primeira candidata.
