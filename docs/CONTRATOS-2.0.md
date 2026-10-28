# Contratos da série 2.x

Ricardo Peres Freoa | Stocche Forbes Advogados · fixados na etapa E0 do plano Traslado 2.0.0 (28/10/2026).

Estes contratos valem para toda a série 2.x. Mudança incompatível só na 3.0. Campo novo é adição compatível; quem lê deve ignorar campo desconhecido.

## 1. Identidade e compatibilidade

- Distribuição e módulo: `traslado`. Comandos: `traslado` (console) e `traslado-gui` (gui-script, sem console no Windows).
- Pacote-sombra `freoa_v6` no mesmo wheel durante toda a série 2.x: `python -I -m freoa_v6 <args>` executa exatamente o mesmo código de `python -I -m traslado <args>`; o aviso de depreciação vai só para stderr e nunca para stdout, `document.*`, `chunks.*` ou `manifest.json`. `import freoa_v6.<mod>` devolve o mesmo objeto de módulo que `traslado.<mod>`.
- Aliases `freoa-md` e `freoa-md-gui` mantidos.
- Os artefatos da conversão (`document.md`, `document.json`, `chunks/`, `manifest.json`, `LEIA-AVISOS.txt`) não mudam de nome, de esquema nem de bytes por causa da renomeação; só `converter_version`/`converter` refletem a versão. Prefixos temporários `.freoa-work-`, `.freoa-anterior-`, `.freoa-reserva-`, `.freoa-ocr-page-` são mantidos (a Biblioteca ignora diretórios iniciados por ponto).
- Textos literais de aviso já emitidos pela 1.1.0 são congelados: nenhum é renomeado ou reescrito na série 2.x.
- Exit codes do `convert`: 0 (tudo ok), 3 (algum parcial), 2 (alguma falha, cancelamento ou timeout, ou erro de uso), 130 (interrupção). Inalterados.

## 2. Raiz gerenciada

| SO | Raiz | Raiz antiga reconhecida |
|---|---|---|
| Windows | `%LOCALAPPDATA%\Traslado` | `%LOCALAPPDATA%\Freoa\MarkdownConverter` |
| Linux | `$XDG_DATA_HOME/traslado`, ou `~/.local/share/traslado` | — |
| macOS | `~/Library/Application Support/Traslado` | — |

Estrutura: `current.json`, `candidate.json` (opcional), `releases/<release_id>/` (imutável enquanto referenciado), `state/` (estado do cliente, por exemplo `revocation.json`), `logs/`.

### 2.1 `current.json` e `candidate.json` (schema_version 2)

```json
{
  "schema_version": 2,
  "product": "traslado",
  "install_id": "hex32",
  "release_id": "2.0.0_<hex32>",
  "version": "2.0.0",
  "profile": "Full | Core",
  "extras": ["legacy"],
  "python": "<caminho absoluto do interpretador da release>",
  "module": "traslado",
  "legacy_module": "freoa_v6",
  "platform": "win32 | linux | darwin",
  "arch": "x86_64 | arm64 | AMD64",
  "data_root": "<pasta de saídas padrão da GUI>",
  "languages": ["por", "eng"],
  "ocr_installed": true,
  "ocr": {"tesseract": "<caminho|null>", "origin": "lock | homebrew | system | none", "version": "5.x|null"},
  "source": {"tag": "v2.0.0", "repository": "https://github.com/rickfreoa/traslado"},
  "created_at": "AAAA-MM-DDThh:mm:ssZ",
  "promoted_at": "AAAA-MM-DDThh:mm:ssZ (só em current.json)",
  "previous_release_id": "<release_id anterior|null> (só em current.json)",
  "gates": {"doctor": true, "fixture": true, "revocation": "ok | offline | blocked"}
}
```

- A instalação grava só `candidate.json`. `current.json` nasce ou muda apenas pela promoção, depois de `gates.doctor` e `gates.fixture` verdadeiros, por escrita em temporário no mesmo diretório seguida de rename atômico. A promoção registra `promoted_at` e `previous_release_id` e remove `candidate.json`.
- Localizadores (a Biblioteca, `traslado where`) leem apenas `current.json`. Para localizar o interpretador, usam `python` se presente; senão, derivam `releases/<release_id>/.venv/Scripts/python.exe` (Windows) ou `.venv/bin/python` (POSIX), que é o contrato da 1.x.
- Quem converte resolve o interpretador uma vez e o usa até o fim da conversão.

### 2.2 `traslado where --json`

Saída em stdout, UTF-8, um objeto:

```json
{"schema": "traslado-where-1", "version": "<versão do código em execução>", "root": "<raiz>",
 "current": {<current.json>} , "candidate": {<candidate.json>} , "python": "<current.python ou null>",
 "module": "traslado", "legacy_roots": [{"root": "...", "current": {...}}], "platform": "...", "arch": "..."}
```

