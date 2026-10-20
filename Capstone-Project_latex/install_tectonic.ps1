$ErrorActionPreference = 'Stop'

$installDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'VietTravelReport\tectonic-0.17.0'
$tectonicExe = Join-Path $installDir 'tectonic.exe'
$archive = Join-Path $installDir 'tectonic-0.17.0-x86_64-pc-windows-msvc.zip'
$url = 'https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-pc-windows-msvc.zip'
$expectedSha256 = 'f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f'

New-Item -ItemType Directory -Path $installDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $tectonicExe)) {
    Invoke-WebRequest -Uri $url -OutFile $archive
    $actualSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "Tectonic checksum mismatch. Expected $expectedSha256, got $actualSha256."
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $installDir -Force
}

if (-not (Test-Path -LiteralPath $tectonicExe)) {
    throw "Tectonic was not found after extraction in $installDir."
}

& $tectonicExe --version
