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

O contrato emendado é validado no CI por `scripts/conferir_exemplos_v2.py`: para `modelo-v2.schema.json` (`scripts/validar_modelo_v2.py`), os exemplos válidos passam e cada inválido falha com o código declarado em `docs/contratos/exemplos/esperado.json`; para `chunks-v2.schema.json` (`scripts/validar_chunks_v2.py`, com cruzamento contra `document.md` e `notes[]` do documento), idem em `docs/contratos/exemplos/chunks/`; e os exemplos versionados coincidem byte a byte com os do gerador. Contagens em 23/11/2026: §12.2.

### 11.4 Linha de base da E2

A 2.0.2 (16/11/2026) sucede a 2.0.1 em produção. Em 17/11/2026, nas 1.875 obras da Biblioteca convertidas pela série 2.x, `document.md` reconstruído e `chunks.jsonl` foram idênticos entre 2.0.1, 2.0.2 e o publicado; a extração não mudou entre as duas versões. A identidade byte a byte da E2 passa a ser medida contra a 2.0.2, com a amostra C0 rodada de novo pela 2.0.2 antes da primeira candidata.

## 12. Adendo de 23/11/2026, anterior ao congelamento: decisões do titular e fechamento da E0

Fontes: decisões D-M1 a D-M5 do titular (diário, 23/11/2026), ADR 0001 (adendo de 23/11) e as revisões independentes da E0 (Fable e Sonnet-B, tentativa 1, reprovadas; objeções tratadas abaixo). Nenhum limiar da §5 muda. Onde este adendo diverge de §1–§11, prevalece este adendo. Em matéria de contratos e gates, este pré-registro congelado e a emenda pdf-craft prevalecem sobre `plano.md`, que não é reescrito (seu §4.2 ainda lista `footnote_ref`, retirado pela D2).

### 12.1 Motor (D-M1), escaneados (D-M4) e escopo dos gates

1. Na 3.0.0, `classic` é o motor padrão; `pymupdf2` fica atrás da interface própria, opt-in e rotulado experimental (D-M1).
2. A promoção de motor de layout fica **fora dos gates da 3.0.0**. A exigência da §5 de melhora mínima no estrato "duas colunas + notas" fica sem objeto. NID, MHS, TEDS e CER do `classic` continuam sob não-regressão por estrato. O `pymupdf2` é medido na E7 só como relatório, sem consumir tentativas. Promoção futura exige nova medição no hardware do titular, com o gate da §8 inalterado, e decisão registrada.
3. Em scan, página impressa sai `inferred`, nunca `confirmed`, e a ligação de notas sai `candidate` (experimental), ainda que o mínimo em scans derivados seja atingido (D-M4). O contrato o impõe (§12.2: `pagina_escaneada_confirmada`, `ligacao_confirmada_em_scan`). Por consequência de D-M4 (decisão do titular, diário de 23/11/2026), as linhas da §5 de página impressa `confirmed` e de ligação `confirmed` em escaneado (derivado e real) ficam sem objeto na 3.0.0: scans derivados são medidos como relatório. Não se gastam tentativas para alcançar o mínimo em scans reais.
4. `footnotes`, `pagination`, `structure` e mobiliário são módulos próprios, sem dependência do layout, testados contra C11–C26 antes de qualquer medição em corpus (D-M2).
5. Escopo dos casos críticos da §6 e do §11.1: valem a 100% para o caminho padrão da 3.0.0 (`classic` + módulos próprios, artefato v2), inclusive C1 (ordem em duas colunas), que permanece gate como pré-registrado. C9 (falha do layout cai no fallback) só tem objeto no `pymupdf2` opt-in e é gate dele. C8 vale para o fallback de OCR (página não se perde). Caso reprovado após 3 tentativas retira a capacidade opcional que o testa (§1.2); se testar requisito obrigatório (integridade, não perda de página), bloqueia a release. O que cede se C1 falhar no `classic` sem capacidade opcional a retirar é pergunta ao titular (diário), não decisão deste adendo.

### 12.2 Contrato: emendas técnicas e validadores

