# Contenção de recursos — Traslado 2.0

O Core usa somente a biblioteca padrão. Estes mecanismos limitam recursos e
encerram processos; não isolam acesso a arquivos nem à rede.

| SO | Imposto pelo SO | Monitorado | Pico observado (MiB) | Teto configurado (MiB) | Encerramento (s) | Após detecção (s) | Processos / temporários remanescentes |
|---|---|---|---:|---:|---:|---:|---|
| Windows | Job Object: memória comprometida agregada, 32 processos, kill-on-close; atribuição antes de retomar o filho | Prazo, log e orçamento de disco | 263,305 | 256 | 11,505 (máximo dos casos) | 1,454 (máximo) | 0 / 0 |
| Linux | `RLIMIT_AS` por processo, herdado pelos descendentes | RSS agregado do grupo via `/proc`, a cada 250 ms; prazo, log e disco | E6: não executado | 256 | E6 | E6 | E6 |
| macOS | Sem teto de memória imposto pelo SO nesta etapa | RSS agregado do grupo via `ps`, a cada 250 ms; prazo, log e disco | E6: não executado | 256 | E6 | E6 | E6 |

O monitor POSIX detecta o excesso depois de ele ocorrer. Não é teto rígido de
RSS. A soma cobre processos que permanecem no grupo da conversão, inclusive o
Tesseract; um programa que crie outra sessão pode escapar desse monitor. O
supervisor abre uma sessão nova e encerra o grupo com `SIGKILL`. Chamadas de OCR
usam explicitamente a sessão do worker, cujo supervisor contabiliza o grupo
inteiro. No Linux, um wrapper Python aplica `RLIMIT_AS` antes de `exec`, evitando
`preexec_fn` em threads. Um teto herdado menor nunca é elevado. No Windows, o
Job Object existente continua responsável por toda a árvore.

O pico Windows é `PeakJobMemoryUsed`, memória comprometida do Job, não RSS. Pode
incluir contabilização transitória acima do limite configurado; o teste comprova
a recusa da alocação (`MemoryError`, código 42), sem afirmar igualdade entre pico
observado e teto. O pico POSIX é a maior soma amostrada de RSS; pode perder picos
entre amostras. `RLIMIT_AS` pode recusar uma alocação antes do excesso de RSS.

## Reprodução e evidência

Com `PYTHONPATH=src`, executar `python -X utf8 -m unittest discover -s tests`.
Os testes E2 gravam `tests/_medicoes/contencao-<sys.platform>.json`, ignorado pelo
Git. Há provas com filho alocador, pai e neto alocadores e timeout com neto. O
JSON registra pico em bytes, teto, duração desde o lançamento, intervalo entre
detecção e encerramento, PIDs observados e remanescentes. No Windows também são
consultados os PIDs ainda pertencentes ao Job antes de fechá-lo; em POSIX se
confere o grupo. Zombies POSIX não contam como processos vivos nem consomem RSS.
O teste reprova remanescentes ou mais de 5 s entre detecção e encerramento.
O prazo do teste de timeout é 10 s para acomodar início de processo sob carga.

O harness remove seu diretório privado antes de contar temporários remanescentes;
essa contagem comprova a limpeza do harness. A limpeza de staging e PNGs na
conversão continua coberta pelos testes existentes de OCR, cancelamento e timeout.
Não se altera nenhum conteúdo de artefato em conversões bem-sucedidas.

## Disco de OCR

TXT, TSV e logs de Tesseract ficam em `package/ocr-*`, já dentro de `budget_root`.
Os PNGs de página ficam em `.freoa-ocr-page-*`, irmãos de `package` dentro do
staging privado daquele trabalho. O supervisor soma essas pastas ao orçamento
de `package`, inclusive numa checagem após a saída do filho. Não se soma o
snapshot de entrada ao orçamento de saída. A checagem é amostrada: não impõe
quota de filesystem e admite excesso transitório entre polls.

## Instalação e integração E5/E6

