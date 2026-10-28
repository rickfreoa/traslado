# Texts and generated fixtures dedicated to the public domain (CC0).
$ErrorActionPreference = 'Stop'
$fixtureRoot = $PSScriptRoot
$word = $null
$doc = $null
$powerpoint = $null
$presentation = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Add()
    $doc.Content.Text = "Acao inicial continua.`rSegundo trecho termina.`rFim do relato.`r"
    $range = $doc.Range(12, 12)
    $doc.Footnotes.Add($range, [Type]::Missing, 'Nota primeira.') | Out-Null
    $position = $doc.Content.Text.IndexOf(' termina.')
    $doc.Footnotes.Add($doc.Range($position, $position), [Type]::Missing, 'Nota segunda.') | Out-Null
    $doc.SaveAs2((Join-Path $fixtureRoot 'publico.doc'), 0)
    $doc.Close(0)
    $doc = $null
    $word.Quit()
    $word = $null
    $powerpoint = New-Object -ComObject PowerPoint.Application
    $presentation = $powerpoint.Presentations.Add(0)
    $slide = $presentation.Slides.Add(1, 2)
    $slide.Shapes.Title.TextFrame.TextRange.Text = 'Relato publico'
    $slide.Shapes.Item(2).TextFrame.TextRange.Text = "Acao inicial.`rFim do relato."
    $presentation.SaveAs((Join-Path $fixtureRoot 'publico.ppt'), 1)
} finally {
    if ($doc) { $doc.Close(0) }
    if ($word) { $word.Quit() }
    if ($presentation) { $presentation.Close() }
    if ($powerpoint) { $powerpoint.Quit() }
}
