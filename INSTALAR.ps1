[CmdletBinding()]
param(
    [ValidateSet('Full','Core')][string]$Profile = 'Full',
    [string]$Python,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Freoa\MarkdownConverter'),
    [string]$DataRoot = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Freoa Markdown Converter'),
    [string]$ShortcutDirectory = ([Environment]::GetFolderPath('Desktop')),
    [string[]]$OCRLanguages = @('por','eng','spa','ita','fra','deu'),
    [switch]$SkipOCR,
    [switch]$NoShortcut,
    [switch]$Launch,
    [string]$LegacyRoot
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
function Find-TaskPython {
    param([string]$Command, [string[]]$Leading=@())
    try {
        $taskFound = & $Command @Leading -I -S -X utf8 -c 'import sys,tkinter; print(sys._base_executable); sys.exit(0 if (3,11)<=sys.version_info[:2]<(3,15) else 9)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $taskFound) { return ($taskFound | Select-Object -Last 1) }
    } catch {}
    return $null
}
if ($Python) {
    $taskPython = Find-TaskPython $Python
} else {
    $taskPython = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($taskVersion in @('-3.14','-3.13','-3.12','-3.11')) {
            $taskPython = Find-TaskPython 'py' @($taskVersion)
            if ($taskPython) { break }
        }
    }
    if (-not $taskPython -and (Get-Command python -ErrorAction SilentlyContinue)) { $taskPython = Find-TaskPython 'python' }
}
if (-not $taskPython) { throw 'Instale Python 3.11–3.14 com Tcl/Tk. O Python deve vir de uma instalação confiável.' }
$taskArgs = @('-I', '-X', 'utf8', (Join-Path $PSScriptRoot 'scripts\install_windows.py'),
    '--package', $PSScriptRoot, '--root', $InstallRoot, '--data-root', $DataRoot,
    '--shortcut-directory', $ShortcutDirectory, '--profile', $Profile,
    '--languages', ($OCRLanguages -join ','))
if ($SkipOCR) { $taskArgs += '--skip-ocr' }
if ($NoShortcut) { $taskArgs += '--no-shortcut' }
if ($Launch) { $taskArgs += '--launch' }
if ($LegacyRoot) { $taskArgs += @('--legacy-root', $LegacyRoot) }
& $taskPython @taskArgs
if ($LASTEXITCODE -ne 0) { throw 'Instalação não concluída. A versão anterior foi preservada quando possível; confira o diagnóstico.' }