`traslado.platform` aceita SO e ambiente injetados para raízes e diretórios
injetados para atalhos. `where` retorna erros de leitura, usa somente o manifesto
current para resolver Python e só consulta raízes legadas existentes. Candidato
não redireciona a conversão. A leitura é tolerante; a promoção é estrita quanto
a gates booleanos e presença da pasta da release. `revocation=blocked` também
impede promoção; a verificação dos canais de revogação pertence à E5.

JSON é gravado em temporário no mesmo diretório, com flush/fsync e `os.replace`.
Depois da troca, o candidato é removido. Isso torna atômico cada arquivo, não o
conjunto de operações nem a durabilidade contra falha elétrica. E5 deve serializar
promoções e reversões. Manifestos anteriores são guardados em
`state/history/<release_id>.json`, sem escrever na release imutável. Rollback usa
esse histórico para recuperar também perfil, OCR e Python explícito; sem histórico
(instalação externa anterior), troca o ID e deriva o Python convencional.
O runtime 1.x permanece inalterado para preservar a leitura da configuração antiga;
E5 pode usar diretamente a nova API de raiz gerenciada.

Windows cria `.lnk` por PowerShell/WScript.Shell e resolve a Área de Trabalho pelo
Known Folder, com fallback para `~/Desktop`. Linux instala `.desktop` e PNG em
pastas do usuário; `update-desktop-database` é opcional. macOS cria bundle e launcher
executável. O PNG original 256×256 é regenerável por `scripts/gerar_icone.py` e
incluído no pacote. `.ico` e `.icns` não são fornecidos nesta etapa.
E6 deve executar as mesmas provas nativamente em Linux/macOS e preencher suas
medições; conteúdo dos atalhos e parsers POSIX são testados por injeção no Windows.

O build direto do wheel inclui módulo e PNG. A lista explícita
`release-files.txt` ainda precisa receber `src/traslado/platform.py` e
`src/traslado/assets/traslado.png` na integração/E5: esse arquivo está fora do
escopo de escrita da E2. Sem isso, o build de distribuição que filtra a árvore
pela lista pode omitir os arquivos novos, mesmo com package data correto.

## Medições Windows

Medição dos testes E2 focados nesta retomada em 28/10/2026, sob carga, incluindo
a espera até todos os PIDs observados terminarem (`drain_s` no JSON):

| Caso | Pico comprometido (MiB) | Teto (MiB) | Até encerramento (s) | Após detecção (s) | Processos vivos | Temporários |
|---|---:|---:|---:|---:|---:|---:|
| Filho alocador | 263,305 | 256 | 2,987 | 0,112 | 0 | 0 |
| Pai e neto alocadores | 262,156 | 256 | 5,012 | 0,282 | 0 | 0 |
| Timeout (prazo de 10 s) | 29,508 | 256 | 11,505 | 1,454 | 0 | 0 |

Os dois alocadores terminaram com código 42, emitido pelo fixture ao receber
`MemoryError`; o timeout levantou `Tempo limite excedido`.

## Decisões de validação

A retomada preservou as mudanças parciais e acrescentou aos testes de memória a
exigência do código 42 (recusa de alocação pelo SO) ou da mensagem exata do monitor
POSIX; um crash arbitrário não comprova contenção. Os 14 testes E2 passaram em
62,892 s (`work/e2-resume-focused.log`, exit code 0). O baseline em `work/baseline`
foi comparado byte a byte ao HEAD nos módulos usados: nele o teste de plataforma
falha por ausência do módulo, e as sondas de contenção anteriores falharam pela
ausência da instrumentação e da contagem dos PNGs irmãos.

O wheel direto foi novamente construído offline e conferido: inclui exatamente
os bytes do módulo e do PNG da árvore, sem dependências obrigatórias no metadata.
O gerador reproduz o PNG 256×256 byte a byte; seus chunks e CRCs foram conferidos.
Não há arquivo de plano nesta árvore; os itens de plataforma e contenção foram
conferidos contra a seção 2 de `CONTRATOS-2.0.md` e o resumo do plano no brief E2.

