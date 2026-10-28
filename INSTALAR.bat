@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
REM Copyright (C) 2026 Ricardo Peres Freoa
REM Fonte correspondente: https://github.com/rickfreoa/traslado/tree/v2.0.0
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -FromDir "%~dp0." %*
if errorlevel 1 (
  echo Instalacao nao concluida. Leia a mensagem acima.
  pause
  exit /b %errorlevel%
)
echo Instalacao concluida. Use o atalho Traslado na Area de Trabalho ou no Menu Iniciar.
pause
