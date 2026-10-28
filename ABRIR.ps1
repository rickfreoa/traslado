# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
param([string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'Freoa\MarkdownConverter'))
$ErrorActionPreference='Stop'
$taskRoot=[IO.Path]::GetFullPath($InstallRoot)
$taskRecord=Get-Content -LiteralPath (Join-Path $taskRoot 'current.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$taskOwner=Get-Content -LiteralPath (Join-Path $taskRoot '.freoa-installation.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($taskRecord.product -ne 'freoa-md-converter' -or $taskRecord.install_id -ne $taskOwner.install_id -or
    $taskRecord.release_id -notmatch '^[0-9A-Za-z.-]+_[0-9a-f]{32}$') { throw 'Registro de instalação inválido.' }
$taskRelease=Join-Path (Join-Path $taskRoot 'releases') $taskRecord.release_id
foreach($taskItem in @($taskRoot,$taskRelease,(Join-Path $taskRelease '.venv'))) {
    if ((Get-Item -LiteralPath $taskItem -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Instalação não pode ser link/junction.' }
}
$taskPythonW=Join-Path $taskRelease '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $taskPythonW)) { throw 'Instale o conversor primeiro com INSTALAR.bat.' }
Start-Process -FilePath $taskPythonW -ArgumentList @('-I','-m','freoa_v6','gui') -WorkingDirectory $taskRecord.data_root -WindowStyle Normal
