$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryDir = Split-Path -Parent $reportDir
$buildRoot = Join-Path $reportDir 'build'
$pidFile = Join-Path $buildRoot 'english-watch.pid'
$buildScript = Join-Path $reportDir 'build_pdfs.ps1'
$watchedExtensions = @('.tex', '.bib', '.cls', '.sty', '.png', '.jpg', '.jpeg')
$watchedCodeExtensions = @('.py', '.sql', '.json', '.yml', '.yaml', '.html', '.css', '.js')
$codeRoots = @('src', 'scripts', 'migrations', 'tests') | ForEach-Object {
    Join-Path $repositoryDir $_
} | Where-Object { Test-Path -LiteralPath $_ }

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

if (Test-Path -LiteralPath $pidFile) {
    $oldPidText = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
    $oldProcess = $null
    if ($oldPidText -match '^\d+$') {
        $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $oldPidText" -ErrorAction SilentlyContinue
    }
    if ($oldProcess -and $oldProcess.CommandLine -match 'watch_english_pdf\.ps1') {
        throw "The English PDF watcher is already running as process $oldPidText."
    }
}

Set-Content -LiteralPath $pidFile -Value $PID -Encoding ascii

function Get-ReportSourceStamp {
    $reportFiles = Get-ChildItem -LiteralPath $reportDir -Recurse -File |
        Where-Object {
            $_.FullName -notlike "$buildRoot\*" -and
            $watchedExtensions -contains $_.Extension.ToLowerInvariant()
        }
    $codeFiles = foreach ($root in $codeRoots) {
        Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object { $watchedCodeExtensions -contains $_.Extension.ToLowerInvariant() }
    }
    $entryPoint = Join-Path $repositoryDir 'main.py'
    $files = @($reportFiles) + @($codeFiles)
    if (Test-Path -LiteralPath $entryPoint) {
        $files += Get-Item -LiteralPath $entryPoint
    }
    $files = $files | Sort-Object -Property FullName

    return (($files | ForEach-Object {
        '{0}|{1}|{2}' -f $_.FullName, $_.Length, $_.LastWriteTimeUtc.Ticks
    }) -join [Environment]::NewLine)
}

try {
    Write-Host 'Starting the English PDF watcher.'
    & $buildScript -EnglishOnly
    $previousStamp = Get-ReportSourceStamp
    Write-Host 'Watching LaTeX sources, bibliography, class, styles, and figure files. Press Ctrl+C to stop.'

    while ($true) {
        Start-Sleep -Milliseconds 700
        $currentStamp = Get-ReportSourceStamp
        if ($currentStamp -ne $previousStamp) {
            Start-Sleep -Milliseconds 800
            try {
                & $buildScript -EnglishOnly
                Write-Host ("PDF refreshed at {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'))
            }
            catch {
                Write-Error $_
            }
            $previousStamp = Get-ReportSourceStamp
        }
    }
}
finally {
    $storedPid = Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue
    if ($storedPid -eq "$PID") {
        [System.IO.File]::Delete($pidFile)
    }
}
