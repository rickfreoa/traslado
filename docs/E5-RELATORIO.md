# Relatório da Etapa 5 (E5) — Bootstrap Remoto (`uv`), Ciclo de Vida, OCR Portável e Revogação em Dois Canais

- **Branch**: `t20/e5`
- **Base**: `fd50fd2` (`Merge branch 't20/e1' into t20/integracao` — E1, E2, E3 e E4 integrados)
- **Data**: 2026-10-28

---

## 1. Resumo Executivo

A Etapa 5 (E5) implementou o ciclo completo de bootstrap remoto e local do **Traslado 2.0.0** com Python 3.13 gerenciado pelo `uv`, locks universais com hashes (`--universal --generate-hashes`), subcomandos de ciclo de vida na CLI (`setup`, `where`, `update`, `uninstall`, `rollback`), reconhecimento não destrutivo da instalação legada no Windows (`%LOCALAPPDATA%\Freoa\MarkdownConverter`), remoção pós-extração do 7-Zip na instalação do OCR e protocolo de revogação em dois canais independentes (`revoked.json` autenticado por attestation + API de Releases do GitHub).

Nenhuma alteração foi feita nos módulos de conversão (`src/traslado/pdf.py`, `src/traslado/formats.py`, `src/traslado/chunks.py`, `src/traslado/render.py`, `src/traslado/model.py`), mantendo intactos os bytes de saída de conversão.

---

## 2. Arquivos Criados e Alterados

### Arquivos criados
- `locks/core.in`, `locks/full.in`, `locks/legacy.in` — entradas de dependências por perfil.
- `locks/requirements-core.hashes.lock`, `locks/requirements-full.hashes.lock`, `locks/requirements-legacy.hashes.lock` — locks universais gerados com `uv pip compile --universal --generate-hashes --python-version 3.13`.
- `scripts/gerar_locks.py` — utilitário reprodutível para regenerar e verificar (`--verificar`) os três locks universais.
- `scripts/gerar_fixtures.py` — gerador determinístico (somente biblioteca padrão, sem strings de `MuPDF`/`PyMuPDF`) das fixtures embutidas de fumaça.
- `src/traslado/assets/fixture.docx`, `src/traslado/assets/fixture.pdf` — fixtures mínimas válidas empacotadas no wheel para o portão `fixture`.
- `src/traslado/assets/ocr-assets.lock.json`, `src/traslado/assets/bootstrap_ocr.ps1` — cópias embutidas no wheel para permitir que `traslado setup` instale o OCR mesmo quando invocado apenas a partir do wheel baixado pelo bootstrap remoto.
- `src/traslado/revocation.py` — implementação da verificação de revogação em dois canais (`verify_promotion`), validação de monotonia de `sequence` e validade (`expires_at`), persistência em `<root>/state/revocation.json` e CLI de emissão de `revoked.json`.
- `revocation/revoked-list.json` — lista base versionada de revogações (`schema: "traslado-revoked-1"`, `sequence: 1`, `revoked: []`).
- `.github/workflows/revocation.yml` — workflow semanal (`cron: '0 6 * * 1'`) e manual (`workflow_dispatch`) com todas as actions fixadas por SHA completo de 40 caracteres, emitindo `revoked.json` com validade de 14 dias, gerando attestation via `actions/attest-build-provenance` e publicando na tag fixa `revocation`.
- `src/traslado/lifecycle.py` — implementação de `setup_release`, `setup_ocr`, `handle_legacy_windows`, `evaluate_doctor_gate`, `run_fixture_gate`, `uninstall_managed` e `update_release`.
- `install.ps1` — bootstrap PowerShell 5.1+ para Windows (`irm <url> | iex` e `-File`).
- `install.sh` — bootstrap POSIX `/bin/sh` para Linux e macOS (`curl -fsSL <url> | sh` e `sh install.sh`).
- `instalar.command` (macOS) e `instalar.sh` (Linux) — alternativas clicáveis que delegam a `install.sh`.
- `tests/test_lifecycle_e5.py` — suíte de testes unitários e integrados (sem rede) cobrindo os 9 cenários exigidos pela E5.
- `docs/E5-RELATORIO.md` — este relatório.