Os testes de diagnóstico do instalador agora criam seu próprio payload temporário,
com uma cópia real de `windows_job.py` e o hash correspondente. Não dependem mais
do `INSTALL-PAYLOAD.json` ignorado na raiz do checkout. O instalador não foi editado.
Dois testes antigos exigiam início de subprocessos antes de 4 s e 1,5 s; sob carga
os processos ainda não tinham produzido os recibos. A repetição isolada reproduziu
isso. As janelas dos fixtures passaram a 12 s (OCR) e 8 s (instalador), mantendo
recibos obrigatórios e verificação real de ausência de descendentes. O cancelamento
de OCR aceita até 30 s para iniciar os quatro workers, com limite total de 35 s.
Esses valores não alteram prazos nem opções do produto.

Na retomada, a única execução integral terminou com 403 testes em 1.346,305 s:
385 passaram, 17 foram ignorados e um falhou (exit code 1). A falha foi
`test_timeout_persists_child_diagnostic`: o filho não escreveu `BEFORE_TIMEOUT`
na janela de 1 s. A repetição isolada sem alteração reproduziu a falha em
1,595 s. Somente o prazo desse fixture passou de 1 s para 8 s, mantendo timeout
obrigatório e a conferência do conteúdo persistido. Os quatro testes da classe
`InstallerDiagnosticsRC5` passaram no estado final em 13,655 s (exit code 0).
Registros: `work/suite-resume.log`, `work/resume-isolated.log` e
`work/resume-isolated-final.log`, acompanhados dos respectivos arquivos `.exit`.

A suíte integral não foi repetida, conforme o brief. Portanto o registro bruto
integral é FAILED; a correção posterior tem validação focada verde. Todo o código
de produto e os demais testes do estado final são idênticos aos executados na
cópia da suíte; a única diferença funcional posterior é essa janela de fixture.
Os 17 testes ignorados não são apresentados como executados.

Os dois testes existentes de custo linear de chunks usam agora lotes de pelo menos
125 ms de CPU por amostra, em vez de uma chamada curta medida por relógio de parede.
Mantêm três amostras, o menor custo por chamada, o critério de menos de 8x para
entrada 4x maior e a contagem determinística existente. Isso reduz interferência
do escalonador sem relaxar o critério de complexidade nem alterar `chunks.py`.

Arquivos de produto: `src/traslado/platform.py`, `safety.py`, `windows_job.py`,
`gui.py` (somente HiDPI), `assets/traslado.png`, `scripts/gerar_icone.py` e
`pyproject.toml`. Testes: os novos `test_platform_e2.py` e `test_containment_e2.py`,
mais os ajustes de fixtures/tempo em `test_onedrive_rc5.py`,
`test_ocr_concurrency_t11.py`, `test_security_review_rc3.py`,
`test_chunks_prose_fence_p6.py` e `test_chunks_unclosed_p6.py`. Completam a mudança
este documento e `.gitignore`. CLI, runner, runtime e instaladores não foram editados.

A validação usa um venv em `work/`, com adaptação local para a ACL de diretórios
0700 do Python 3.14 neste ambiente. O build offline usa os pacotes locais, com
`PIP_NO_INDEX=1` e `PIP_NO_BUILD_ISOLATION=0` (esta opção invertida do pip desativa
o isolamento). A suíte também é executada numa cópia em `work/final-snapshot`,
pois seus testes de release geram arquivos fora do escopo do checkout principal.
O índice dessa cópia espelha o original: os arquivos novos da E2 permanecem não
rastreados. Nenhum commit foi criado. Isso não homologa o build filtrado após a
integração; a pendência de `release-files.txt` acima continua expressa.

Sugestões de commits para o integrador: `feat: adicionar plataforma e atalhos do
Traslado` e `fix: conter memória e temporários da árvore de conversão`.


## Correção da revisão independente E2/E3

Promoção e rollback usam o mesmo lock exclusivo `state/promote.lock`. Um lock
com menos de dez minutos recusa a operação; um lock mais antigo é registrado
no log e retomado, com serialização da disputa entre retomadores. O lock é
removido ao sair, inclusive em erro. `current.json` existente e ilegível impede
a promoção, e uma re-promoção preserva a referência anterior de rollback.

O supervisor lê no máximo 257 bytes de `mimetype` e 65.537 bytes de
`[Content_Types].xml` por stream ZIP; descarta conteúdo acima dos tetos de
256/65.536 bytes. Deflate inválido, EOF, criptografia e compressão não suportada
fazem o detector devolver `None`. O worker e `check_archive` decidem o erro.

