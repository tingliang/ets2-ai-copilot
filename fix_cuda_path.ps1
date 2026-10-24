# fix_cuda_path.ps1
# Permanently add the CUDA DLL folders needed by faster-whisper / ctranslate2
# to the USER Path, so cublas64_12.dll is found after every reboot.
#
# Auto-locates site-packages via `python -m site` (no hard-coded version).

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "== Locating Python site-packages =="

try {
    $siteDirs = & python -c "import site; [print(p) for p in site.getsitepackages()]" 2>$null
} catch {
    $siteDirs = @()
}

if (-not $siteDirs -or $siteDirs.Count -eq 0) {
    Write-Host "[!] Cannot locate site-packages. Is Python installed?" -ForegroundColor Red
    Write-Host "    Run 'python -m site' manually to check."
    exit 1
}

$sp = $siteDirs[0].Trim()
Write-Host "    site-packages: $sp"

$add = @(
    (Join-Path $sp "ctranslate2"),
    (Join-Path $sp "nvidia\cublas\bin"),
    (Join-Path $sp "nvidia\cudnn\bin")
)

Write-Host ""
Write-Host "== Directories to add =="
foreach ($d in $add) {
    if (Test-Path $d) {
        Write-Host "    [OK]   $d"
    } else {
        Write-Host "    [SKIP] $d  (not found)"
    }
}

$add = @($add | Where-Object { Test-Path $_ })
if ($add.Count -eq 0) {
    Write-Host ""
    Write-Host "[!] None of the CUDA DLL folders exist." -ForegroundColor Red
    Write-Host "    Install them first:"
    Write-Host "    python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
    exit 1
}

Write-Host ""
Write-Host "== Updating USER Path (permanent) =="

$cur = [Environment]::GetEnvironmentVariable("Path", "User")
if ($null -eq $cur) { $cur = "" }
$cur = $cur.TrimEnd(";")

$addedCount = 0
foreach ($d in $add) {
    $already = $false
    foreach ($part in ($cur -split ";")) {
        if ($part.Trim() -ieq $d) { $already = $true; break }
    }
    if (-not $already) {
        if ($cur -eq "") { $cur = $d } else { $cur = "$cur;$d" }
        Write-Host "    [+] added: $d"
        $addedCount++
    } else {
        Write-Host "    [=] already present: $d"
    }
}

[Environment]::SetEnvironmentVariable("Path", $cur, "User")

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
Write-Host "    Added $addedCount folder(s) to USER Path (permanent)."
Write-Host ""
Write-Host "    IMPORTANT: Close this window, open a NEW PowerShell,"
Write-Host "    then run:  python main.py --debug-timing"
Write-Host ""
