# SPDX-License-Identifier: AGPL-3.0-or-later
$ErrorActionPreference = 'Stop'
if (Get-Command python,python3,uv,tesseract -ErrorAction SilentlyContinue) { throw 'Imagem Server Core não está limpa' }
$env:TRASLADO_PROMOCAO_OFFLINE = 'teste'  # artefatos locais de CI; revogacao real so no E2E publicado (E11)
& C:\dist\install.ps1 -FromDir C:\dist -Root C:\teste\Traslado -Yes -NoShortcut -SkipOCR -AllowOfflinePromotion
if ($LASTEXITCODE -ne 0) { throw "install.ps1 falhou: $LASTEXITCODE" }
$current = Get-Content C:\teste\Traslado\current.json -Raw | ConvertFrom-Json
$python = "C:\teste\Traslado\releases\$($current.release_id)\.venv\Scripts\python.exe"
& $python -I -m traslado where --json --root C:\teste\Traslado
if ($LASTEXITCODE -ne 0) { throw 'where falhou no Server Core' }
foreach ($format in @('docx','pdf')) {
    $result = (& $python -I -m traslado convert "C:\fixtures\fixture.$format" "C:\teste\out-$format" --chunks) | ConvertFrom-Json
    if ($LASTEXITCODE -notin @(0,3)) { throw "Conversão $format falhou no Server Core" }
    if ($result.error_code) { throw "Erro de conversão: $($result.error_code)" }
    $text = Get-Content (Join-Path $result.output 'document.md') -Raw
    if (-not $text.Contains("Traslado verificacao de instalacao $($format.ToUpper()) OK.")) { throw 'Sentinela ausente' }
    $result | ConvertTo-Json -Depth 8
}
