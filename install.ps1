# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
# Fonte correspondente: https://github.com/rickfreoa/traslado/tree/v2.0.0
# Códigos de saída: 0 = instalado/ativo; 1 = erro de download, hash ou attestation; 2 = falha nos gates ou parâmetros.
 $InstallMain = {
[CmdletBinding()]
param(
    [string]$Version = "2.0.0",
    [ValidateSet("Full", "Core", "full", "core")][string]$Profile = "Full",
    [string]$Extras = "",
    [string]$Languages = "por,eng,spa,ita,fra,deu",
    [switch]$NoShortcut,
    [switch]$KeepFailed,
    [string]$DataRoot = "",
    [string]$Root = "",
    [string]$FromDir = "",
    [switch]$Yes,
    [switch]$AllowOfflinePromotion,
    [switch]$NoPromote,
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
            if ($map.ContainsKey($name)) { throw "Nome duplicado em SHA256SUMS: $name" }
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
    if ($Version -match "[\r\n]" -or $Version -cnotmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$') { throw "Version invalida" }
    $CleanVersion = $Version
    $NormProfile = if ($Profile.ToLowerInvariant() -eq "core") { "Core" } else { "Full" }
    $ProfileLower = $NormProfile.ToLowerInvariant()
    $ExtrasList = @($Extras -split '[,+]' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ } | Sort-Object -Unique)
    $LangsList = @($Languages -split '[,+]' | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })

    if (-not $Root) {
        $localAppData = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }
        $Root = Join-Path $localAppData "Traslado"
    }
    $RootFull = [IO.Path]::GetFullPath($Root)
    $env:UV_CACHE_DIR = Join-Path $RootFull "state\uv-cache"
    New-Item -ItemType Directory -Path $RootFull -Force | Out-Null

    $PayloadKey = "$CleanVersion|$NormProfile|$($ExtrasList -join ',')|$($LangsList -join ',')|"
    $ReleaseId = "${CleanVersion}_" + [Guid]::NewGuid().ToString("N")

    $CurrentPath = Join-Path $RootFull "current.json"
    if (Test-Path -LiteralPath $CurrentPath -PathType Leaf) {
        try {
            $currentObj = Get-Content -LiteralPath $CurrentPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $activeDir = Join-Path (Join-Path $RootFull "releases") $currentObj.release_id
            $activePy = Join-Path $activeDir ".venv\Scripts\python.exe"
            if ($currentObj.product -eq "traslado" -and
                $currentObj.version -eq $CleanVersion -and
                $currentObj.profile -eq $NormProfile -and
                (Test-Path -LiteralPath $activePy -PathType Leaf)) {
                $ActiveArgs = @("-I", "-m", "traslado", "setup", "--check-active", "--expected-version", $CleanVersion,
                                "--root", $RootFull, "--profile", $NormProfile, "--languages", $Languages)
                if ($Extras) { $ActiveArgs += @("--extras", $Extras) }
                & $activePy @ActiveArgs
                if ($LASTEXITCODE -ne 0) { throw "Release ativa sem integridade" }
                Write-Host "Traslado $CleanVersion ($NormProfile) já está ativo em $RootFull ($ReleaseId)."
                Write-Host "Licença: AGPL-3.0-or-later | Fonte: https://github.com/rickfreoa/traslado/tree/v$CleanVersion"
                exit 0
            }
        } catch {}
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
        $SumsPath = Find-LocalArtifact $EffectiveFromDir @("SHA256SUMS", "..\SHA256SUMS", "PAYLOAD-SHA256SUMS")
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

    $VerifiedDigests = @{}
    $VerifiedDigests[$WheelName] = Get-FileSha256Hex $WheelPath
    $VerifiedDigests[$ProfileLockName] = Get-FileSha256Hex $ProfileLockPath
    if ($LegacyLockPath) { $VerifiedDigests[$LegacyLockName] = Get-FileSha256Hex $LegacyLockPath }
    # SHA256SUMS itself is authenticated by attestation/API digest, without self-reference.
    if ((Split-Path -Leaf $SumsPath) -eq "SHA256SUMS") { $VerifiedDigests["SHA256SUMS"] = Get-FileSha256Hex $SumsPath }
    $DigestsPath = Join-Path $StageDownloads ("verified-" + $ReleaseId + ".json")
    $VerifiedDigests | ConvertTo-Json | Set-Content -LiteralPath $DigestsPath -Encoding UTF8

    if (-not $FromDir) {
        $api = Invoke-RestMethod -Uri "https://api.github.com/repos/rickfreoa/traslado/releases/tags/v$CleanVersion" -Headers @{Accept="application/vnd.github+json"; "User-Agent"="Traslado-Bootstrap"}
        if ($api.draft -isnot [bool] -or $api.prerelease -isnot [bool] -or $api.draft -ne $false -or $api.prerelease -ne $false -or $api.tag_name -cne "v$CleanVersion") { throw "Release API nao publicada ou tag divergente" }
        $selfAsset = @($api.assets | Where-Object { $_.name -ceq "install.ps1" })
        # Via "irm ... | iex" não há arquivo do próprio script ($PSCommandPath vazio): o digest dele não é
        # conferível, e a autenticidade vem do TLS do GitHub; os artefatos instalados seguem conferidos na API.
        $SelfFile = if ($PSCommandPath -and (Test-Path -LiteralPath $PSCommandPath -PathType Leaf)) { $PSCommandPath } else { $null }
        if ($selfAsset.Count -gt 0) {
            if ($SelfFile) { $VerifiedDigests["install.ps1"] = Get-FileSha256Hex $SelfFile }
            else { Write-Host "Aviso: instalador executado sem arquivo (iex); digest do proprio install.ps1 nao conferivel." }
        }
        foreach ($name in $VerifiedDigests.Keys) {
            $assets = @($api.assets | Where-Object { $_.name -ceq $name })
            if ($assets.Count -ne 1 -or $assets[0].digest -cne ("sha256:" + $VerifiedDigests[$name])) { throw "Digest API divergente ou asset ausente: $name" }
        }
        $VerifiedDigests | ConvertTo-Json | Set-Content -LiteralPath $DigestsPath -Encoding UTF8
    }

    # 3. Verificação de attestation (gh)
    $GhCmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($GhCmd) {
        Write-Host "Verificando attestation criptográfica com gh..."
        $SelfArtifact = if (-not $FromDir -and $selfAsset.Count -gt 0 -and $SelfFile) { $SelfFile } else { $null }
        foreach ($artifact in @($WheelPath, $ProfileLockPath, $LegacyLockPath, $SumsPath, $SelfArtifact)) {
            if (-not $artifact) { continue }
            & $GhCmd.Source attestation verify $artifact --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml --source-ref "refs/tags/v$CleanVersion"
            if ($LASTEXITCODE -ne 0) { throw "Attestation reprovada para $artifact" }
        }
    } else {
        Write-Host "Aviso: verificação automática via GitHub CLI (gh) não executada neste modo."
        Write-Host "Modo remoto: digests conferidos na API; modo local: promocao exige excecao offline explicita."
        Write-Host "  gh attestation verify `"$WheelPath`" --repo rickfreoa/traslado --signer-workflow rickfreoa/traslado/.github/workflows/release.yml --source-ref refs/tags/v$CleanVersion"
    }

    # 1. Obter uv fixado por versão e SHA-256
    $UvExe = $null
    if ($env:TRASLADO_PROMOCAO_OFFLINE -eq "teste" -and $env:TRASLADO_TEST_UV -and (Test-Path -LiteralPath $env:TRASLADO_TEST_UV -PathType Leaf)) {
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

    # 4. Criar ambiente virtual Python 3.13 gerenciado na release candidata
    $ReleasesDir = Join-Path $RootFull "releases"
    New-Item -ItemType Directory -Path $ReleasesDir -Force | Out-Null
    $ReleaseDir = Join-Path $ReleasesDir $ReleaseId
    if (Test-Path -LiteralPath $ReleaseDir) { throw "Release ja existe: $ReleaseDir" }
    New-Item -ItemType Directory -Path $ReleaseDir | Out-Null

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
        "--expected-digests", $DigestsPath
    )
    # -NoPromote: instala e qualifica a candidata (candidate.json) sem tocar current.json; promoção depois,
    # pelo Python da candidata: traslado setup --promote --release-id <id> (implantação lado a lado).
    if (-not $NoPromote) { $SetupArgs += "--promote" }
    if ($ExtrasList.Count -gt 0) {
        $SetupArgs += @("--extras", ($ExtrasList -join ","))
    }
    if ($DataRoot) { $SetupArgs += @("--data-root", $DataRoot) }
    if ($KeepFailed) { $SetupArgs += "--keep-failed" }
    if ($NoShortcut) {
        $SetupArgs += "--no-shortcut"
    }
    if ($EffectiveFromDir) {
        $SetupArgs += @("--from-dir", $EffectiveFromDir)
    }
    if ($AllowOfflinePromotion) {
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
    if ($LASTEXITCODE -eq 4) {
        Write-Host "Instalacao promovida; somente os atalhos falharam. Recrie-os com: & '$VenvPython' -I -m traslado shortcuts --root '$RootFull'"
        exit 4
    }
    if ($LASTEXITCODE -ne 0) {
        if (-not $KeepFailed) {
            # The candidate interpreter has exited. Clean from its base interpreter,
            # outside the release, so Windows can remove the candidate executable.
            try {
                $CleanupRuntime = (& $VenvPython -I -c "import json,sys,pathlib,traslado; print(json.dumps([sys._base_executable,str(pathlib.Path(traslado.__file__).parent.parent)]))") | ConvertFrom-Json
                & $CleanupRuntime[0] -I -c "import sys,pathlib; sys.path.insert(0,sys.argv[1]); from traslado.lifecycle import remove_unreferenced_release; remove_unreferenced_release(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]))" $CleanupRuntime[1] $RootFull $ReleaseDir
                if ($LASTEXITCODE -ne 0) { Write-Host "Limpeza pendente; marcador preservado para a proxima execucao." }
            } catch { Write-Host "Limpeza pendente; marcador preservado para a proxima execucao." }
        }
        Write-Error "Setup falhou; consulte o diagnostico acima para o estado da instalacao."
        exit 2
    }

    if ($NoPromote) {
        Write-Host "Candidata $ReleaseId do Traslado $CleanVersion instalada e qualificada em $RootFull, sem promoção."
    } else {
        Write-Host "Instalação do Traslado $CleanVersion concluída e ativa em $RootFull."
    }
    exit 0
} catch {
    Write-Host ("Erro na instalação: " + $_.Exception.Message) -ForegroundColor Red
    if ($_.Exception.Message -match "SHA-256|Attestation|URL|SHA256SUMS|ausente em FromDir") {
        exit 1
    }
    exit 2
}

}
& $InstallMain @args
