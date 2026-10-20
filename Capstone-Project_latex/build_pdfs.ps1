[CmdletBinding()]
param(
    [switch]$EnglishOnly
)

$ErrorActionPreference = 'Stop'
$reportDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $reportDir 'build'
$englishOut = Join-Path $buildRoot 'english'
$vietnameseOut = Join-Path $buildRoot 'vietnamese'
$tectonicExe = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'VietTravelReport\tectonic-0.17.0\tectonic.exe'

if (-not (Test-Path -LiteralPath $tectonicExe)) {
    $tectonicCommand = Get-Command tectonic.exe -ErrorAction SilentlyContinue
    if ($tectonicCommand) {
        $tectonicExe = $tectonicCommand.Source
    }
    else {
        throw "Tectonic is missing. Run install_tectonic.ps1 once, then build again."
    }
}

New-Item -ItemType Directory -Path $englishOut -Force | Out-Null
if (-not $EnglishOnly) {
    New-Item -ItemType Directory -Path $vietnameseOut -Force | Out-Null
}

Push-Location $reportDir
try {
    Write-Host 'Building the English report...'
    & $tectonicExe --keep-intermediates --keep-logs --synctex --outdir $englishOut 'main.tex'
    if ($LASTEXITCODE -ne 0) {
        throw "English report compilation failed with exit code $LASTEXITCODE."
    }

    $englishBuildPdf = Join-Path $englishOut 'main.pdf'
    if (-not (Test-Path -LiteralPath $englishBuildPdf)) {
        throw "The English PDF was not created at $englishBuildPdf."
    }
    Copy-Item -LiteralPath $englishBuildPdf -Destination (Join-Path $reportDir 'VietTravel_AI_Report_EN.pdf') -Force
    Write-Host "Updated $reportDir\VietTravel_AI_Report_EN.pdf"

    if (-not $EnglishOnly) {
        Write-Host 'Building the Vietnamese report...'
        & $tectonicExe --keep-intermediates --keep-logs --synctex --outdir $vietnameseOut 'main_vi.tex'
        if ($LASTEXITCODE -ne 0) {
            throw "Vietnamese report compilation failed with exit code $LASTEXITCODE."
        }

        $vietnameseBuildPdf = Join-Path $vietnameseOut 'main_vi.pdf'
        if (-not (Test-Path -LiteralPath $vietnameseBuildPdf)) {
            throw "The Vietnamese PDF was not created at $vietnameseBuildPdf."
        }
        Copy-Item -LiteralPath $vietnameseBuildPdf -Destination (Join-Path $reportDir 'VietTravel_AI_Report_VI.pdf') -Force
        Write-Host "Updated $reportDir\VietTravel_AI_Report_VI.pdf"
    }
}
finally {
    Pop-Location
}
