[CmdletBinding()]
param(
    [string]$Destination,
    [string[]]$Languages = @('por', 'eng', 'spa', 'ita', 'fra', 'deu'),
    [string]$CacheDirectory,
    [string]$LockPath,
    [switch]$Offline
)

# Extract authenticated official archives; never execute their installers.
# The caller supplies a new directory inside its unpublished release stage.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $LockPath) { $LockPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'ocr-assets.lock.json' }

function Get-OcrReparseTag([string]$Path) {
    if (-not ('FreoaOcrReparseNative' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class FreoaOcrReparseNative {
    [StructLayout(LayoutKind.Sequential)]
    private struct AttributeTagInfo { public uint Attributes; public uint Tag; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, uint share,
        IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool GetFileInformationByHandleEx(SafeFileHandle handle,
        int informationClass, out AttributeTagInfo info, uint size);
    public static uint GetTag(string path) {
        // Query the placeholder itself; never follow a name-surrogate link.
        using (SafeFileHandle handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3,
                0x02200000, IntPtr.Zero)) {
            if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            AttributeTagInfo info;
            if (!GetFileInformationByHandleEx(handle, 9, out info, 8))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            return info.Tag;
        }
    }
}
'@
    }
    return [FreoaOcrReparseNative]::GetTag($Path)
}

function Test-OcrCloudTag([uint32]$Tag) {
    # Microsoft CLOUD and CLOUD_1..F differ only in bits 12..15.
    # All other tags, including symlink/junction/name-surrogate tags, fail closed.
    return (($Tag -band [uint32]4294905855) -eq [uint32]2415919130)
}

function Assert-OcrNoLinks([string]$Path) {
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                -not (Test-OcrCloudTag (Get-OcrReparseTag $cursor))) {
                throw "OCR: links/junctions nao permitidos: $cursor"
            }
        } else {
            # Get-Item also finds dangling links which Test-Path may omit.
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
            if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -and
                -not (Test-OcrCloudTag (Get-OcrReparseTag $cursor))) {
                throw "OCR: link pendente nao permitido: $cursor"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Assert-OcrMemberName([string]$Name) {
    if ($Name -notmatch '^[A-Za-z0-9_.+-]+(?:/[A-Za-z0-9_.+-]+)*$' -or
        @($Name.Split('/') | Where-Object { $_ -eq '.' -or $_ -eq '..' -or $_.StartsWith('-') }).Count) {
        throw "OCR: membro invalido no lock: $Name"
    }
}

function Get-OcrLockedFile([string]$Path, $Specification) {
    Assert-OcrNoLinks $Path
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($stream.Length -ne [long]$Specification.bytes) { throw "OCR: tamanho divergente: $Path" }
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try { $actual = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
        finally { $algorithm.Dispose() }
        if ($actual -ne $Specification.sha256) { throw "OCR: SHA256 divergente: $Path" }
        $stream.Position = 0
        return [pscustomobject]@{ Path = $Path; Handle = $stream }
    } catch { $stream.Dispose(); throw }
}

function Save-OcrPinnedAsset($Asset, [string]$Cache, [bool]$OnlyOffline) {
    Assert-OcrMemberName $Asset.name
    if ($Asset.name.Contains('/')) { throw 'OCR: nome de asset deve ser simples' }
    if ($Asset.sha256 -notmatch '^[a-f0-9]{64}$' -or [long]$Asset.bytes -lt 1 -or [long]$Asset.bytes -gt 100000000) {
        throw 'OCR: hash/tamanho invalido no lock'
    }
    $target = Join-Path $Cache $Asset.name
    if (Test-Path -LiteralPath $target) { return Get-OcrLockedFile $target $Asset }
    if ($OnlyOffline) { throw "OCR: cache offline incompleto: $($Asset.name)" }
    $allowed = @('github.com', 'raw.githubusercontent.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com')
    $uri = [Uri]$Asset.url
    if ($uri.Scheme -ne 'https' -or $uri.Host -notin $allowed -or $uri.UserInfo) { throw 'OCR: origem HTTPS invalida' }
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(90)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('Freoa-Converter-OCR/6.0rc3')
    $partial = Join-Path $Cache ('.download-' + [Guid]::NewGuid().ToString('N') + '.part')
    $response = $null
    $output = $null
    try {
        for ($redirect = 0; $redirect -le 5; $redirect++) {
            if ($uri.Scheme -ne 'https' -or $uri.Host -notin $allowed -or $uri.UserInfo) { throw 'OCR: redirect HTTPS invalido' }
            $response = $client.GetAsync($uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            $code = [int]$response.StatusCode
            if ($code -in @(301, 302, 303, 307, 308)) {
                if ($redirect -eq 5 -or -not $response.Headers.Location) { throw 'OCR: excesso de redirects' }
                $uri = New-Object Uri($uri, $response.Headers.Location)
                $response.Dispose(); $response = $null
                continue
            }
            $response.EnsureSuccessStatusCode() | Out-Null
            break
        }
        $contentLength = $response.Content.Headers.ContentLength
        if ($contentLength -and $contentLength -ne [long]$Asset.bytes) { throw 'OCR: tamanho HTTP diverge do lock' }
        $input = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        try {
            $output = [IO.File]::Open($partial, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $buffer = New-Object byte[] 65536
            [long]$total = 0
            $deadline = [DateTime]::UtcNow.AddSeconds(180)
            while ($true) {
                $readTask = $input.ReadAsync($buffer, 0, $buffer.Length)
                if (-not $readTask.Wait(90000)) { throw 'OCR: download sem progresso no prazo' }
                $read = $readTask.GetAwaiter().GetResult()
                if ($read -eq 0) { break }
                $total += $read
                if ($total -gt [long]$Asset.bytes -or [DateTime]::UtcNow -gt $deadline) { throw 'OCR: download excede limite/prazo' }
                $output.Write($buffer, 0, $read)
            }
            $output.Dispose(); $output = $null
        } finally { $input.Dispose() }
        $checked = Get-OcrLockedFile $partial $Asset
        $checked.Handle.Dispose()
        # No overwrite: another bootstrap may have populated this cache meanwhile.
        [IO.File]::Move($partial, $target)
        return Get-OcrLockedFile $target $Asset
    } finally {
        if ($output) { $output.Dispose() }
        if ($response) { $response.Dispose() }
        $client.Dispose(); $handler.Dispose()
        if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force }
    }
}

function Invoke-OcrPrivateTool([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory, [int]$TimeoutSeconds = 60) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    # Windows file names cannot contain quotes. None of our arguments ends in a
    # backslash; paths are complete files or non-root directory names.
    if (@($Arguments | Where-Object { $_.Contains('"') -or $_.EndsWith('\') }).Count) { throw 'OCR: argumento nativo invalido' }
    $info.Arguments = (($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' ')
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.EnvironmentVariables['TESSDATA_PREFIX'] = (Join-Path $WorkingDirectory 'tessdata')
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        if (-not $process.Start()) { throw 'OCR: ferramenta nao iniciou' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            # Only our newly-created private process tree; no installer execution.
            & "$env:SystemRoot\System32\taskkill.exe" /PID $process.Id /T /F 2>&1 | Out-Null
            $process.WaitForExit(5000) | Out-Null
            throw 'OCR: ferramenta excedeu prazo'
        }
        $out = $stdout.GetAwaiter().GetResult(); $err = $stderr.GetAwaiter().GetResult()
        if ($out.Length + $err.Length -gt 1000000) { throw 'OCR: log de ferramenta excedeu limite' }
        if ($process.ExitCode -ne 0) { throw "OCR: ferramenta falhou ($($process.ExitCode)): $err" }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $out; Stderr = $err }
    } finally { $process.Dispose() }
}

function Install-FreoaOcr {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Destination,
          [string[]]$Languages = @('por', 'eng', 'spa', 'ita', 'fra', 'deu'),
          [string]$CacheDirectory, [Parameter(Mandatory)][string]$LockPath, [switch]$Offline)
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) { throw 'OCR: requer Windows x64' }
    if (-not [IO.Path]::IsPathRooted($Destination)) { throw 'OCR: destino deve ser absoluto' }
    $destinationFull = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/')
    $parent = [IO.Path]::GetDirectoryName($destinationFull)
    if (-not $parent -or -not [IO.Directory]::Exists($parent) -or $destinationFull -eq [IO.Path]::GetPathRoot($destinationFull).TrimEnd('\')) {
        throw 'OCR: destino requer pai existente e diretorio novo'
    }
    Assert-OcrNoLinks $destinationFull
    if (Get-Item -LiteralPath $destinationFull -Force -ErrorAction SilentlyContinue) { throw 'OCR: destino ja existe; nao reutilizado' }
    $lockBytes = [IO.File]::ReadAllBytes($LockPath)
    if ($lockBytes.Length -gt 1000000) { throw 'OCR: lock excede limite' }
    $lockText = [Text.Encoding]::UTF8.GetString($lockBytes).TrimStart([char]0xFEFF)
    $lock = $lockText | ConvertFrom-Json
    $lockAlgorithm = [Security.Cryptography.SHA256]::Create()
    try { $lockHash = [BitConverter]::ToString($lockAlgorithm.ComputeHash($lockBytes)).Replace('-', '').ToLowerInvariant() }
    finally { $lockAlgorithm.Dispose() }
    if ($lock.schema_version -ne 1 -or $lock.platform -ne 'windows-x64' -or $lock.engine_version -ne '5.5.3') { throw 'OCR: lock incompativel' }
    # Python/native argv transports arrays as one comma/plus-separated value.
    $Languages = @($Languages | ForEach-Object { $_ -split '[,+]' } | ForEach-Object { $_.Trim().ToLowerInvariant() })
    if (-not $Languages.Count -or @($Languages | Select-Object -Unique).Count -ne $Languages.Count -or @($Languages | Where-Object { $_ -notin $lock.default_languages }).Count) {
        throw 'OCR: idiomas invalidos; use por, eng, spa, ita, fra ou deu'
    }
    foreach ($member in @($lock.seven_zip_members) + @($lock.tesseract_members)) { Assert-OcrMemberName $member.path }
    $cache = if ($CacheDirectory) { [IO.Path]::GetFullPath($CacheDirectory) } else { Join-Path $parent '.ocr-download-cache' }
    if ($cache -eq $destinationFull -or $cache.StartsWith($destinationFull + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'OCR: cache deve ficar fora do destino' }
    Assert-OcrNoLinks $cache
    if (-not [IO.Directory]::Exists($cache)) { New-Item -ItemType Directory -Path $cache | Out-Null }
    $held = New-Object Collections.Generic.List[object]
    $created = $false
    try {
        $assets = @{}
        $wanted = @('tesseract-ocr-w64-setup-5.5.3.20260724.exe', '7zr.exe', '7z2603-x64.exe', 'TESSDATA-LICENSE.txt') + @($Languages | ForEach-Object { $_ + '.traineddata' })
        foreach ($name in $wanted) {
            $matches = @($lock.assets | Where-Object { $_.name -eq $name })
            if ($matches.Count -ne 1) { throw "OCR: asset unico ausente no lock: $name" }
            $asset = Save-OcrPinnedAsset $matches[0] $cache $Offline.IsPresent
            $held.Add($asset); $assets[$name] = $asset.Path
        }
        # Reserve atomically after all downloads verified; any preexisting path fails.
        New-Item -ItemType Directory -Path $destinationFull -ErrorAction Stop | Out-Null
        $created = $true
        $scratch = Join-Path $destinationFull '.bootstrap-tools'
        New-Item -ItemType Directory -Path $scratch | Out-Null
        $privateInputs = Join-Path $scratch 'inputs'
        New-Item -ItemType Directory -Path $privateInputs | Out-Null
        $privateAssets = @{}
        foreach ($name in @('7zr.exe', '7z2603-x64.exe', 'tesseract-ocr-w64-setup-5.5.3.20260724.exe')) {
            # Read from the authenticated, write/delete-denying handle. Native
            # programs only consume fresh private copies, never OneDrive inputs.
            $source = @($held | Where-Object { $_.Path -eq $assets[$name] })[0]
            $copy = Join-Path $privateInputs $name
            $output = [IO.File]::Open($copy, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try { $source.Handle.Position = 0; $source.Handle.CopyTo($output) }
            finally { $output.Dispose(); $source.Handle.Position = 0 }
            $specification = @($lock.assets | Where-Object { $_.name -eq $name })[0]
            $bound = Get-OcrLockedFile $copy $specification
            $held.Add($bound); $privateAssets[$name] = $copy
        }
        $sevenArgs = @('x', $privateAssets['7z2603-x64.exe'], '-y', '-bd', '-bso0', '-bsp0', ('-o' + $scratch), '--') + @($lock.seven_zip_members | ForEach-Object { $_.path })
        Invoke-OcrPrivateTool $privateAssets['7zr.exe'] $sevenArgs $scratch | Out-Null
        foreach ($member in $lock.seven_zip_members) { $bound = Get-OcrLockedFile (Join-Path $scratch $member.path) $member; $held.Add($bound) }
        $engineArgs = @('x', $privateAssets['tesseract-ocr-w64-setup-5.5.3.20260724.exe'], '-y', '-bd', '-bso0', '-bsp0', ('-o' + $destinationFull), '--') + @($lock.tesseract_members | ForEach-Object { $_.path })
        Invoke-OcrPrivateTool (Join-Path $scratch '7z.exe') $engineArgs $scratch | Out-Null
        foreach ($member in $lock.tesseract_members) { $bound = Get-OcrLockedFile (Join-Path $destinationFull $member.path) $member; $held.Add($bound) }
        foreach ($language in $Languages) { Copy-Item -LiteralPath $assets[($language + '.traineddata')] -Destination (Join-Path $destinationFull ('tessdata/' + $language + '.traineddata')) }
        Copy-Item -LiteralPath $assets['TESSDATA-LICENSE.txt'] -Destination (Join-Path $destinationFull 'doc/TESSDATA-LICENSE.txt')
        Copy-Item -LiteralPath (Join-Path $scratch 'License.txt') -Destination (Join-Path $destinationFull 'doc/7ZIP-LICENSE.txt')
        foreach ($language in $Languages) {
            $spec = @($lock.assets | Where-Object { $_.name -eq ($language + '.traineddata') })[0]
            $bound = Get-OcrLockedFile (Join-Path $destinationFull ('tessdata/' + $language + '.traineddata')) $spec; $held.Add($bound)
        }
        $exe = Join-Path $destinationFull 'tesseract.exe'; $tessdata = Join-Path $destinationFull 'tessdata'
        $version = Invoke-OcrPrivateTool $exe @('--version') $destinationFull 15
        if ($version.Stdout -notmatch '(?m)^tesseract v?5\.5\.3(?:\.|\s|$)') { throw 'OCR: versao inesperada' }
        $listed = Invoke-OcrPrivateTool $exe @('--tessdata-dir', $tessdata, '--list-langs') $destinationFull 15
        $available = @($listed.Stdout -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[a-z]{3}$' })
        if (@($Languages | Where-Object { $_ -notin $available }).Count) { throw 'OCR: idioma instalado nao listado pelo motor' }
        $receipt = [ordered]@{ schema_version = 1; version = $version.Stdout.Split("`n")[0].Trim(); languages = $Languages; lock_sha256 = $lockHash; installers_executed = $false; global_environment_changed = $false }
        $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $destinationFull 'ocr-runtime.json') -Encoding UTF8
        foreach ($entry in $held) { $entry.Handle.Dispose() }; $held.Clear()
        Assert-OcrNoLinks $scratch
        if ([IO.Path]::GetFullPath($scratch) -ne (Join-Path $destinationFull '.bootstrap-tools')) { throw 'OCR: cleanup fora do stage' }
        Remove-Item -LiteralPath $scratch -Recurse -Force
        return [pscustomobject]@{ Tesseract = $exe; Tessdata = $tessdata; Languages = $Languages; Version = $receipt.version; Ready = $true; CacheDirectory = $cache }
    } catch {
        foreach ($entry in $held) { $entry.Handle.Dispose() }; $held.Clear()
        if ($created) {
            # Only the directory this call reserved; never a reused installation.
            Assert-OcrNoLinks $destinationFull
            if ([IO.Path]::GetDirectoryName($destinationFull) -ne $parent) { throw 'OCR: cleanup fora do pai reservado' }
            $links = @(Get-ChildItem -LiteralPath $destinationFull -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
            if ($links.Count) { throw 'OCR: cleanup recusado por link inesperado; stage preservado' }
            Remove-Item -LiteralPath $destinationFull -Recurse -Force
        }
        throw
    } finally { foreach ($entry in $held) { $entry.Handle.Dispose() } }
}

if ($Destination) {
    Install-FreoaOcr -Destination $Destination -Languages $Languages -CacheDirectory $CacheDirectory -LockPath $LockPath -Offline:$Offline
}
