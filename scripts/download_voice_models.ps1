# Download Piper voice models for brain TTS (T-023).
# Stores .onnx + .onnx.json under data/voices/ (relative to repo / MIMIR_DATA_DIR).

param(
    [string]$OutDir = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) {
    $OutDir = Join-Path $RepoRoot "data\voices"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# Hugging Face rhasspy/piper-voices raw paths (medium quality).
$Voices = @(
    @{
        Name = "nl_NL-pim-medium"
        Base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/nl/nl_NL/pim/medium"
    },
    @{
        Name = "en_US-lessac-medium"
        Base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
    }
)

function Get-FileIfMissing {
    param([string]$Url, [string]$Dest)
    if ((Test-Path $Dest) -and -not $Force) {
        Write-Host "skip (exists): $Dest"
        return
    }
    Write-Host "download: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
}

foreach ($v in $Voices) {
    $onnx = Join-Path $OutDir "$($v.Name).onnx"
    $json = Join-Path $OutDir "$($v.Name).onnx.json"
    Get-FileIfMissing -Url "$($v.Base)/$($v.Name).onnx" -Dest $onnx
    Get-FileIfMissing -Url "$($v.Base)/$($v.Name).onnx.json" -Dest $json
}

Write-Host ""
Write-Host "Voice models ready in: $OutDir"
Write-Host "Ensure config voice.tts.voices points at voices/nl_NL-pim-medium.onnx under runtime.data_dir."