A demonstração do §12.3 e as revisões mostraram lacunas no contrato de 17/11. Emendas, sem tocar D1–D6:
- **Fonte sem geometria.** `block.bbox` admite `null` quando o motor não observa caixa (DOCX, TXT, XLSX, PPTX, prévia de scan); nunca caixa inventada. `page.width` e `page.height` admitem `null`, juntos, e então nenhuma caixa é admitida na página (`geometria_de_pagina_parcial`, `pagina_sem_geometria_com_caixa`). Em página sem geometria não cabe âncora não localizada; o tratamento da chamada sem posição segura nessas fontes é questão aberta da E3.
- **`quality_signals.page_image`** = {`in_source`, `preview_exported`}: fato da página que o `LEIA-AVISOS.txt` da projeção exige e que define, com `route: "ocr"`, a página escaneada para D-M4.
- **Rótulos de página.** `confirmed` exige valor em `declared` ou `observed`; `inferred` exige `inferred`; `divergent` exige `divergence`; página escaneada nunca `confirmed`.
- **Notas e relações.** Parte de nota tem `role: footnote`; um bloco pertence a no máximo uma nota; uma âncora tem ligações `confirmed` para uma só nota; `footnote_link` com origem em página escaneada é `candidate`; âncora localizada não incide sobre caractere gerado; `block.page` = `page.index`; fragmentos em ordem de texto e de página, a partir da página do bloco, só em páginas existentes; âncora não localizada na página do bloco ou de um fragmento; toda âncora `object_position` tem `anchored_in` (D2).
- **`render_map`.** Sem duplicação de trecho-fonte num bloco; trecho de `block.text` não coberto por intervalo de bloco renderizado só pode conter espaço em branco (remoção de outro caractere exige `removed`). Seções referem blocos existentes, com intervalo não invertido.
- **Chunks v2.** `index` começa em 1, como na v1 (o esquema de 17/11 admitia 0: corrigido); `printed_pages` só contém rótulos `confirmed` das páginas do chunk (inferido nunca entra) e é igual a `context.printed_pages`; `footnote_ids` refere `notes[].id` existentes e é igual a `context.footnote_ids`; contíguo: `text` = fatia do `document.md`, `char_count` e `chunk_id` conferidos; composto: spans ordenados, sem sobreposição, exatos no envelope, `text` = trechos unidos por `\n\n`, `chunk_id` sobre os spans.
- **`config_sha256`** exclui as opções operacionais que não alteram resultado bem-sucedido (`job_timeout`, `ocr_timeout`, `ocr_workers`, `advanced_timeout`, `max_memory_mb`, `overwrite`) e os caminhos absolutos.
- **Validadores como contrato.** `scripts/validar_modelo_v2.py` e `scripts/validar_chunks_v2.py` integram o contrato e entram no hash do congelamento. Exemplos gerados por `scripts/gerar_exemplos_v2.py`: documento, 3 válidos (`minimo.json`, `sem-geometria.json`, `variantes.json`) e 51 inválidos, cobrindo todos os códigos do validador salvo `estrutura_incompleta` (salvaguarda, inalcançável com esquema válido); chunks, 1 arquivo válido (contíguo e composto) e 14 inválidos, cobrindo todos os códigos de `validar_chunks_v2.py`. Oráculo `scripts/conferir_exemplos_v2.py`, no CI.

### 12.3 Derivabilidade de `--schema 1` (emenda pdf-craft, §3, item 4)

`scripts/derivar_schema1.py` converte pela 2.x, monta o v2 (blocos, `render_map` por alinhamento literal, ids determinísticos), valida estrutura e invariantes, confere cada intervalo literal do `render_map` contra o `document.md`, reconstrói o `Document` v1 **só do v2** e projeta de novo. Fixtures públicas (oito): `fixture.pdf` e `fixture.docx` do autodiagnóstico; `fixture-22p.pdf` (viabilidade de 17/11); `fixture-scan3p.pdf` (3 páginas dela rasterizadas a 200 dpi, rota de OCR); e, por `scripts/gerar_fixtures_derivacao.py`, PDF com tabela com régua, imagem e fólio, DOCX com tabela e imagem, XLSX com células numéricas e vazias, PPTX com tabela. Resultado: `document.md`, `chunks.jsonl`, `document-layout.json` e `LEIA-AVISOS.txt` **idênticos em bytes** nos oito, com transformações `literal`, `table_render` e `image_ref`; `minimo.json` projeta de modo determinístico, com chunks válidos. Hashes no diário.
Limites: (i) `document.json` v1 não é derivável só do v2 (`page.metrics` além do §12.2, `page.native_text`, `block.details`); a Biblioteca não lê esses campos, e com `classic` ele continua emitido do `Document` do próprio `classic` (`projecao-schema1.md` §1); (ii) o `classic` não produz seções, notas, âncoras nem rótulos: a derivabilidade provada é a do subconjunto que ele preenche; (iii) o hífen discricionário (U+00AD) não sobreviveu à camada de texto da fixture PDF: a transformação `soft_hyphen_join` fica coberta pelo exemplo `minimo.json` e pelo validador, não por saída do `classic`; (iv) a identidade na amostra congelada continua sendo o gate da E2.

### 12.4 Corpus, composição dos mínimos e Camada A