### Arquivos alterados
- `INSTALAR.bat` — atualizado com cabeçalho SPDX `AGPL-3.0-or-later`, URL da árvore correspondente (`https://github.com/rickfreoa/traslado/tree/v2.0.0`) e delegação para `install.ps1` quando presente (mantendo fallback para `INSTALAR.ps1`).
- `src/traslado/cli.py` — adicionados os subcomandos `setup`, `where`, `update`, `uninstall` e `rollback`.
- `pyproject.toml` — incluídos `assets/*.docx`, `assets/*.pdf`, `assets/*.json` e `assets/*.ps1` em `[tool.setuptools.package-data]`.
- `release-files.txt` — incluídos todos os novos arquivos rastreados distribuídos no pacote/wheel.
- `scripts/build_release.py` — atualizado para produzir também `traslado-2.0.0.tar.gz` (sdist determinístico com código, testes, scripts e `LICENCE`), `install.ps1`, `install.sh` e `requirements-{core,full,legacy}.hashes.lock` em `dist/` e em `SHA256SUMS`, inspecionando recursivamente arquivos `.tar.gz` contra bytes de MuPDF/PyMuPDF.
- `tests/test_release_t11.py` — atualizado o conjunto esperado de artefatos em `SHA256SUMS`, adicionada verificação de `sdist` determinístico e sem MuPDF, incluído `revocation.yml` na verificação de actions fixadas por SHA de 40 caracteres e adicionados `lifecycle` e `revocation` em `Nucleo.MODULOS`.
- `README.md` — adicionada a seção `## Instalação` com o controle primário via `gh attestation verify`, o atalho one-liner para Windows, Linux e macOS, o aviso expresso sobre ausência de certificado de assinatura de código e alertas do sistema operacional, e os comandos de ciclo de vida.

---

## 3. Decisões Técnicas Registradas

1. **Versão e hashes SHA-256 fixados do `uv`**:
   - Versão fixada: **`0.13.6`** (conferida diretamente nos arquivos `.sha256` oficiais da release `0.13.6` de `astral-sh/uv`):
     - `x86_64-pc-windows-msvc` (`uv-x86_64-pc-windows-msvc.zip`): `a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f`
     - `x86_64-unknown-linux-gnu` (`uv-x86_64-unknown-linux-gnu.tar.gz`): `f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645`
     - `aarch64-unknown-linux-gnu` (`uv-x86_64-unknown-linux-gnu` / `aarch64`): `229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d`
     - `aarch64-apple-darwin` (`uv-aarch64-apple-darwin.tar.gz`): `60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472`
     - `x86_64-apple-darwin` (`uv-x86_64-apple-darwin.tar.gz`): `63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02`
2. **Preservação byte a byte de `ocr-assets.lock.json`**:
   - `scripts/inventario.py` valida o hash `ocr_assets_lock_sha256` em `THIRD-PARTY-INVENTORY.json`. Como `ocr-assets.lock.json` já contém os 6 arquivos `.traineddata` (`por`, `eng`, `spa`, `ita`, `fra`, `deu`) e `TESSDATA-LICENSE.txt` com URLs, tamanhos e SHA-256 exatos para todas as plataformas, o arquivo foi preservado inalterado e embutido também em `src/traslado/assets/ocr-assets.lock.json` para uso autônomo pelo wheel.
