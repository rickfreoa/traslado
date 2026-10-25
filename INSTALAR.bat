@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALAR.ps1" -Launch %*
if errorlevel 1 (
  echo Instalacao nao concluida. Leia a mensagem acima.
  pause
  exit /b 1
)
echo Instalacao concluida. Use o atalho Freoa Markdown Converter na Area de Trabalho.
pause
