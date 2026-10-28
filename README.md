# Traslado 2.0.0

Conversor local de documentos para Markdown rastreável: texto nativo rápido, OCR seletivo (Tesseract) e chunks com offsets exatos, hashes e páginas de origem, prontos para busca e RAG. O nome vem do *traslado* forense: a cópia fiel que remete sempre ao original.

Traslado é a primeira versão pública do antigo Freoa Markdown Converter v6 (6.0.0rc9). O pacote Python e o módulo passam a `traslado`; `freoa_v6`, `freoa-md` e `freoa-md-gui` permanecem compatíveis durante a série 2.x.

Candidata com limitações explícitas: testes sintéticos não homologam fidelidade jurídica, e todo texto convertido sai marcado `fidelity: unverified`.

## Instalação

### Controle primário: download verificado com GitHub Attestations

Para verificação criptográfica de proveniência antes de executar qualquer script ou instalar o pacote, baixe os artefatos da [página de releases](https://github.com/rickfreoa/traslado/releases), confira o `SHA256SUMS` e valide a attestation oficial:

```bash
gh attestation verify traslado-2.0.0-py3-none-any.whl \
  --repo rickfreoa/traslado \
  --signer-workflow rickfreoa/traslado/.github/workflows/release.yml

gh attestation verify install.ps1 \
  --repo rickfreoa/traslado \
  --signer-workflow rickfreoa/traslado/.github/workflows/release.yml

gh attestation verify install.sh \
  --repo rickfreoa/traslado \
  --signer-workflow rickfreoa/traslado/.github/workflows/release.yml
```

### Atalho de conveniência (one-liner)

**Windows (PowerShell 5.1+):**

```powershell
irm https://github.com/rickfreoa/traslado/releases/download/v2.0.0/install.ps1 | iex
```

**Linux e macOS (`/bin/sh` POSIX):**

```sh
curl -fsSL https://github.com/rickfreoa/traslado/releases/download/v2.0.0/install.sh | sh
```

> **Aviso de segurança e assinatura:** O comando one-liner (`irm ... | iex` ou `curl ... | sh`) é **apenas um atalho de conveniência** e não substitui a verificação prévia com `gh attestation verify` e `SHA256SUMS`. Os scripts e pacotes do Traslado **não são assinados por certificado de assinatura de código** (Authenticode no Windows ou Developer ID / notarização Apple no macOS); por isso, alertas de segurança do sistema operacional (como Windows SmartScreen ou macOS Gatekeeper) podem aparecer ao baixar ou executar os instaladores clicáveis (`INSTALAR.bat`, `instalar.command`, `instalar.sh`).

### Ciclo de vida pelo CLI

Após a instalação, o comando `traslado` gerencia o ciclo de vida local sem exigir administrador:

- `traslado where [--json]` — exibe caminhos da instalação ativa, Python gerenciado, OCR e diretórios do usuário;
- `traslado update [--version V] [--yes]` — verifica revogação em dois canais, confere `SHA256SUMS` e promove a nova release de forma atômica;
- `traslado rollback` — restaura a release imediatamente anterior preservada em `releases/<previous_release_id>`;
- `traslado uninstall [--yes] [--keep-data]` — remove atalhos e a raiz de instalação gerenciada, preservando sempre os documentos do usuário (`data_root`).

## Novidades da 1.1.0

A 1.1 acrescenta geometria e classificação aos artefatos estruturados, notas no DOCX e OCR concorrente, sem mudar o texto. Na amostra de qualificação (80 obras), o Markdown dos PDFs nativos saiu idêntico ao da 1.0.0; nas páginas com OCR, as palavras são as mesmas e só muda o espaçamento, porque cada parágrafo reconhecido vira uma linha (quebras internas passam a espaço). Histórico completo em [CHANGELOG.md](CHANGELOG.md).

- **Notas de rodapé e de fim do DOCX** viram notas Markdown (`[^n]`), ligadas às definições de `footnotes.xml` e `endnotes.xml`, com famílias distintas. Também beneficiam `.doc`, `.rtf` e `.odt` convertidos para `.docx`. O aviso agregado da 1.0.0 dá lugar a avisos por espécie de perda remanescente (chamada sem definição, definição sem chamada, id repetido, conteúdo complexo achatado, cabeçalhos e rodapés, comentários).
- **Caixas nos blocos de OCR.** Cada parágrafo reconhecido pelo Tesseract leva sua caixa em coordenadas da folha, tirada do TSV da mesma execução; a sequência de palavras é idêntica à do TXT, ou o bloco recai no texto sem geometria com aviso `ocr_geometry_fallback`. Em escaneadas, isso permite localizar o número impresso da página: nas 442 folhas rotuladas à mão da amostra de qualificação, as páginas simples com número correto (pela regra de paginação da Biblioteca) passaram de 13 para 78, sem nenhuma página antes correta piorar.
- **Linhas e spans** (tamanho de fonte, negrito, itálico) sob os blocos nativos do PDF, em `document-layout.json`.
- **Mobiliário de página marcado** (`--furniture mark`, padrão): cabeçalhos corridos, rodapés e números de folha recebem `role: furniture` nos chunks, sem nenhum texto omitido. `--furniture off` reproduz a 1.0.0.
- **Folha escaneada com carimbo** (imagem única que cobre a folha e pouco texto visível) passa a OCR mesmo quando há camada de texto invisível.
- **OCR concorrente por folha** (`--ocr-workers`, 1 a 4, padrão 4), com resultado idêntico ao serial e ordem determinística.
- **Registro único de conversores**: despacho, descoberta e `doctor` leem o mesmo cadastro; formato indisponível é recusado com motivo.
- **Distribuição verificável**: CI em Windows e Linux (Core e Full, Python 3.11 a 3.14), seleção explícita dos arquivos distribuídos, ZIP determinístico, `SHA256SUMS` e attestation vinculada aos bytes.

Fora da 1.1, por decisão medida: a ordem de leitura por calhas (`layout = gutters`) piorou a ordem das folhas de referência e não foi incluída.

## Novidades da 1.0.0

- **Cerca ou `<table>` sem fechamento** protege só até o fim do bloco de origem, não até o fim do documento (avisos `chunk_unclosed_fence` e `chunk_unclosed_table`). Antes, uma única cerca aberta podia produzir chunks de milhões de caracteres.
- **Ruído de OCR com forma de cerca** (linhas iniciadas em `~~~` ou ```` ``` ````) em prosa extraída de PDF ou imagem só pareia com fechamento no mesmo bloco. Num livro real de 1.538 páginas, o maior chunk caiu de 720.877 para 6.000 caracteres; em 54 obras com essas linhas, 43 ficaram idênticas e as 11 restantes deixaram de ter chunks acima de 24.000 caracteres.
- O Markdown (`document.md`) não muda: só a segmentação. Varredura em passada única, com custo linear testado.
- O OCR não acompanha mais o pacote: na instalação, os artefatos fixados em `ocr-assets.lock.json` (Tesseract 5.5.3, 7-Zip e seis modelos `tessdata_fast`) são baixados das fontes oficiais e conferidos por tamanho e SHA-256.

## Obter

Baixe `traslado-2.0.0-windows.zip` na página de [releases](https://github.com/rickfreoa/traslado/releases) e confira o SHA-256 com o arquivo `SHA256SUMS` publicado ao lado. Para montar o pacote a partir do código: `python scripts/build_release.py` (gera o wheel, o `INSTALL-PAYLOAD.json`, o ZIP e as somas em `dist/`).

## Instalar e abrir no Windows

Requer Windows x64 e Python 3.11–3.14 com Tcl/Tk, pip e venv de origem confiável. O pacote não instala Python. Extraia o ZIP e abra **INSTALAR.bat** na pasta `traslado-2.0.0-windows`. Por padrão instala **Full**, OCR Tesseract 5.5.3 e português, inglês, espanhol, italiano, francês e alemão; cria o atalho **Freoa Markdown Converter** na Área de Trabalho e abre a janela. Não exige administrador. Full inclui PyMuPDF, sujeito a AGPL ou licença comercial; consulte THIRD-PARTY.md antes de redistribuir/integrar.

Aplicativo: `%LOCALAPPDATA%\Freoa\MarkdownConverter`. Documentos: a pasta `Freoa Markdown Converter` em Documentos; saída inicial em `resultados`. O atalho abre diretamente a interface. `ABRIR.bat` também abre a instalação ativa. Os caminhos reconhecem as pastas especiais do Windows. Pastas com links/junctions/reparse points ou sobreposição entre aplicativo, pacote e dados são recusadas.

Full obtém nove bibliotecas Python do PyPI, com versões e hashes obrigatórios e somente wheels. O wheel próprio já acompanha o ZIP: não há build/resolução de dependências durante sua instalação. Executável OCR, extrator e seis modelos são baixados na instalação, com URLs, tamanhos e SHA-256 fixados no `ocr-assets.lock.json`; uma pasta `ocr-cache` preenchida ao lado do instalador permite a etapa offline. O instalador valida os bytes e extrai o motor para uma pasta privada, sem executar o instalador NSIS, gravar registro/PATH global ou baixar modelos no primeiro uso. O cache OCR permite essa etapa offline; bibliotecas Python ainda precisam de internet ou cache pip previamente preenchido. Os hashes internos detectam divergência; conserve o SHA-256 externo do ZIP para autenticar a entrega.

## Opções expressas pelo CLI

O perfil leve **Core exige seleção expressa** e atende texto/Markdown, JSON/XML como texto, CSV/TSV e texto HTML. Não inclui bibliotecas PDF/Office/imagens nem instala OCR:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\INSTALAR.ps1 -Profile Core
```

Alterar idiomas padrão, omitir OCR ou escolher caminhos:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\INSTALAR.ps1 -OCRLanguages por,eng
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\INSTALAR.ps1 -SkipOCR
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\INSTALAR.ps1 -InstallRoot 'C:\Apps\Freoa' -DataRoot 'C:\Documentos\Freoa' -ShortcutDirectory 'C:\Atalhos'
```

Na chamada nativa `powershell.exe -File`, passe os idiomas como **uma lista separada por vírgulas**, como acima. Também são aceitos `por+eng`. Idiomas permitidos: por, eng, spa, ita, fra, deu, sem duplicatas. `-NoShortcut` omite o atalho; `-Launch` abre a janela ao fim; `-Python` seleciona Python confiável; `-LegacyRoot` identifica legado sem atalho. `INSTALAR.ps1` sem parâmetros instala Full, seis idiomas e atalho; `INSTALAR.bat` acrescenta abertura da janela. ExecutionPolicy Bypass é somente do processo, sem alteração persistente.

## OCR e interface

Nesta arquitetura, Tesseract é necessário para OCR de imagens e PDFs escaneados. PDFs com texto aproveitável usam extração nativa; o modo automático aplica OCR apenas nas páginas/regiões elegíveis. O motor privado desta instalação tem precedência sobre instalações globais; CLI `--tesseract`/`--tessdata` permite escolha explícita.

1. Abra o atalho; escolha arquivo ou pasta, saída e configuração TOML opcional.
2. Use `auto` para OCR seletivo, `native` para texto nativo ou `ocr` para forçar OCR em PDF/imagens. Office continua nativo.
3. Clique Converter. O log, a lista de resultados e a prévia textual mostram progresso e resultados parciais.
4. Confira os avisos e abra a pasta de saída. “Concluído” indica execução encerrada, sem certificar fidelidade.

Cancelar encerra o processo atual e conserva arquivos anteriores concluídos. Fechar a janela durante execução solicita cancelamento. “Verificar instalação” identifica o motor e os modelos disponíveis. A execução OCR valida os idiomas solicitados no mesmo motor/tessdata e recusa modelos ausentes ou falhas de carregamento, mesmo com código de saída zero. PDFs mistos preservam a extração nativa e publicam avisos nas páginas com OCR incompleto. Prévia passiva até128KB: não executa HTML nem carrega URLs.

## Conversão pelo CLI

Use o Python da versão ativa; não mova ambientes já instalados:

```powershell
$app = Join-Path $env:LOCALAPPDATA 'Freoa\MarkdownConverter'
$current = Get-Content (Join-Path $app 'current.json') -Raw | ConvertFrom-Json
$python = Join-Path $app ('releases\' + $current.release_id + '\.venv\Scripts\python.exe')
& $python -I -m traslado doctor
& $python -I -m traslado convert 'C:\documentos\contrato.pdf' -o 'C:\resultados' --mode auto
& $python -I -m traslado convert 'C:\documentos\scan.pdf' -o 'C:\resultados' --mode ocr --language por
```

Exit codes: 0 todos concluídos; 3 resultado parcial; 2 falha/cancelamento/timeout; 130 interrupção CLI. Cada arquivo gera JSON de resultado. TOML usa apenas `[conversion]`; CLI `--config` e campo correspondente na UI. Exemplo:

```toml
[conversion]
mode = "auto"
language = "por+eng+spa+ita+fra+deu"
dpi = 250
job_timeout = 3600
chunks = true
chunk_size_chars = 6000
chunk_overlap_chars = 600
scan_previews = false
ocr_timeout = 45
images = true
tables = true
```

## Erros e avisos

A assinatura do conteúdo prevalece sobre a extensão para PDF, Office, imagens e HTML reconhecidos. Uma divergência gera `format_extension_mismatch` em `document.json`, nos chunks, em `LEIA-AVISOS.txt` e no resumo do resultado. Texto e CSV continuam dependendo da extensão; formatos legados detectados são recusados pelo perfil padrão.

Resultados `failed`, `partial`, `cancelled` e `timeout` incluem `error_code`: `unsupported`, `needs_ocr`, `malformed`, `encrypted`, `resource_limit`, `missing_part`, `io`, `timeout`, `cancelled` ou `internal`. `needs_ocr` inclui `error_pages` (folhas a partir de 1). O campo textual `error` é preservado. Em resultados parciais, vale o primeiro erro localizado por página; avisos de capacidade ou fidelidade sem exceção usam `unsupported`. Erros de uso/configuração saem em JSON no stderr.

Os códigos de saída continuam 0 (completo), 3 (parcial), 2 (falha, cancelamento, timeout ou uso) e 130 (interrupção). `internal` identifica uma exceção ainda não classificada.

## Artefatos e limites efetivos

Cada conversão publicada cria uma subpasta **com o nome do documento no destino escolhido**, contendo `document.md`, `document.json`, `LEIA-AVISOS.txt`, `manifest.json`, chunks opcionais e ativos locais quando presentes. Repetições recebem `(2)`, `(3)` etc.; arquivos/pastas existentes não são substituídos. Nomes longos são encurtados no meio para conservar ano/número de volume, com limite de 80 caracteres antes do sufixo de repetição. Links, nomes reservados Windows e caracteres de caminho são tratados. Pastas `.freoa-work-…` são temporárias da transação e são removidas ao terminar; uma falha de limpeza aparece explicitamente no log. JSON mantém rotas por página, blocos, motores, avisos, texto nativo e informações de OCR; manifesto registra SHA-256 da fonte/artefatos, opções e versão. O hash comprova identidade dos bytes, não acurácia.

- PDF: texto nativo, tabelas quando recuperáveis, imagens e OCR de páginas ou regiões elegíveis. Roteamento e ordem de leitura são heurísticos; colunas complexas, fórmulas, scans ruins e tabelas escaneadas precisam de revisão. Confiança Tesseract é score do motor, não taxa comprovada de acerto.
- DOCX: parágrafos, títulos e tabelas, preservando mesclas em HTML; algumas imagens. Cabeçalhos, notas, comentários e formatação integral não são extraídos.
- XLSX: texto/formulas de células sem recalcular; estilos, imagens e mesclas não são preservados.
- PPTX: texto e tabelas/imagens suportadas pelo parser; notas, SmartArt, gráficos e layout podem ficar incompletos.
- HTML: apenas texto local; scripts/estilos são descartados. Não renderiza navegador, busca URLs nem carrega recursos remotos.
- Imagens: normalização e OCR local; não existe descrição semântica por visão nesta rc4.
- JSON/XML: conteúdo textual; não há transformação semântica de esquema.
- PDF com senha, `.doc/.xls/.ppt`, RTF, EPUB/OFD e formatos não listados são incompatíveis nesta rc4.

Limites padrão: 256 MiB de entrada, 512 MiB de expansão de arquivo, 5.000 páginas, 20 milhões de pixels por imagem/render, 128 MiB de saída, 1 milhão de células, 3.600 s por trabalho (limite configurável, com cancelamento) e 45 s por OCR. No Windows, um Job Object contém o processo de trabalho e seus descendentes: criação suspensa antes da atribuição, encerramento ao fechar o job, teto padrão de 2 GiB de memória comprometida total e 32 processos. `max_memory_mb` é configurável entre 128 e 16.384 MiB. Em outros sistemas, há encerramento por sessão/deadline, mas esse teto Windows de memória não é aplicado. Isso reduz impacto de entradas hostis; não constitui isolamento de arquivos ou rede. O motor e as bibliotecas locais rodam com as permissões do usuário; não há antivírus ou sandbox de filesystem/rede.

MinerU e modelos de visão não são distribuídos. Existe um adaptador **experimental e opt-in pelo CLI** para `mineru-kit` 4.0.10 instalado em ambiente separado: `--advanced-kit 'C:\ambiente-mineru\Scripts\mineru-kit.exe'`. Ele seleciona páginas PDF suspeitas, limita execução e mantém o resultado derivado; não está habilitado pela interface. Os modelos precisam estar disponíveis previamente. Variáveis offline dos hubs reduzem downloads comuns, mas não bloqueiam a rede no sistema operacional nem desativam toda telemetria de terceiros. O runtime MinerU/modelos real não foi validado: **NOT RUN**. Não habilite esse adaptador para documentos confidenciais sem bloquear e verificar a rede e concluir a revisão de licenças. Tabelas visuais complexas não têm garantia de reconhecimento nesta entrega; nenhuma afirmação de “mínimo erro” ou equivalência MinerU é garantida.

## Atualização e migração

O instalador cria um ambiente novo em caminho definitivo, valida bibliotecas/OCR, prepara o atalho e só então troca a versão ativa. Na atualização gerenciada, remove a versão anterior identificada pelo registro do aplicativo. Arquivos acrescentados ou alterados nessa versão são copiados e verificados antes da remoção, em `preservados-na-atualizacao` dentro da pasta de documentos. Documentos de entrada, resultados e configurações ficam separados do aplicativo.

Para instalações anteriores v5.5.0/rc1/rc2, detecta a pasta apontada pelo atalho reconhecido; também aceita `-LegacyRoot 'C:\pasta\versao-anterior'`. Confere arquivos contra o catálogo de hashes sem executar o Python antigo. Remove apenas arquivos de aplicativo reconhecidos e o ambiente `.venv`, conservando cópia verificada; documentos, YAML, exemplos e código modificado permanecem na pasta original. Não varre o disco nem apaga cópias de ZIP, pastas desconhecidas ou Tesseract global usado por outros programas. Sem atalho conhecido, informe `-LegacyRoot` para localizar o legado. Uma janela/conversão antiga em execução impede a atualização até ser fechada. Falha antes da troca mantém a versão ativa; falha na limpeza posterior é comunicada como instalação com ressalvas.

A aparência e o fluxo da v5.5 foram recuperados. YAML, plugins, watch mode, cache, sumarização e CLI legados não foram migrados: a configuração aceita TOML. A rc9 habilita sobrescrita de resultados reconhecidos, conforme a seção abaixo. **Chunks estão habilitados e vêm marcados na interface**, com tamanho e sobreposição em caracteres; podem ser desmarcados. Sem sobrescrita, a v6 produz pastas únicas e preserva a saída anterior. A candidata precisa de comparação com referência humana antes da adoção.

## Validação e distribuição

Os relatórios de auditoria das versões candidatas não integram o repositório público. Consulte-os para distinguir checks sintéticos, OCR efetivamente executado, benchmark e itens NOT RUN. A suíte completa está em `tests/`. O extra `test` declara o parser CommonMark usado pelos testes (`markdown-it-py` e `mdurl`); ele não integra o runtime Full nem o instalador. Em um ambiente de validação separado, instale o wheel e os extras antes da suíte:

```powershell
& $python -I -m pip install --require-hashes -r requirements-full.hashes.lock -r requirements-test.hashes.lock
& $python -I -m pip install --no-deps .\dist\traslado-2.0.0-py3-none-any.whl
& $python -I -m unittest discover -s tests -v
```

Antes de uso jurídico, validar com referência humana em corpus representativo. Antes de distribuir com bibliotecas opcionais, concluir revisão das respectivas licenças. O autor não substitui essa decisão por checks verdes.

## Revisão de fonte e correções da rc3

A rc3 corrige defeitos reproduzidos da rc1: separadores e continuidade de mesclagens DOCX, duplicação de blocos centrais PDF, destinos remotos no Markdown produzido pelo adaptador avançado e avisos globais ausentes da saída. Os pareceres externos examinaram a rc1 congelada; os testes locais verificam as correções da rc3. Os pareceres e a matriz de tratamento acompanham o pacote de auditoria.

No Windows, o snapshot é mantido aberto negando escrita e exclusão durante extração/publicação; worker e supervisor conferem seu hash. Em outros sistemas, o handle de leitura não fornece a mesma trava obrigatória. Os arquivos e o manifesto são reconferidos imediatamente antes da publicação, incluindo tentativas limitadas de renomeação quando Windows retorna negação transitória de acesso. Isso não isola a pasta de saída de outro processo do mesmo usuário: use uma pasta de trabalho sob seu controle. `--no-tables` controla apenas detecção geométrica PDF; Office/CSV preservam suas células.

A saída avançada literaliza sintaxe de links/referências para evitar carregamentos remotos e conserva o texto modelado original no JSON para rastreio. Essa política pode simplificar a formatação. A prévia da interface permanece textual. Textos/Markdown de entrada são preservados e podem conter HTML ou links: use um renderizador com política segura quando abrir esses documentos em outro produto.

PDFs com anotações ou formulários exigem revisão; seus valores não têm extração completa assegurada. Texto estritamente não UTF-8 falha com diagnóstico: reconverta a codificação antes do uso. Esta candidata ainda requer corpus representativo e validação do MinerU real; testes sintéticos não medem acurácia geral.

## Correções finais da rc3

A revisão externa confirmou seleção indevida de Tesseract na pasta atual quando o motor privado não existe. O fallback agora considera somente diretórios PATH absolutos fora da pasta atual; entradas vazias/relativas são descartadas. A escolha explícita do usuário continua disponível. PowerShell e taskkill usam caminhos System32 absolutos, sem busca na pasta de documentos.

O instalador contém cada etapa e seus descendentes em Job Object Windows, com início suspenso/atribuição antes da retomada, encerramento ao fechar, 2 GiB e 32 processos. O término normal do pai também encerra seus filhos. A detecção de aplicativo antigo ativo inclui executáveis sob toda a release, inclusive OCR. É contenção de processo, não sandbox de filesystem/rede.

O bootstrap recusa nomes de membros iniciados por opção e encerra a interpretação de switches antes da lista de arquivos do extrator. A alegação de fuga usando dois switches `-o` foi refutada pelo extrator real, que recusou duplicidade; a validação foi endurecida mesmo assim. PNG avançado parcialmente gravado é removido se a normalização falhar. Avisos de limpeza de cópias temporárias aparecem também no log da UI.

Os registros e caminhos com acentos são lidos/transmitidos como UTF-8 nos dois launchers PowerShell. Preservados os casos de falha e os manifestos de cada alvo revisado; parecer de predecessor não é aprovação dos bytes finais. Configuração por TOML digitado usa modo/idioma mostrados na UI como overrides; outras chaves são aplicadas. No PDF, falhas de renderização/OCR são localizadas por página e preservam o texto nativo quando disponível; o resultado é parcial. Limites gerais de entrada, memória, prazo e saída ainda podem impedir a conversão do arquivo. Esses limites continuam explícitos, sem homologação de fidelidade.


## Correções da rc4: livros escaneados, pastas e chunks

PDFs escaneados com texto já existente usam esse texto no modo auto. Isso não garante que a camada OCR anterior esteja correta. Escolha **ocr** para refazer o reconhecimento, ou **por** para um documento somente em português quando não precisar dos seis idiomas simultaneamente. A instalação continua contendo os seis idiomas e o padrão de seleção continua por+eng+spa+ita+fra+deu.

A imagem que ocupa quase toda a página não é duplicada no resultado por padrão: o PDF original continua sendo a referência visual, identificado pelo SHA-256 e página. Isso também pode incluir ilustrações; `LEIA-AVISOS.txt` informa as páginas afetadas. A presença dessa imagem e o reaproveitamento do texto constam nas métricas do JSON. Ilustrações menores continuam sendo exportadas quando `images=true`. OCR usa um PNG temporário de alta resolução por página e o remove ao terminar. **Incluir prévias de páginas** na interface, `scan_previews=true` no TOML ou `--scan-previews` no CLI inclui prévias JPEG com perda, limitadas a 1.200 pixels na maior dimensão; essas prévias **não** são usadas como entrada de OCR. O teto final de 128 MiB permanece.

O progresso mostra a página corrente durante PDFs longos. `LEIA-AVISOS.txt` agrupa motivos e páginas afetadas; os detalhes técnicos ficam no JSON. “OCR não encontrou texto” também pode corresponder a capa, página ilustrada ou página ilegível: revise o original. Score de confiança não equivale a precisão medida. “Concluído” não é certificado de fidelidade.

**Chunks:** na interface, marque/desmarque Gerar chunks para IA/RAG; padrão 6.000 caracteres, sobreposição solicitada de 600. Os valores são caracteres Unicode, não tokens. O CLI requer `--chunks`; `--no-chunks` substitui configuração TOML. `--chunk-size-chars` e `--chunk-overlap-chars` ajustam os limites. Exemplo:

```powershell
& $python -I -m traslado convert 'C:\documentos\livro.pdf' -o 'C:\resultados' --chunks --chunk-size-chars 6000 --chunk-overlap-chars 600
```

Na subpasta `chunks/`, `chunks.jsonl` contém texto exato recortado de `document.md`, identificador determinístico, hashes da fonte/Markdown, offsets de início/fim em caracteres (fim exclusivo), títulos, páginas, blocos, motores, confiança, estado parcial e avisos. `manifest.json` registra parâmetros/quantidade; `0001.md` etc. são versões com metadados e links de imagens ajustados para a pasta pai. Tabelas e blocos de código são mantidos inteiros, podendo ultrapassar o tamanho pretendido com aviso. A sobreposição solicitada é um máximo. Se o início ideal cair no meio de uma palavra, o corte avança até o próximo espaço em branco (inclusive tabulação ou quebra de linha), reduzindo apenas o necessário. Na rc9, também avança sobre espaços iniciais até o próximo caractere não branco, sem ultrapassar o intervalo já coberto. Um início no começo de palavra é mantido. Quando a região inteira é uma palavra longa ou espaços, a sobreposição pode chegar a zero; a cobertura tem prioridade e não se pulam caracteres além do fim anterior. Sem espaço antes do fim do chunk anterior, a sobreposição pode ficar zero para não repetir uma palavra truncada; não há lacuna no texto. Quando preservar uma tabela, código, imagem ou aviso inteiro exige reduzir a sobreposição, o JSONL e o frontmatter informam `actual_overlap_chars`, `overlap_adjustment` e o aviso `chunk_overlap_reduced`. O primeiro chunk tem sobreposição 0 (`first_chunk`). O manifesto resume mínimo/máximo entre transições e quantidade de reduções; com apenas um chunk, mínimo/máximo são `null`. Os inícios e fins avançam, sem lacunas ou chunks inteiramente contidos no anterior. Não há garantia de contexto semântico. Chunks não geram embeddings nem indexam banco vetorial.


## Atualização rc5: OneDrive e diagnóstico

Arquivos comuns do OneDrive (tags Microsoft CLOUD) são aceitos no pacote, no cache OCR e nos documentos/resultados. Links, junctions e pontos especiais desconhecidos continuam recusados. Instale o aplicativo na pasta local padrão; não use uma pasta de nuvem como runtime. O cache é autenticado antes de preparar o Tesseract local. Em caso de falha, o instalador mostra a causa e o caminho do registro de cada etapa em `installation-logs` na pasta do aplicativo. Feche a janela anterior, extraia o novo ZIP e abra `INSTALAR.bat`. Não copie o candidato por cima do pacote anterior.


## Markdown para leitura e rastreabilidade (rc6)

`document.md` passa a ser o arquivo para leitura contínua: quebras físicas prováveis de prosa PDF/OCR são reunidas e os avisos de conversão são concentrados em `LEIA-AVISOS.txt`. Textos Markdown, listas, código, tabelas e imagens mantêm sua estrutura. Mesmo na entrada `.md`, o documento recebe um título inicial `# nome-do-arquivo.md`; não há identidade byte a byte com a entrada. `document-layout.json` mapeia os blocos de origem; esse título inicial fica fora dos intervalos de blocos. O fechamento do arquivo normaliza a quebra final de linha. O texto extraído bruto continua em `document.json`. O novo `document-layout.json` relaciona os intervalos exatos do Markdown às páginas e blocos de origem, inclusive quando os chunks estão desligados. Quando gerados, os chunks referenciam o hash e as posições do Markdown já formatado.

A formatação é conservadora: hífens ASCII ambíguos, números de páginas, cabeçalhos/rodapés e fronteiras sem evidência permanecem. Ela não reescreve o conteúdo nem corrige erros de OCR. Guarde a subpasta completa ao abrir no seu leitor Markdown, para manter imagens e arquivos auxiliares acessíveis.

## Correções rc7 e validação

O rc7 corrige perda de sobreposição por cortes em parágrafos; separa código inline de fences e parágrafos; escreve JSONL e Markdown incrementalmente, aplicando a cota durante a gravação; limita a grade final de tabelas irregulares antes do preenchimento; encerra descendentes POSIX no grupo supervisionado mesmo após saída normal do pai. A API `generate_chunks` continua retornando uma lista para compatibilidade; `write_chunks` usa um iterador. O texto do documento e o mapa de origem não mudam por causa da sobreposição. Os novos campos são adições compatíveis ao schema 1; identificadores mudam quando mudam os intervalos.

A suíte rc7 inclui regressões para prosa de parágrafos longos/variados, Unicode, sobreposição extrema, limites atômicos, quotas, preenchimento de tabelas e limpeza de subprocessos. Consulte `audit/RELATORIO-CORRECAO-V6-RC9.md` na raiz do ZIP para resultados, ambiente exato e testes não executados. Evidências anteriores estão em `audit/history-rc6/` e não aprovam esta release. O rc7 continua sendo candidato: instalação/GUI no Windows e OCR/qualidade em corpus jurídico real precisam de validação nesse ambiente.

## Ajuste rc8 — sobreposição em limite de palavra

O início de sobreposição não corta palavras. Para 600 caracteres solicitados, usa até 600 e avança até o próximo espaço se necessário, preservando fatias exatas e sem lacunas. `overlap_adjustment` distingue `word_boundary`, `atomic_boundary` e `word_and_atomic_boundary`, além de `none` e `first_chunk`. Tabelas, código e imagens continuam protegidos; o ajuste revalida as duas restrições. Relatórios do rc7 são históricos, em `audit/history-rc7/`; sua garantia de exatamente 600 foi substituída por este contrato a pedido do usuário.


## Correções rc9 — sobrescrita e validação OCR

A caixa **Sobrescrever resultado anterior** pode ser marcada; fica temporariamente desabilitada durante a execução e volta a ficar disponível depois. Padrão desmarcado. CLI: `--overwrite` / `--no-overwrite`; TOML: `overwrite = true` em `[conversion]`. A interface aplica a seleção da caixa às opções da conversão.

A sobrescrita usa a pasta principal com o nome da entrada, sem acrescentar `(2)`. Não procura nem elimina cópias numeradas antigas. Só substitui uma pasta com manifesto reconhecido, arquivos íntegros e da mesma entrada; arquivos editados, arquivos/pastas adicionais, links e colisões com outra fonte são recusados. Sem sobrescrita, cria outra pasta numerada. Para resultados rc6–rc8, que não registravam o caminho da fonte, a identificação usa o nome exato do arquivo: em colisões de nomes provenientes de pastas diferentes, mantenha a sobrescrita desmarcada. A partir da rc9, o manifesto também registra o caminho absoluto da entrada e as pastas geradas. Esse caminho acompanha o resultado quando compartilhado.

A nova conversão termina em pasta privada antes da substituição. A publicação reserva o destino, confere o resultado anterior e o renomeia para `.freoa-anterior-...`, no mesmo volume; depois publica o novo e remove o anterior somente após nova conferência. Falha de extração, quota ou cancelamento antes da publicação conserva a saída anterior. Falha na publicação tenta restaurá-la. Se a restauração falhar ou a saída anterior sofrer alteração, a cópia restante é conservada e seu caminho aparece no diagnóstico/log. Uma saída nova `partial` pode substituir a anterior: confira os avisos. A troca usa dois renames: não é uma transação contra queda de energia; uma interrupção entre eles pode deixar a cópia em `.freoa-anterior-...`. O bloqueio coordena instâncias do conversor; a pasta de saída continua sendo um local confiável do usuário.

OCR exige todos os idiomas selecionados. A rc9 consulta `--list-langs` com o executável/tessdata efetivos e verifica diagnósticos de carregamento após a execução. Não reduz silenciosamente `eng+fra+por` a `eng`. O bloco OCR registra `requested_languages`, `available_languages` e `language_validation`; uma listagem de modelos disponíveis não prova a qualidade do reconhecimento. `confidence` continua sendo média de escores do motor, não acurácia, e `fidelity` permanece `unverified`, inclusive com confiança alta. Trocas de ordinais, grafia e números exigem comparação com a página original; não são corrigidas por substituições heurísticas.

A rc9 também elimina espaços iniciais da faixa de sobreposição quando o intervalo já coberto permite, conservando offsets, hashes, proteção de tabelas/código e sobreposição efetiva. Evidências rc8 são históricas, em `audit/history-rc8/`. O relatório rc9 contém os resultados atuais e os limites de validação.
