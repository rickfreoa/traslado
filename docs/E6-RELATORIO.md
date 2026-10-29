# E6 — CI multiplataforma, E2E e auditoria

Estado em 29/10/2026: **CI aprovada** na branch `t20/e6`, commit `4484c18`, run 40827368727 (https://github.com/rickfreoa/traslado/actions/runs/40827368727): 37 jobs verdes — build em checkout limpo com testes da árvore-fonte, matriz Windows/Ubuntu/macOS × 3.11–3.14 × Core/Full e legacy 3.13, mutação, contenção nos três SO e consolidação, E2E Debian e Fedora mínimos por digest, Windows (Server Core registrado como limite: DLL de sistema exigida pelo PyMuPDF ausente; gates reprovam e nada é promovido) e macOS. Revisão independente: APROVADA_COM_RESSALVAS, ressalvas corrigidas (ver diário). As seções abaixo são o relatório original da implementação; onde divergem do estado atual, prevalece este parágrafo e o `release.yml` vigente (PyPI dentro de `release.yml`, job de verificação sem escrita, bytes liberados idênticos ao pacote testado no CI).

## Entregas e decisões

- `ci.yml`: 24 células Core/Full (Windows, Ubuntu, macos-14 × Python 3.11–3.14), mais três células Full+legacy em 3.13. Wheel instalado em venv novo e testes copiados para fora do checkout. OCR Windows pelo lock; Linux apt; macOS Homebrew somente na CI. O ensaio macOS exige arquitetura arm64 e reprova outra arquitetura. GUI por widgets/eventos sintéticos, conversão real, resultado e preview, com prazos.
- Jobs bloqueantes próprios de mutação nativa/E4, contenção nos três SO, consolidação da contenção e E2E. Todos dependem do build correspondente. Artefatos de diagnóstico são publicados com `if: always()`. Não se baixa uma release para executar E2E.
- `fuzz.yml`: dez alvos, Atheris obrigatório em Python 3.11, até 1200 segundos por alvo, timeout por entrada e limite de RSS. O parser é instrumentado e executado **no mesmo processo** do fuzzer; manter o parser no worker separado impediria observar sua cobertura. Há corpus inicial público, gravação de payload/metadados e captura dos artefatos nativos do libFuzzer. Não há fallback que se apresente como fuzz guiado por cobertura.
- `release.yml`: tag v* constrói e verifica composição/licença, versão, inventário e checksums; exige CI do mesmo commit; atesta todos os arquivos em `dist/` e cria apenas rascunho. Despacho manual constrói/verifica e entrega artefatos, sem criar release nem attestation publicada. `pypi.yml` separado usa OIDC e environment `pypi` após `release: published`, ou despacho com tag e confirmação `PUBLICAR`; exige release já publicada. Assim uma falha de PyPI não bloqueia nem reverte a release.
- `scripts/versao.py --conferir --dist dist`: fontes, instaladores, wheel, sdist, payload e tag. Ausência do diretório/instaladores reprova. O gate de composição compara membros esperados, detecta duplicações, exige LICENCE canônica e SPDX; os testes distribuídos no sdist também receberam cabeçalho SPDX, sem alterar os seus corpos. A allowlist de licenças e o lock legacy são obrigatórios no inventário.
- `skills/traslado/SKILL.md`: frontmatter, instalação, conversão/chunks, leitura dos artefatos, erros e proibição explícita de upload/OCR hospedado. Não foram usados documentos de clientes.

Correções pequenas necessárias ao ciclo: update local com hash do instalador conferido e versão de wheel sem ambiguidade; preservação do perfil/idiomas/atalhos; RECORD do wheel post1 em base64 URL-safe; caches do uv dentro da raiz gerenciada; remoção Windows diferida até a saída do próprio interpretador (o E2E espera e confere a remoção); diagnóstico `FIXTURE_GATE_FAILED` com a causa real. As alterações não reescrevem parsers, renderização, chunks ou bytes dos documentos convertidos. A CLI acrescenta instrução de instalação local a `needs_ocr`.

## Validação local

Python disponível: CPython 3.14/Windows, ambiente Full+legacy fornecido, utilizado somente para leitura. Temporários em `work/temp`. O Python 3.14 cria diretórios privados inacessíveis a este sandbox; simplesmente apontar TEMP/TMP para work não resolveu. Foi usado um `sitecustomize.py` **somente em work/test_support**, que muda o modo de mkdir nos temporários deste worktree, para continuar os testes do processo supervisor. Esse adaptador não integra o produto, o wheel nem os workflows; processos com `-I` continuam ignorando-o. Portanto esta execução local não substitui a CI.

| Verificação | Resultado e evidência local |
|---|---|
| YAML dos cinco workflows, inclusive chaves duplicadas | PASS, `work/check_yaml.py`; PyYAML 6.0.3 no Python base |
| Sintaxe Python 3.11 por AST | PASS, 39 módulos/scripts; não equivale a execução em 3.11 |
| Parser PowerShell de install.ps1 e ci/e2e_servercore.ps1 | PASS |
| Sintaxe shell por bash local | NOT_RUN: Git Bash não cria signal pipe neste sandbox (Win32 error 5) |
| Build offline com `--permitir-sujo`, composição/SPDX/licença | PASS com adaptação local; `work/build-final.log`; payload declara checkout sujo |
| Versão única em fontes e dist | PASS, 2.0.0 |
| Inventário | PASS mecânico: zero divergências, seis lacunas declaradas; não é auditoria jurídica individual das DLLs |
| Scripts E6, inventário, workflows e GUI | PASS: 17 testes, `work/final-checks.log` |
| Wheel instalado em venv novo fora do checkout | PASS: 11 testes, um skip de composição que exige manifesto do checkout; `work/wheel-proof-final.log`. Importação conferida sob work/wheel-proof/venv/Lib/site-packages |
| Contenção E2 Windows | PASS: três ensaios, `work/contencao-local.log` e `work/evidencias/contencao-windows.json` |
| Auditoria negativa de bibliotecas | PASS da recusa esperada: 72 módulos observados, 26 violações, incluindo Python externo; processo terminou com código 1. Não é aprovação da autonomia. JSON em `work/evidencias/auditoria-negativa-windows.json` |
| Bateria E5 ampliada | 30 testes, uma falha de bootstrap; repetição isolada confirma `WinError 5` na pasta temporária da fixture dentro do Python `-I`. `work/bootstrap-diagnostico.log` registra o erro tipado io e a recusa de promoção |
| Suíte completa diagnóstica em estado intermediário | FAIL: 444 testes em 1959,327 s; 14 falhas, 84 erros, 13 skips; exit 1. `work/tests-adaptado.log`. Não é uma validação integral do estado final |
| Regressões pytest E2/E3 | PASS: 42 testes no Python base + dependências Full do ambiente fornecido, com adaptação local; um aviso de cache inacessível. `work/pytest-diagnostico.log`. A CI instala pytest 9.1.1 e executa estas funções explicitamente contra o wheel |

O build estrito de checkout limpo depende do integrador registrar os arquivos novos. O build local usa `--permitir-sujo`, mantém a recusa padrão em CI e não escreve no índice. Os testes novos não rastreados são copiados explicitamente para a prova local do wheel; passarão a compor o sdist quando rastreados pelo integrador. Os logs e binários em work/dist são evidências locais ignoradas pelo Git.

## Matriz e E2E que somente a CI pode provar

| SO | Python/perfil | Runs/resultados reais |
|---|---|---|
| windows-latest | 3.11, 3.12, 3.13, 3.14 × Core/Full; 3.13 Full+legacy | NOT_RUN; link pendente do integrador |
| ubuntu-latest | 3.11, 3.12, 3.13, 3.14 × Core/Full; 3.13 Full+legacy | NOT_RUN; link pendente do integrador |
| macos-14 arm64 | 3.11, 3.12, 3.13, 3.14 × Core/Full; 3.13 Full+legacy | NOT_RUN; link pendente do integrador |
| Debian bookworm-slim / Fedora minimal 42 | E2E pelos digests fornecidos no brief | NOT_RUN; inventário inicial e pré-requisitos persistidos antes do bootstrap |
| windows-2022 | Perfil temporário, LOCALAPPDATA/HOME/APPDATA redirecionados | NOT_RUN; `.lnk`, conversão, update post1, rollback, remoção e arquivos remanescentes são verificados |
| macos-14 | HOME temporário, Applications temporário | NOT_RUN; Info.plist, launcher executável, conversão, update, rollback, remoção e auditoria |
| Windows Server Core | Só se Docker informar OSType=windows | NOT_RUN; ausência de daemon/modo Windows vira limite registrado, não sucesso de CLI; havendo suporte, falha de instalação/conversão reprova |

O digest Server Core foi localizado no [Microsoft Artifact Registry](https://mcr.microsoft.com/en-us/artifact/mar/windows/servercore/tag/ltsc2022): `sha256:a23b350061d76236e2c427e32175a2decfe3214200eee4ae9ee9cd9e98f26bf0`. Pull/execução e compatibilidade com o host não foram verificados localmente. Os runners usam rótulos fixos pedidos no brief, cujas imagens ainda podem receber atualizações.

Há um bloqueio potencial explícito nas imagens Linux: o bootstrap uv usa `tar`/`gzip`. O ensaio **não instala** tar, gzip, findutils, Python, Tk, uv ou Tesseract; só instala curl/certificados, conforme autorizado. Se a imagem fixada não trouxer tar/gzip, reprova com `limite-bootstrap-<distro>.txt`. Este caso ainda não foi observado executando a imagem neste ambiente e não foi declarado aprovado. Evitar esse requisito demanda um bootstrap adicional, que não foi improvisado sem validação. Em Linux headless, a auditoria registra dependências ausentes de `_tkinter`; o smoke gráfico é executado no runner com display/Xvfb.

A fixture DOCX pública contém o aviso esperado `docx_formatting_partial`; sua conversão é parcial por desenho. O E2E e a auditoria aceitam somente esse parcial conhecido sem error_code, exigem a sentinela textual e artefatos/chunks. A fixture PDF exige a sentinela correspondente. O modo degradado exige needs_ocr, página 1 e comando local de instalação.

## Contenção consolidada

Medição local desta retomada. Pico Windows = memória comprometida do Job, não RSS. POSIX = maior RSS agregado amostrado; Linux também impõe RLIMIT_AS por processo, enquanto macOS monitora sem teto de memória do kernel. NOT_RUN não é zero. O caso timeout tem prazo de 10 segundos e teto de memória de 256 MiB.

| SO/caso | Imposto / monitorado | Pico MiB | Teto MiB | Até encerramento s | Após detecção s | Processos / temporários remanescentes |
|---|---|---:|---:|---:|---:|---|
| Windows/filho | Job Object / prazo | 263,070 | 256 | 1,706 | 0,080 | 0 / 0 |
| Windows/pai e neto | Job Object / prazo | 261,703 | 256 | 1,216 | 0,151 | 0 / 0 |
| Windows/timeout | Job Object / prazo | 28,910 | 256 | 10,350 | 0,312 | 0 / 0 |
| Linux/todos | RLIMIT_AS / RSS, prazo | NOT_RUN | 256 configurado | NOT_RUN | NOT_RUN | NOT_RUN |
| macOS/todos | Não / RSS, prazo | NOT_RUN | 256 configurado | NOT_RUN | NOT_RUN | NOT_RUN |

A CI publica contencao-windows.json, contencao-linux.json e contencao-macos.json. `consolidar-contencao` produz artefato e tabela no resumo do run; ausências/falhas reprovam a consolidação. Substituir as linhas NOT_RUN somente pelos resultados do mesmo commit. A contagem de temporários comprova a remoção do diretório do harness, conforme a limitação já descrita em CONTENCAO.md.

## Bibliotecas, zona e quarentena

Allowlist versionada por SO: raiz gerenciada e diretórios do sistema; caminhos externos conhecidos são explicitamente proibidos. Matching permitido é por prefixo com separador, sem aceitação de uma substring no meio de um caminho. A auditoria faz extração real no processo observado, além das conversões supervisionadas, e combina módulos/maps com ldd ou otool/DYLD/vmmap. Enumeração vazia não aprova. Falhas deixam JSON com causa.

A auditoria positiva de autonomia nos três SO está NOT_RUN. No macOS a prova será de carregamento, não de imagem virgem. Tokens relocáveis de otool são complementados pela observação runtime. Dependências de Tk ausentes em Linux headless permanecem explícitas no JSON, não são evidência de GUI funcionando.

Zona Windows: o ensaio marca ZIP, BAT e PS1 com ZoneId=3, chama o BAT e mede RemoteSigned/Bypass. Usa Profile inválido para testar o despacho sem repetir downloads/instalação; registra stdout, stderr e códigos, sem interpretar todo exit não zero como MOTW. **Não mede o fluxo gráfico SmartScreen/Explorer nem garante propagação do ADS na extração do ZIP**. macOS aplica xattr em instalar.command e no bundle e registra spctl; não automatiza os diálogos do Finder/Gatekeeper. Ambos estão NOT_RUN neste ambiente; nenhum resultado visual é prometido.

## Diagnóstico da suíte completa e bloqueios reproduzidos

A tentativa integral terminou, com exit 1, e não foi declarada verde. Muitos erros incluem PermissionError/WinError 5 nos temporários criados pelo Python isolado ou em `.t11-logs`; o adaptador limitado a work não os corrige. Outros dependem do estado local: pytest ausente no venv somente leitura (corrigido na instalação de testes da CI; 42 funções passaram no Python base); importação `-I` sem pacote instalado no teste de rede legacy (a CI usa wheel instalado); cópia de checkout em testes de build sem os arquivos novos ainda não rastreados; teste de inventário carregado antes da inclusão da allowlist/lock nos seus arquivos de apoio (o teste atualizado passou na bateria final). A GUI teve falha de prazo/entrada durante a execução pesada; a bateria final usa 60 s de diagnóstico inicial, 60 s de conversão, e passou. Este resultado focal não elimina a falha integral histórica.

Reprodução focal em `work/legacy-diagnostico.log`: dois métodos executados, três falhas de asserção:

- DOC público: chamadas de notas `e1`, `e2`, mas o oráculo exige `1`, `2`.
- PPT público: tipos `heading:1, list_item:2`, mas o oráculo exige `heading:1, paragraph:2`.
- ZIP legado com método não suportado/flag de criptografia: o teste espera ConversionError em sniff e não a recebe. A interação entre detecção tolerante e erro tipado de conversão exige decisão de contrato; o teste não foi enfraquecido.

Esses casos permanecem bloqueantes, inclusive no job de mutação/E4 e nas células legacy. Corrigir as duas diferenças semânticas poderia mudar os bytes da conversão, vedado neste brief. Não foram atribuídas ao sandbox. A execução E4 de mutações também foi interrompida por WinError 32 ao remover `ods107/.../private/worker.log`, ainda aberto; há payloads retidos em work/e4-mutant-failures. A mutação nativa terminou `ok` na suíte diagnóstica, mas emitiu ResourceWarning de subprocessos ainda ativos; isto não comprova ausência de remanescentes para todos os mutantes. O teste de contenção comprova apenas os três cenários específicos medidos. Uma diferença de comparação OCR serial/paralelo também apareceu na suíte completa; requer reprodução sem o bloqueio de temporários antes de imputar regressão ao produto.

Resumo detalhado das exceções: `work/full-diagnostico.txt`. O integrador deve repetir a suíte completa no estado final em CI e devolver os logs; os resultados locais focais não autorizam marcar o conjunto como aprovado.

## Achados de fuzz e riscos pendentes

Para cada achado: baixar o artefato público do alvo; reproduzir com o harness e `--reproduce`; minimizar preservando a falha; conferir que o payload continua público/sintético; adicionar a fixture a tests/fixtures e teste que exija erro tipado ou conteúdo esperado, nunca só ausência de crash; corrigir a causa; abrir PR. A fixture deve reprovar a revisão anterior e passar na corrigida. Não promover automaticamente um corpus nem carregar documentos privados. Falhas nativas do libFuzzer podem ser reproduzidas apontando o arquivo crash/timeout para o mesmo alvo. O fuzz foi escrito, mas Atheris/libFuzzer não foi executado localmente no Windows.

Pendências externas: configurar trusted publisher/environment pypi; executar os runs da branch/tag, os dois contêineres e auditorias positivas; registrar resultados reais de arquitetura, quarentena e alertas; confirmar disponibilidade dos digests e das wheels para toda a matriz; manter as seis lacunas de inventário visíveis. As permissões, hashes e testes mecânicos não substituem avaliação de licença das DLLs nativas nem aprovação humana para publicação.
