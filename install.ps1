# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
# Fonte correspondente: https://github.com/rickfreoa/traslado/tree/v2.0.0
# Códigos de saída: 0 = instalado/ativo; 1 = erro de download, hash ou attestation; 2 = falha nos gates ou parâmetros.
[CmdletBinding()]
param(
    [string]$Version = "2.0.0",
    [ValidateSet("Full", "Core", "full", "core")][string]$Profile = "Full",
    [string]$Extras = "",
    [string]$Languages = "por,eng,spa,ita,fra,deu",
    [switch]$NoShortcut,
    [string]$Root = "",
    [string]$FromDir = "",
    [switch]$Yes,
    [switch]$AllowOfflinePromotion,
    [switch]$SkipOCR,
    [string]$OCRCache = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding

$UvVersion = "0.13.6"
$UvHashes = @{
    "x86_64-pc-windows-msvc"      = "a2a8820d5eea32f08ef467ef3b7f66883a9b076d5737a2fc2fbb5f458d1bb50f"
    "x86_64-unknown-linux-gnu"    = "f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645"
    "aarch64-unknown-linux-gnu"   = "229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d"
    "aarch64-apple-darwin"        = "60fbd5c8a1ed681845eea28d9d622fcde03c51e91a43465b64a1e17db1ed3472"
    "x86_64-apple-darwin"         = "63ba17fdda55ad1a230d4b37fc6017ffa10c23688c83818bd3bfdf914c652a02"
}

function Get-FileSha256Hex([string]$Path) {
    $alg = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::OpenRead($Path)
        try {
            return ([BitConverter]::ToString($alg.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
        } finally {
            $stream.Dispose()
        }
    } finally {
        $alg.Dispose()
    }
}

function Get-StringSha256Hex([string]$Text) {
    $alg = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($alg.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $alg.Dispose()
    }
}

function Save-HttpsFile([string]$Url, [string]$Destination) {
    if (-not $Url.StartsWith("https://")) {
        throw "URL insegura recusada: $Url"
    }
    $parent = Split-Path -Parent $Destination
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $Destination -UseBasicParsing
}

function Read-SumsMap([string]$SumsFile) {
    $map = @{}
    foreach ($line in (Get-Content -LiteralPath $SumsFile -Encoding UTF8)) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        $parts = $trimmed -split '\s+'
        if ($parts.Count -ge 2 -and $parts[0] -match '^[0-9a-fA-F]{64}$') {
            $name = $parts[$parts.Count - 1].TrimStart('*')
            $map[$name] = $parts[0].ToLowerInvariant()
        }
    }
    return $map
}

function Find-LocalArtifact([string]$BaseDir, [string[]]$RelativeCandidates) {
    foreach ($rel in $RelativeCandidates) {
        $candidate = Join-Path $BaseDir $rel
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

try {
    $CleanVersion = $Version.TrimStart('v')
    $NormProfile = if ($Profile.ToLowerInvariant() -eq "core") { "Core" } else { "Full" }
    $ProfileLower = $NormProfile.ToLowerInvariant()
    $ExtrasList = @($Extras -split '[,+]' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ } | Sort-Object -Unique)
    $LangsList = @($Languages -split '[,+]' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })

    if (-not $Root) {
        $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
        $Root = Join-Path $localAppData "Traslado"
    }
    $RootFull = [IO.Path]::GetFullPath($Root)
    New-Item -ItemType Directory -Path $RootFull -Force | Out-Null

    $PayloadKey = "$CleanVersion|$NormProfile|$($ExtrasList -join ',')|$($LangsList -join ',')|"
    $ReleaseId = "${CleanVersion}_" + (Get-StringSha256Hex $PayloadKey).Substring(0, 32)

    $CurrentPath = Join-Path $RootFull "current.json"
    if (Test-Path -LiteralPath $CurrentPath -PathType Leaf) {
        try {
            $currentObj = Get-Content -LiteralPath $CurrentPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $activeDir = Join-Path (Join-Path $RootFull "releases") $currentObj.release_id
            $activePy = Join-Path $activeDir ".venv\Scripts\python.exe"
            if ($currentObj.product -eq "traslado" -and
                $currentObj.release_id -eq $ReleaseId -and
                $currentObj.version -eq $CleanVersion -and
                $currentObj.profile -eq $NormProfile -and
                (Test-Path -LiteralPath $activePy -PathType Leaf)) {
                Write-Host "Traslado $CleanVersion ($NormProfile) já está ativo em $RootFull ($ReleaseId)."
                Write-Host "Licença: AGPL-3.0-or-later | Fonte: https://github.com/rickfreoa/traslado/tree/v$CleanVersion"
                exit 0
            }
        } catch {}
    }

    # 1. Obter uv fixado por versão e SHA-256
    $UvExe = $null
    if ($env:TRASLADO_TEST_UV -and (Test-Path -LiteralPath $env:TRASLADO_TEST_UV -PathType Leaf)) {
        $UvExe = (Resolve-Path -LiteralPath $env:TRASLADO_TEST_UV).Path
    } else {
        $UvDir = Join-Path $RootFull "tools\uv-$UvVersion"
        $CandidateUv = Join-Path $UvDir "uv.exe"
        if (Test-Path -LiteralPath $CandidateUv -PathType Leaf) {
            $UvExe = $CandidateUv
        } else {
            $PlatformKey = "x86_64-pc-windows-msvc"
            $ExpectedUvHash = $UvHashes[$PlatformKey]
            $UvArchiveName = "uv-$PlatformKey.zip"
            $UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$UvArchiveName"
            New-Item -ItemType Directory -Path $UvDir -Force | Out-Null
            $UvArchivePath = Join-Path $UvDir $UvArchiveName
            Write-Host "Baixando uv $UvVersion ($PlatformKey)..."
            Save-HttpsFile $UvUrl $UvArchivePath
            $ActualUvHash = Get-FileSha256Hex $UvArchivePath
            if ($ActualUvHash -ne $ExpectedUvHash) {
                Remove-Item -LiteralPath $UvArchivePath -Force -ErrorAction SilentlyContinue
                throw "SHA-256 do uv divergente: esperado $ExpectedUvHash, obtido $ActualUvHash"
            }
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            [IO.Compression.ZipFile]::ExtractToDirectory($UvArchivePath, $UvDir)
            Remove-Item -LiteralPath $UvArchivePath -Force -ErrorAction SilentlyContinue
            if (-not (Test-Path -LiteralPath $CandidateUv -PathType Leaf)) {
                $foundUv = Get-ChildItem -LiteralPath $UvDir -Filter "uv.exe" -Recurse | Select-Object -First 1
                if (-not $foundUv) { throw "uv.exe não encontrado após extração" }
                $UvExe = $foundUv.FullName
            } else {
                $UvExe = $CandidateUv
            }
        }
    }

    # 2. Obter e autenticar artefatos da release (SHA256SUMS, wheel e locks)
    $WheelName = "traslado-$CleanVersion-py3-none-any.whl"
    $ProfileLockName = "requirements-$ProfileLower.hashes.lock"
    $LegacyLockName = "requirements-legacy.hashes.lock"
    $StageDownloads = Join-Path $RootFull "state\downloads\v$CleanVersion"
    New-Item -ItemType Directory -Path $StageDownloads -Force | Out-Null

    $ReleaseBaseUrl = "https://github.com/rickfreoa/traslado/releases/download/v$CleanVersion"
    $SumsPath = $null
    $WheelPath = $null
    $ProfileLockPath = $null
    $LegacyLockPath = $null
    $EffectiveFromDir = $null

    if ($FromDir) {
        $EffectiveFromDir = (Resolve-Path -LiteralPath $FromDir).Path
        $SumsPath = Find-LocalArtifact $EffectiveFromDir @("SHA256SUMS", "..\SHA256SUMS")
        $WheelPath = Find-LocalArtifact $EffectiveFromDir @($WheelName, "dist\$WheelName")
        $ProfileLockPath = Find-LocalArtifact $EffectiveFromDir @($ProfileLockName, "locks\$ProfileLockName")
        if ("legacy" -in $ExtrasList) {
            $LegacyLockPath = Find-LocalArtifact $EffectiveFromDir @($LegacyLockName, "locks\$LegacyLockName")
        }
        if (-not $WheelPath -or -not $ProfileLockPath) {
            throw "Artefatos obrigatórios ($WheelName, $ProfileLockName) ausentes em FromDir: $EffectiveFromDir"
        }
        if ("legacy" -in $ExtrasList -and -not $LegacyLockPath) {
            throw "Lock do extra legacy ($LegacyLockName) ausente em FromDir: $EffectiveFromDir"
        }
        if ($SumsPath) {
            $SumsMap = Read-SumsMap $SumsPath
            foreach ($pair in @(@{Name=$WheelName; Path=$WheelPath}, @{Name=$ProfileLockName; Path=$ProfileLockPath})) {
                if ($SumsMap.ContainsKey($pair.Name)) {
                    $actual = Get-FileSha256Hex $pair.Path
                    if ($actual -ne $SumsMap[$pair.Name]) {
                        throw "SHA-256 divergente para $($pair.Name): esperado $($SumsMap[$pair.Name]), obtido $actual"
                    }
                } else {
                    throw "Entrada $($pair.Name) ausente em SHA256SUMS"
                }
            }
            if ($LegacyLockPath) {
                if (-not $SumsMap.ContainsKey($LegacyLockName) -or (Get-FileSha256Hex $LegacyLockPath) -ne $SumsMap[$LegacyLockName]) {
                    throw "SHA-256 divergente ou ausente em SHA256SUMS para $LegacyLockName"
                }
            }
        } elseif (Test-Path -LiteralPath (Join-Path $EffectiveFromDir "INSTALL-PAYLOAD.json") -PathType Leaf) {
            $payload = Get-Content -LiteralPath (Join-Path $EffectiveFromDir "INSTALL-PAYLOAD.json") -Raw -Encoding UTF8 | ConvertFrom-Json
            $payloadMap = @{}
            foreach ($f in $payload.files) { $payloadMap[$f.path] = $f.sha256.ToLowerInvariant() }
            $wheelRel = "dist/$WheelName"
            if (-not $payloadMap.ContainsKey($wheelRel) -or (Get-FileSha256Hex $WheelPath) -ne $payloadMap[$wheelRel]) {
                throw "SHA-256 do wheel diverge de INSTALL-PAYLOAD.json"
            }
        } else {
            throw "SHA256SUMS ausente em FromDir: $EffectiveFromDir"
        }
    } else {
        $SumsPath = Join-Path $StageDownloads "SHA256SUMS"
        $WheelPath = Join-Path $StageDownloads $WheelName
        $ProfileLockPath = Join-Path $StageDownloads $ProfileLockName
        Write-Host "Baixando SHA256SUMS, $WheelName e $ProfileLockName de $ReleaseBaseUrl..."
        Save-HttpsFile "$ReleaseBaseUrl/SHA256SUMS" $SumsPath
        Save-HttpsFile "$ReleaseBaseUrl/$WheelName" $WheelPath
        Save-HttpsFile "$ReleaseBaseUrl/$ProfileLockName" $ProfileLockPath
        $SumsMap = Read-SumsMap $SumsPath
        foreach ($pair in @(@{Name=$WheelName; Path=$WheelPath}, @{Name=$ProfileLockName; Path=$ProfileLockPath})) {
            if (-not $SumsMap.ContainsKey($pair.Name)) {
                throw "Arquivo $($pair.Name) ausente em SHA256SUMS"
            }
            $actual = Get-FileSha256Hex $pair.Path
            if ($actual -ne $SumsMap[$pair.Name]) {
                throw "SHA-256 divergente para $($pair.Name): esperado $($SumsMap[$pair.Name]), obtido $actual"
            }
        }
        if ("legacy" -in $ExtrasList) {
            $LegacyLockPath = Join-Path $StageDownloads $LegacyLockName
            Save-HttpsFile "$ReleaseBaseUrl/$LegacyLockName" $LegacyLockPath
            if (-not $SumsMap.ContainsKey($LegacyLockName) -or (Get-FileSha256Hex $LegacyLockPath) -ne $SumsMap[$LegacyLockName]) {
                throw "SHA-256 divergente para $LegacyLockName"
            }
        }
    }

    # 3. Verificação de attestation (gh)
    $GhCmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($GhCmd -and (-not $FromDir -or $env:TRASLADO_VERIFY_ATTESTATION -eq "1")) {
        Write-Host "Verificando attestation criptográfica com gh..."
        & $GhCmd.Source attestation verify $WheelPath --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml
        if ($LASTEXITCODE -ne 0) {
            throw "Attestation reprovada para $WheelPath"
        }
    } else {
        Write-Host "Aviso: verificação automática via GitHub CLI (gh) não executada neste modo."
        Write-Host "O controle principal de autenticidade é manual:"
        Write-Host "  gh attestation verify `"$WheelPath`" --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml"
    }

    # 4. Criar ambiente virtual Python 3.13 gerenciado na release candidata
    $ReleasesDir = Join-Path $RootFull "releases"
    New-Item -ItemType Directory -Path $ReleasesDir -Force | Out-Null
    $ReleaseDir = Join-Path $ReleasesDir $ReleaseId
    if (Test-Path -LiteralPath $ReleaseDir) {
        Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null

    if ($EffectiveFromDir) {
        $localOcrLock = Find-LocalArtifact $EffectiveFromDir @("ocr-assets.lock.json", "..\ocr-assets.lock.json")
        if ($localOcrLock) {
            Copy-Item -LiteralPath $localOcrLock -Destination (Join-Path $ReleaseDir "ocr-assets.lock.json") -Force
        }
        $localBootstrap = Find-LocalArtifact $EffectiveFromDir @("scripts\bootstrap_ocr.ps1", "bootstrap_ocr.ps1", "..\scripts\bootstrap_ocr.ps1")
        if ($localBootstrap) {
            $scriptsDest = Join-Path $ReleaseDir "scripts"
            New-Item -ItemType Directory -Path $scriptsDest -Force | Out-Null
            Copy-Item -LiteralPath $localBootstrap -Destination (Join-Path $scriptsDest "bootstrap_ocr.ps1") -Force
        }
    }

    $PythonInstallDir = Join-Path $RootFull "python"
    New-Item -ItemType Directory -Path $PythonInstallDir -Force | Out-Null
    $env:UV_PYTHON_INSTALL_DIR = $PythonInstallDir

    $VenvDir = Join-Path $ReleaseDir ".venv"
    Write-Host "Criando ambiente Python 3.13 gerenciado em $VenvDir..."
    & $UvExe venv $VenvDir --python 3.13 --python-preference only-managed
    if ($LASTEXITCODE -ne 0) { throw "Falha ao criar .venv com uv" }

    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw "Interpretador não encontrado em $VenvPython"
    }

    if ($NormProfile -eq "Full") {
        Write-Host "Instalando dependências fixadas ($ProfileLockName)..."
        & $UvExe pip install --python $VenvPython --require-hashes --only-binary :all: -r $ProfileLockPath
        if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar dependências do perfil $NormProfile" }
    }
    if ($LegacyLockPath) {
        Write-Host "Instalando extra experimental legacy ($LegacyLockName)..."
        & $UvExe pip install --python $VenvPython --require-hashes -r $LegacyLockPath
        if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar extra legacy" }
    }

    Write-Host "Instalando wheel $WheelName..."
    & $UvExe pip install --python $VenvPython --no-deps $WheelPath
    if ($LASTEXITCODE -ne 0) { throw "Falha ao instalar wheel do Traslado" }

    # 5. Executar traslado setup e promover somente se os gates aprovarem
    $SetupArgs = @(
        "-I", "-m", "traslado", "setup",
        "--release-id", $ReleaseId,
        "--profile", $NormProfile,
        "--languages", ($LangsList -join ","),
        "--root", $RootFull,
        "--promote"
    )
    if ($ExtrasList.Count -gt 0) {
        $SetupArgs += @("--extras", ($ExtrasList -join ","))
    }
    if ($NoShortcut) {
        $SetupArgs += "--no-shortcut"
    }
    if ($EffectiveFromDir) {
        $SetupArgs += @("--from-dir", $EffectiveFromDir)
    }
    if ($AllowOfflinePromotion -or ($FromDir -and $env:TRASLADO_FORCE_ONLINE_REVOCATION -ne "1")) {
        $SetupArgs += "--allow-offline-promotion"
    }
    if ($SkipOCR) {
        $SetupArgs += "--skip-ocr"
    }
    if ($OCRCache) {
        $SetupArgs += @("--ocr-cache", $OCRCache)
    }

    Write-Host "Executando verificação de gates e configuração (traslado setup)..."
    & $VenvPython @SetupArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Os gates de verificação reprovaram a instalação candidata; current.json não foi alterado."
        exit 2
    }

    Write-Host "Instalação do Traslado $CleanVersion concluída e ativa em $RootFull."
    exit 0
} catch {
    Write-Host ("Erro na instalação: " + $_.Exception.Message) -ForegroundColor Red
    if ($_.Exception.Message -match "SHA-256|Attestation|URL|SHA256SUMS|ausente em FromDir") {
        exit 1
    }
    exit 2
}