3. **Expurgo completo do 7-Zip no Windows**:
   - Após a execução de `bootstrap_ocr.ps1`, `lifecycle._purge_seven_zip` remove `.bootstrap-tools`, `.ocr-download-cache` e qualquer arquivo `7z.exe`, `7z.dll`, `7zr.exe` ou `7z2603-x64.exe` da árvore da instalação, preservando `ocr/doc/7ZIP-LICENSE.txt`. Além disso, quando nenhum cache OCR externo é informado, `setup_ocr` utiliza um diretório temporário externo para o download intermediário do instalador do 7-Zip.
4. **Isolamento de migração legada e `data_root` em raízes customizadas**:
   - Quando `--root` aponta para uma raiz customizada (como em testes de isolamento em `%TEMP%`), `handle_legacy_windows` inspeciona apenas `TRASLADO_LEGACY_ROOT` (se definido) ou `<new_root>/../Freoa/MarkdownConverter`, garantindo que testes locais nunca alterem `%LOCALAPPDATA%\Freoa\MarkdownConverter` da máquina hospedeira.

---

## 4. Provas de Teste e Validação Real no Windows

### 4.1. Suíte de testes automatizados (`unittest`)
- `tests/test_lifecycle_e5.py`: **15 testes, 0 falhas** (cobrindo os 9 itens da especificação E5 sem uso de rede).
- `tests/test_release_t11.py`: **43 testes, 0 falhas, 1 skipped** (incluindo determinismo de `.zip`, `.whl`, `.tar.gz`, `SHA256SUMS`, ausência de PyMuPDF/MuPDF, `inventario.py` com 0 divergências e actions fixadas por SHA de 40 caracteres em `ci.yml`, `release.yml` e `revocation.yml`).
- Suíte completa (`C:\dev\t11-venv\Scripts\python.exe -X utf8 -m unittest discover -s tests`): **436 testes executados (`OK, skipped=13`), 0 falhas, 0 erros**.

### 4.2. Bootstrap real no Windows (`install.ps1 -FromDir` em `%TEMP%`)
Executado contra os artefatos reais gerados por `scripts/build_release.py --permitir-sujo` em raiz temporária sob `%TEMP%`:
- `uv 0.13.6` baixado e verificado por SHA-256;
- CPython `3.13.18 (main, Oct 26 2026) [MSC v.1944 64 bit (AMD64)]` provisionado em `<temp_root>/python` e `.venv` criado em `<temp_root>/releases/2.0.0_e4fa8c7304ca13f459e0eb3873321e28/.venv`;
- 9 pacotes do perfil `Full` instalados via `requirements-full.hashes.lock` com `--require-hashes --only-binary :all:`;
- `traslado-2.0.0-py3-none-any.whl` instalado sem dependências externas;
- Tesseract `5.5.3.20260724` e os 6 modelos `tessdata_fast` instalados e validados em `<release>/ocr`;
- 0 artefatos do 7-Zip remanescentes em `<temp_root>`;
- Portões `doctor` (`true`) e `fixture` (`true`) aprovados e `current.json` promovido atomicamente com `"revocation": "offline"`;
- Atalhos `Traslado.lnk` criados nos diretórios temporários de Área de Trabalho e Menu Iniciar;
- `%LOCALAPPDATA%\Traslado` (inexistente) e `%LOCALAPPDATA%\Freoa` (intacto, mtimes idênticos) preservados sem qualquer modificação;
- Diretório temporário em `%TEMP%` integralmente removido ao final.

---

## 5. Escopo para a Etapa 6 (E6) e Riscos

### Itens que pertencem à Etapa 6 (E6)
1. **Workflow `.github/workflows/release.yml` completo para 2.0**: publicação e attestation de todos os novos artefatos de `dist/` (`traslado-2.0.0.tar.gz`, `install.ps1`, `install.sh`, `requirements-{core,full,legacy}.hashes.lock`) junto ao `.whl`, `.zip` e `SHA256SUMS`.
2. **Workflow `.github/workflows/audit.yml`**: auditoria automatizada contínua de dependências e licenças.
3. **Fumaça real de instalação em runners Linux e macOS**: execução real de `install.sh` e `traslado setup` em ambientes Linux (`apt`/`dnf` + `uv`) e macOS (`brew` + `uv`) no GitHub Actions.

