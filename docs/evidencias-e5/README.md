# Evidências da correção E5

Os testes sem rede registram bootstrap com uv simulado; isso não prova instalação real dos locks.
O teste real está em `tests.test_e5_corrections.RealBootstrapE5` e fica desabilitado por padrão.

Para executar no Windows, fora do sandbox, a partir deste worktree:

```powershell
$env:PYTHONPATH = 'src'
$env:TRASLADO_E2E_REAL = '1'
& C:\Traslado\venv-legacy\Scripts\python.exe -m unittest -v tests.test_e5_corrections.RealBootstrapE5
```

O teste cria artefatos com `scripts/build_release.py --permitir-sujo`, baixa o uv real fixado pelo bootstrap,
instala o perfil Full com `--require-hashes --only-binary :all:`, instala o wheel real e executa doctor e fixture.
Usa raiz em TEMP e diretórios temporários de atalhos. Confere imports das dependências e reinstalação idempotente.
OCR fica desabilitado nesse teste para isolar o bootstrap e os locks de Python.

A promoção local usa flag explícita e `TRASLADO_PROMOCAO_OFFLINE=teste`. O PATH desse teste exclui gh:
artefatos locais sujos, ainda não publicados, não têm attestations de release. A exigência de gh quando disponível
é exercitada separadamente pelos testes sem rede, com reprovação injetada por artefato antes de chamar uv.

O log real será gravado em `bootstrap-real-windows.txt`, inclusive se houver falha.
**Estado nesta entrega: NOT_RUN** (rede e execução de uv fora do sandbox reservadas ao integrador).

Validação local da suíte completa: 451 testes, 437 aprovados, 14 pulados, zero falhas/erros e exit code 0. Log integral: `suite-unittest.txt`. Esse log não substitui o teste real de bootstrap acima.