A contenção registra `limit_bytes`, `peak_bytes`, `detected_after_s` e
`rlimit_applied`. Sem `error.json`, NTSTATUS de falta de memória ou as mensagens
`MemoryError`, `out of memory`, `bad_alloc` e `cannot allocate memory` indicam
`resource_limit`. Em término por SIGKILL, SIGSEGV ou SIGABRT, também se admite
`resource_limit` se o monitor acionou a contenção ou se o RLIMIT_AS foi aplicado
e o pico de RSS medido chegou a pelo menos 90% do teto. Sem esses indícios, um
SIGSEGV continua `internal`. RSS mede memória residente; uma falha precoce por
espaço virtual sem diagnóstico pode permanecer `internal`. As duas chamadas
OCR recebem o orçamento `max_memory_mb` configurado. Erros ao ler um PID não
interrompem a inspeção dos demais processos.

Atalhos com `home` injetado usam somente os diretórios desse `home`, salvo
diretórios explicitamente fornecidos. No Linux, `Exec` aplica primeiro o escape
do argumento e depois o escape geral de string da
[Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry/latest/exec-variables.html).
O nome normalizado determina os arquivos `.desktop` e `.png`; a remoção elimina
duplicatas.

A validação desta correção usa `work/venv` com temporários em `work/temp`.
O hook local `validation_acl.py` substitui somente a criação de diretórios
0700 por diretórios com ACL herdada no ambiente de testes Windows. Ele não faz
parte do produto. A suíte integral roda uma vez em `work/final-snapshot`, cópia
isolada com os mesmos bytes finais de código e testes, para conter os arquivos
que os próprios testes de release geram. Logs e códigos de saída ficam em
`work/focused.log`, `work/focused.exit`, `work/full-suite.log` e
`work/full-suite.exit`.

A comparação com `a50991b` reaproveita as fixtures de `test_registry_t11.py`,
acrescenta HTML completo ao fragmento HTML (20 casos) e executa as duas pipelines
reais com `mode=native` e chunks. Somente o relógio de `worker.main` é fixado em
zero; os relógios de timeout permanecem reais. Todos os arquivos publicados são
comparados byte a byte, inclusive manifests, com hashes por arquivo em
`work/parity/result.json`. Resultado: **20 fixtures, 170 arquivos, zero diferenças**.


Resultados finais desta correção (Windows, Python 3.14.6):

- Focados E2/E3, registro, OneDrive, segurança e os 42 novos casos da revisão:
  **101 aprovados, 3 ignorados e 53 subtestes aprovados**, exit 0, 137,08 s.
- Única execução integral: **442 aprovados, 13 ignorados, 8 falhas, 1 erro de
  limpeza e 229 subtestes aprovados**, exit 1, 394,78 s. As oito falhas são as
  conversões de `CompatE1`; o erro de limpeza ocorre no fim dessa mesma classe.
  Os venvs filhos dessa classe não herdaram o hook de ACL e criaram diretórios
  temporários 0700 inacessíveis ao token do sandbox. O erro aparente de
  `Path(None)` decorre de a conversão não ter produzido pasta de saída.
- Depois de propagar o mesmo hook de ACL aos venvs criados pelos testes,
  repetiram-se somente as oito conversões e `test_worker_success`:
  **9 aprovados**, exit 0, 17,50 s (`work/compat-recheck.log` e `.exit`). Não houve
  alteração no produto ou nos testes entre a execução integral e essa repetição.
  A execução integral permanece registrada como FAILED; não foi repetida.
- Paridade contra `a50991b`: **20 fixtures, 170 arquivos, zero diferenças**,
  exit 0. Código e testes da cópia integral: **66 arquivos Python idênticos**
  (`work/snapshot-parity.json`). `git diff --check`: exit 0.

Os 13 casos ignorados não são apresentados como executados. A validação real
ocorreu no Windows; os parsers de `/proc`/`ps` e as condições de sinais POSIX
foram exercitados por fixtures e injeções controladas, não em um kernel Linux
ou macOS. O hook de ACL e sua propagação pertencem somente a `work/venv`.
