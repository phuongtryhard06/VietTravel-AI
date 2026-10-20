$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path $reportDir 'build\english-watch.pid'

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host 'The English PDF watcher is not running.'
    exit 0
}

$watchPidText = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
if ($watchPidText -notmatch '^\d+$') {
    throw "The watcher PID file is invalid: $pidFile"
}

$watchProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $watchPidText" -ErrorAction SilentlyContinue
if (-not $watchProcess) {
    Write-Host 'The English PDF watcher process has already stopped.'
    exit 0
}
if ($watchProcess.CommandLine -notmatch 'watch_english_pdf\.ps1') {
    throw "Process $watchPidText does not match the English PDF watcher; it was not stopped."
}

Stop-Process -Id ([int]$watchPidText) -Force
Write-Host 'Stopped the English PDF watcher.'
