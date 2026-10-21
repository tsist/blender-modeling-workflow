$ErrorActionPreference = 'Stop'
$mwPython = $env:BLENDERCTL_PYTHON
if ([string]::IsNullOrWhiteSpace($mwPython)) {
    $mwCommand = Get-Command python -CommandType Application -ErrorAction Stop
    $mwPython = $mwCommand.Source
}
& $mwPython -B (Join-Path $PSScriptRoot 'tools\blenderctl\cli.py') @args
exit $LASTEXITCODE
