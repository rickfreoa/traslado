# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Ricardo Peres Freoa
param([string]$ReleaseRoot)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$taskPrefix=[IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
$taskItems=@(Get-CimInstance Win32_Process | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskPrefix,[StringComparison]::OrdinalIgnoreCase)
} | ForEach-Object { @{ pid=$_.ProcessId; executable=$_.ExecutablePath } })
ConvertTo-Json -InputObject $taskItems -Compress
