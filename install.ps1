# Native Windows installer for H3 Higgsfield; no WSL or Bash required.
param(
    [string]$ComfyRoot = "",
    [string]$ComfyPython = "",
    [ValidateRange(1, 65535)][int]$Port = 8188,
    [ValidateSet("127.0.0.1", "0.0.0.0")][string]$Bind = "127.0.0.1",
    [switch]$Preflight,
    [switch]$NoStart,
    [ValidateSet("", "int8", "bf16", "int8,bf16")][string]$QwenImageProfiles = ""
)
$ErrorActionPreference = "Stop"
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$installer = Join-Path $project "deploy\install_windows.py"
$candidates = New-Object 'System.Collections.Generic.List[string]'
if ($ComfyPython) { $candidates.Add($ComfyPython) }
if ($ComfyRoot) {
    $candidates.Add((Join-Path $ComfyRoot "python_embeded\python.exe"))
    $candidates.Add((Join-Path $ComfyRoot "..\python_embeded\python.exe"))
}
$candidates.Add((Join-Path $project "..\ComfyUI_windows_portable\python_embeded\python.exe"))
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $hostPython = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
$prefix = @()
if (-not $hostPython) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $hostPython = $launcher.Source
        foreach ($version in @("-3.13", "-3.12", "-3")) {
            try {
                & $hostPython $version -c "import sys" 2>$null
                if ($LASTEXITCODE -eq 0) { $prefix = @($version); break }
            } catch {
                # The Python launcher reports an unavailable version on stderr.
                continue
            }
        }
        if ($prefix.Count -eq 0) { $hostPython = $null }
    }
    if (-not $hostPython) {
        $launcher = Get-Command python -ErrorAction SilentlyContinue
        if ($launcher) { $hostPython = $launcher.Source }
    }
}
if (-not $hostPython) {
    throw "Python 3 was not found. Install Python 3.13 or use ComfyUI Portable and pass -ComfyRoot to its folder."
}
$arguments = @($installer, "--port", "$Port", "--bind", $Bind)
if ($ComfyRoot) { $arguments += @("--comfy-root", $ComfyRoot) }
if ($ComfyPython) { $arguments += @("--comfy-python", $ComfyPython) }
if ($Preflight) { $arguments += "--preflight" }
if ($NoStart) { $arguments += "--no-start" }
if ($QwenImageProfiles) { $arguments += @("--qwen-image-profiles", $QwenImageProfiles) }
& $hostPython @prefix @arguments
exit $LASTEXITCODE