`current`/`candidate` valem `null` quando ausentes ou ilegíveis (com `errors` listando o motivo). Exit code 0 se houver `current`, 1 se não houver.

## 3. Erros tipados

Cada resultado de arquivo em stdout (`convert`) com `status` em `failed | partial | cancelled | timeout` traz `error_code`, além do campo `error` (texto livre, já existente). Valores estáveis:

| `error_code` | Quando |
|---|---|
| `unsupported` | formato não suportado pelo perfil instalado (assinatura e extensão) |
| `needs_ocr` | páginas sem texto e OCR indisponível; `error_pages` lista as folhas (1-based) |
| `malformed` | arquivo corrompido ou estrutura inválida para o formato detectado |
| `encrypted` | protegido por senha ou criptografado |
| `resource_limit` | teto de memória, processos, páginas, pixels, bytes de entrada, expansão ou saída |
| `missing_part` | parte obrigatória ausente (por exemplo, `word/document.xml`, motor OCR declarado e não encontrado) |
| `io` | erro de leitura ou escrita do sistema de arquivos |
| `timeout` | prazo do trabalho ou do OCR excedido |
| `cancelled` | cancelamento pelo usuário |
| `internal` | qualquer outra exceção (sempre um defeito a corrigir; a suíte exige que nenhuma fixture caia aqui) |

`partial` pode trazer `error_code` do primeiro problema localizado. Erro antes de qualquer arquivo (uso, configuração) sai em stderr como `{"status": "failed", "error": "...", "error_code": "..."}`. `manifest.json` e `document.json` não ganham campo por causa disto quando a conversão é `ok`.

## 4. Avisos novos (2.0)

| Aviso (estável) | Onde | Significado |
|---|---|---|
| `format_extension_mismatch` | `document.json` (`warnings`), `LEIA-AVISOS.txt`, chunks | A assinatura do conteúdo indica formato diferente da extensão; a conversão seguiu o conteúdo |
| `legacy_engine_experimental` | idem, só em resultado do motor anydoc | Resultado candidato de motor experimental (`submotor: anydoc`, `experimental: true`, `fidelity: unverified`) |
| `legacy_layout_unavailable` | idem | O motor experimental não fornece páginas nem caixas; offsets valem só sobre o Markdown gerado |

A Biblioteca trata aviso desconhecido como motivo para manter a obra em revisão.

## 5. Motor experimental (extra `legacy`)

- Formatos: `.doc`, `.xls`, `.ppt`, `.rtf`, `.odt`, `.ods`, `.odp`, `.epub` (e os que o anydoc reconhecer por assinatura).
- Só roda com o extra `legacy` instalado e com `--engine legacy` (CLI) ou `legacy = true` (TOML); nunca substitui saída existente; o resultado leva `submotor: anydoc`, `experimental: true`, `fidelity: unverified` em `document.json` e no `manifest.json`.

## 6. `revoked.json`

Asset da release mais recente com tag `revocation` (release dedicada `revocation`, não pré-release), reemitido semanalmente por workflow agendado e sempre com attestation do workflow `revocation.yml`.

```json
{"schema": "traslado-revoked-1", "sequence": 12, "issued_at": "2026-10-28T00:00:00Z",
 "expires_at": "2026-11-11T00:00:00Z", "revoked": [{"version": "2.0.1", "sha256": ["..."], "reason": "..."}]}
```

- O cliente persiste a maior `sequence` aceita em `state/revocation.json` e recusa documento com sequência menor, vencido (`expires_at` no passado) ou sem attestation da identidade esperada (`rickfreoa/traslado`, workflow `revocation.yml`).
- Segundo canal: a API de releases do GitHub deve mostrar a versão a promover como publicada, não rascunho, não pré-release, com `tag_name` e digests dos assets iguais aos esperados.
- Promoção só com os dois canais concordes. Falha de rede ou divergência bloqueia a promoção (`gates.revocation = blocked`), nunca a conversão nem a reversão local. Primeira instalação aceita só documento válido e não vencido.
- Limite declarado: a sequência impede regressão e a expiração limita a aceitação de informação antiga; nada prova que o documento recebido seja o mais recente.
- Verificação da attestation pelo cliente: com `gh` disponível, `gh attestation verify <arquivo> --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/revocation.yml` (verificação criptográfica). Sem `gh`, o cliente consulta `GET /repos/rickfreoa/traslado/attestations/sha256:<digest>` e exige ao menos um bundle cujo certificado declare o workflow esperado e a ref `refs/heads/main`; isso é conferência de presença e identidade declarada, não verificação criptográfica, e o cliente registra o modo usado (`gh` ou `api-presence`) em `state/revocation.json`.
