# STT → chat → TTS round-trip with local mic (T-023 acceptance).
param(
    [string]$Url = "http://127.0.0.1:8000",
    [string]$Locale = "nl",
    [double]$Seconds = 5,
    [string]$Audio = "",
    [string]$Device = "",
    [switch]$NoPlay
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    $args = @(
        "run", "python", "scripts/voice_roundtrip.py",
        "--url", $Url,
        "--locale", $Locale,
        "--seconds", "$Seconds"
    )
    if ($Audio) { $args += @("--audio", $Audio) }
    if ($Device) { $args += @("--device", $Device) }
    if ($NoPlay) { $args += "--no-play" }
    & uv @args
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