1. `docs/pre-registro/corpus-manifesto.json`, gerado por `gerar_corpus_manifesto.py` sobre a Camada B baixada por revisão fixada (opendataloader-bench `7af1d8f`, olmOCR-bench `54a96a6`), lista 1.191 arquivos com camada, fonte, estrato, papel, SHA-256, origem, revisão, licença e divisão. Documentos de teste: duas colunas 160, tabelas 116, cabeçalho corrido 176, escaneado 70, DP-Bench 134.
2. Divisão (§1.4): por documento, dentro de cada fonte, em ordem de SHA-256; o primeiro terço (piso) é calibração.
3. **Origem dos n da §5.** Página impressa e ligação de notas: só do gabarito local (§3: amostra congelada T11 e pares derivados de DOCX), nunca da Camada A. Mobiliário omitido: o n são os blocos omitidos em páginas de teste da Camada B que tenham testes `present`; é erro o bloco omitido cujo texto satisfaz um teste `present`, pelo critério de correspondência do próprio olmOCR-bench; bloco omitido sem correspondência é excluído e contado à parte; abaixo de 500 blocos avaliáveis, `omit` não vira padrão. Não há gabarito local de mobiliário (§3). NID, TEDS, MHS e CER: Camada B. A Camada B não tem estrato de notas nem de romano→arábico; esses mínimos dependem do gabarito local, e abaixo deles a funcionalidade fica experimental no estrato (§5). Escaneado da Camada B: 70 documentos de teste; o mínimo de 100 páginas conta páginas, não documentos.
4. **Camada A** (fixtures por construção) serve só aos casos críticos C1–C26 e a diagnósticos; não compõe nenhum n da §5. Gerador em `benchmarks/corpus-a/`, escrito por agente distinto do que implementa os módulos da E3, revisado às cegas, com manifesto e resultados esperados congelados por hash no diário antes de qualquer código de módulo da E3. Distribuição: ao menos uma instância por caso; C15 com uma instância positiva e uma por negativo obrigatório (16); C23 com as três decorações; C24 com ausente, declarado e divergente.
5. Resultado esperado verificável de cada caso do §11.1 (o 100% vale para estas instâncias): C11 notas reiniciadas por página ligam à nota da própria página; C12 mesma marca em notas distintas liga cada chamada à nota da sua página ou seção, sem cruzamento; C13 notas por capítulo ligam dentro do capítulo; C14 `*` e `†` saem `mark.form` `asterisk`/`dagger` e ligam; C15 a chamada `palavra.12` liga e nenhum negativo vira âncora; C16 chamadas repetidas geram várias `footnote_link` para uma nota; C17 nota de vários parágrafos é uma nota com uma ou mais partes; C18 a continuação sem número é parte da nota só com continuação `confirmed`, senão `continuation` `candidate`; C19 nota reclassificada como corpo mantém marca e ordem no texto; C20 parágrafo entre páginas ou colunas é um bloco com fragmentos, hífen em `removed` e sem palavra corrompida; C21 parágrafo interrompido por figura tem âncora `object_position` com `anchored_in` à figura, ou dois blocos com `continuation` `candidate`; C22 fórmula em display é fronteira de bloco; C23 fólio decorado gera `observed` com o número; C24 `/PageLabels` ausente dá `declared: null`, declarado dá o rótulo, divergente dá `state: divergent` com `divergence`; C25 mobiliário repetido sai `role: furniture` e o texto substantivo repetido sai corpo; C26 repetição degenerada no OCR gera aviso, sai como bloco próprio fora do `render_map` e não duplica texto no Markdown.

### 12.5 Regra de metadados (D5)

D5 governa os metadados da Biblioteca, não os três contratos do Traslado; mora no contrato r15 da Biblioteca e é testada na E8. Interpretação para a 3.0.0, coerente com D-M4: "camada nativa" é a camada de texto de página que não é página-imagem; escaneado com camada de texto embutida conta como OCR, logo no máximo `candidate`. A normalização declarada de D5 é fixada no contrato da E8 antes dos testes; cada ISBN impresso é verificado por si; `confirmed` atesta a leitura do campo, não sua atribuição. Pergunta ao titular registrada no diário.

### 12.6 Linha de base

1. Identidade da E2: a amostra C0 (80 obras) reconvertida pela 2.0.3 instalada em 23/11/2026 (`C:\Traslado\wt30\metricas\p0\p0-v203-resumo.json`), comparada à da 2.0.0 (`c0-v200-resumo.json`) para provar que a extração não mudou de 2.0.0 a 2.0.3; o hash desse resumo entra no diário com o congelamento. Substitui as referências da §10 e do §11.4.
2. A linha de base do `classic` nas Camadas A e B roda depois do congelamento e antes da primeira candidata (§10); a da Camada A, depois de seu manifesto (12.4.4).
