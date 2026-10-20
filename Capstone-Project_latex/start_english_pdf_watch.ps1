$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildDir = Join-Path $reportDir 'build'
$pidFile = Join-Path $buildDir 'english-watch.pid'
$watchScript = Join-Path $reportDir 'watch_english_pdf.ps1'
$stdout = Join-Path $buildDir 'english-watch.log'
$stderr = Join-Path $buildDir 'english-watch-error.log'

New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
if (Test-Path -LiteralPath $pidFile) {
    $oldPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
    if ($oldPid -match '^\d+$') {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $oldPid" -ErrorAction SilentlyContinue
        if ($existing -and $existing.CommandLine -match 'watch_english_pdf\.ps1') {
            Write-Host "The English PDF watcher is already running as process $oldPid."
            exit 0
        }
    }
}

$shell = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if (-not $shell) {
    $shellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
}
else {
    $shellPath = $shell.Source
}

$quotedScript = [char]34 + $watchScript + [char]34
$arguments = "-NoProfile -ExecutionPolicy Bypass -File $quotedScript"
$process = Start-Process -FilePath $shellPath -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Start-Sleep -Milliseconds 700

if (Test-Path -LiteralPath $pidFile) {
    Write-Host "Started the English PDF watcher as process $($process.Id)."
}
else {
    Write-Host "Started the English PDF watcher as process $($process.Id); it is still initializing."
}