### Riscos residuais e mitigações
- **Primeira instalação online antes da primeira execução de `revocation.yml`**: como o protocolo exige `revoked.json` válido e não expirado na tag `revocation` quando a promoção é online (sem `-FromDir` / `--allow-offline-promotion`), o workflow `.github/workflows/revocation.yml` deve ser disparado uma vez (`workflow_dispatch`) antes ou no momento da publicação da release `v2.0.0` para inicializar a tag `revocation`.
- **Alertas de SmartScreen / Gatekeeper**: mitigado por documentação explícita no `README.md` e pelo controle primário documentado via `gh attestation verify`.

## Correções da revisão

Esta seção substitui as afirmações anteriores incompatíveis com o parecer independente de `9852d16` e com as decisões do integrador. A implementação anterior foi reprovada; seus relatos de bootstrap real não constituem evidência desta correção.

1. **Digests no segundo canal.** Os dois bootstraps gravam o mapa dos hashes efetivamente conferidos e o transmitem por `--expected-digests`. O setup exige wheel, lock do perfil e lock legacy quando selecionado. A API exige cada nome transmitido e seu digest, inclusive o hash do próprio SHA256SUMS externo; mapa vazio, incompleto, asset ausente ou divergência bloqueiam.
2. **Attestation antes das dependências.** Quando gh está disponível, wheel, lock do perfil, lock legacy usado e manifesto passam por `gh attestation verify --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml` antes de criar a venv ou instalar dependências. FromDir não dispensa a verificação. O workflow de release publica e atesta os locks, bootstraps e manifesto interno. Regressão injeta reprovação por artefato e confere ausência de chamada ao uv.
3. **Identidade do certificado.** `api-presence` exige certificado DER em verificationMaterial, interpretado pelo parser X.509 do OpenSSL via stdlib ssl, e URI SAN exatamente igual ao workflow/ref exigidos. Campos livres e payload DSSE não servem como identidade. Testes recusam bundle sem certificado, bytes inválidos e URI divergente; certificado sintético com SAN correto passa, inclusive release.yml com ref de tag. O modo registrado continua `api-presence`: presença e identidade declarada, sem alegar verificação criptográfica.
4. **Promoção offline explícita.** FromDir sozinho não habilita exceção. São necessários flag e `TRASLADO_PROMOCAO_OFFLINE=rascunho` ou `teste`. `current.json` registra revocation=offline e offline_reason. A promoção de baixo nível também exige autorização offline explícita e motivo correspondente. Testes cobrem falta de flag, contexto ausente/inválido e ambos os motivos permitidos.
5. **Desinstalação preserva dados.** Remoção seletiva preserva data_root no caminho original, inclusive se estiver profundamente dentro de releases, em ambas as modalidades. Também considera a candidata e preserva a instalação inteira se ela estiver dentro de data_root. O teste remove arquivos de fato em diretório temporário e confere os bytes do documento preservado.
6. **Atalhos após promoção e no rollback.** O setup registra a configuração dos atalhos na candidata, mas só os atualiza após current.json promovido. Rollback restaura o destino da release anterior e também atualiza os atalhos conhecidos pela release revertida, inclusive quando a anterior foi instalada sem atalhos. Teste confere o destino contra current.json em cada atualização e nenhuma chamada quando os gates falham.
7. **Emissor isolado instalado.** O workflow instala o pacote com `python -m pip install --no-deps .` antes de `python -I -m traslado.revocation`. O teste de fumaça instala o pacote localmente em venv temporária e executa o emissor com -I a partir de outro diretório, conferindo sequence=42.
8. **Sequência monotônica.** Somente HTTP 404 do asset inicial autoriza sequência anterior zero. HTTP diferente de 200/404, erro de transporte, JSON inválido ou sequência inválida abortam. Grupo fixo de concurrency com cancel-in-progress=false serializa as emissões. Teste confere o contrato do workflow; publicação remota não foi executada nesta sessão.
9. **Latest preservado.** Criação da release operacional usa --latest=false. O workflow captura latest de produto antes da emissão e exige o mesmo valor após publicar o asset. A verificação efetiva no GitHub depende da execução do workflow; nesta sessão foi conferido seu contrato local.
10. **Execução adiada dos bootstraps.** Todo o corpo POSIX está em main; a última linha é apenas `main "$@"`. O PowerShell declara o bloco completo antes da chamada final `& $InstallMain @args`, incluindo binding dos parâmetros dentro do bloco. Testes verificam a estrutura e executam o bootstrap PowerShell.
11. **ZIP autossuficiente.** O ZIP inclui PAYLOAD-SHA256SUMS para wheel, locks e arquivos consumidos pelos wrappers, sem hash do ZIP nem autorreferência. Os bootstraps aceitam esse manifesto quando não há SHA256SUMS externo. Ele também é emitido como asset atestável. Teste constrói o ZIP real, extrai somente ele e executa INSTALAR.bat com uv simulado; isso verifica o empacotamento e o wrapper, sem alegar instalação real dos locks.
12. **OCR portátil no runtime normal.** A descoberta prioriza ocr.tesseract do current.json ativo, conserva compatibilidade com o executável privado Windows do layout anterior e usa PATH, junto ao tessdata privado da release. Teste injeta raiz e executável externo e confere descoberta para Linux e macOS, inclusive fallback PATH. Não houve execução em hosts Linux/macOS nesta sessão.
13. **Fixture materializada.** Exige output e document.md, document.json, manifest.json, LEIA-AVISOS.txt e chunks/chunks.jsonl quando chunks é solicitado. Só aceita complete ou partial sem error_code. O aviso preventivo padrão docx_formatting_partial continua sendo aviso e deixa de produzir error_code; perdas efetivas e erros localizados mantêm os códigos. Testes recusam ausência de output, cada artefato faltante e partial com erro.
14. **Reinstalação preserva releases.** Os bootstraps alocam um novo `<versão>_<hex>` e recusam colisão, sem apagar diretórios existentes. Uma release ativa com versão, perfil, extras e idiomas iguais e inventário SHA-256 íntegro resulta em no-op antes de baixar/instalar. Setup recusa reconstruir IDs ativo/anterior. Não foi introduzida limpeza automática de releases: todas são retidas, incluindo as duas referenciadas. Testes cobrem no-op, corrupção, candidata reprovada e preservação da anterior.

### Validação desta correção

O teste real com uv e rede está preparado e condicionado a `TRASLADO_E2E_REAL=1`; **NOT_RUN nesta sessão**. Instruções e destino do log: `docs/evidencias-e5/README.md`. O teste usa perfil Full, locks reais, raiz e atalhos temporários, e não habilita OCR.

A suíte completa terminou com **451 testes: 437 aprovados, 14 pulados, zero falhas e zero erros**, em 1350.488 s, com **exit code 0**. Comando: `work\venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v`, com `PYTHONPATH=src`; o ambiente de validação em work foi copiado do venv-legacy autorizado. O log integral está em `docs/evidencias-e5/suite-unittest.txt`, incluindo a declaração do ambiente usado. As adaptações de sandbox ficaram restritas a work e não integram o produto.

A execução abrange build determinístico, wrapper do ZIP extraído, pacote instalado com emissor -I, gates, rollback, preservação real de dados, attestations com falhas injetadas e no-op da reinstalação. Os skips estão discriminados no log; incluem o bootstrap real explicitamente reservado ao integrador, contratos POSIX e integrações/caches indisponíveis neste ambiente.

Nenhum commit, publicação ou alteração do índice deste worktree foi realizado.
