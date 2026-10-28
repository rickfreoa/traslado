# Textos e fixtures gerados dedicados ao domínio público (CC0).
# Gera publico.doc e publico.ppt por COM abrindo fontes existentes (doc-fonte.rtf; ppt-fonte.pptx gerado por
# gerar_pptx.py com python-pptx), como a Biblioteca faz: Documents.Add/SaveAs de documento novo pode travar em
# diálogo invisível (rótulos de confidencialidade do Office corporativo).
$ErrorActionPreference = 'Stop'
$d = $PSScriptRoot
$w = New-Object -ComObject Word.Application
try {
    $w.Visible = $false; $w.DisplayAlerts = 0; $w.AutomationSecurity = 3
    $doc = $w.Documents.Open((Join-Path $d 'doc-fonte.rtf'), $false, $true, $false)
    $doc.SaveAs2((Join-Path $d 'publico.doc'), 0); $doc.Close($false)
} finally { $w.Quit() }
$p = New-Object -ComObject PowerPoint.Application
try {
    $pr = $p.Presentations.Open((Join-Path $d 'ppt-fonte.pptx'), -1, 0, 0)
    $pr.SaveAs((Join-Path $d 'publico.ppt'), 1); $pr.Close()
} finally { $p.Quit() }
