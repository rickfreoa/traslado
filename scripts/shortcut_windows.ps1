param([ValidateSet('Inspect','Create')][string]$Action, [string]$ShortcutPath, [string]$Target, [string]$WorkingDirectory)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$taskShell = New-Object -ComObject WScript.Shell
$taskLink = $taskShell.CreateShortcut($ShortcutPath)
if ($Action -eq 'Create') {
    if (Test-Path -LiteralPath $ShortcutPath) { throw 'Atalho temporário já existe.' }
    $taskLink.TargetPath = $Target
    $taskLink.Arguments = '-I -m freoa_v6 gui'
    $taskLink.WorkingDirectory = $WorkingDirectory
    $taskLink.Description = 'Freoa Markdown Converter - converter arquivos e pastas'
    $taskLink.IconLocation = (Join-Path $env:SystemRoot 'System32\imageres.dll') + ',109'
    $taskLink.Save()
}
$taskRecognized = ($taskLink.TargetPath -match '\\\.venv\\Scripts\\pythonw?\.exe$') -and
    ($taskLink.Arguments -match '(^|\s)-m\s+(freoa_md\.gui|freoa_v6(\.gui|\s+gui))(\s|$)')
@{ target=$taskLink.TargetPath; arguments=$taskLink.Arguments; working_directory=$taskLink.WorkingDirectory; recognized=$taskRecognized } | ConvertTo-Json -Compress
