$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$repoPath = Join-Path $PSScriptRoot 'vendor\MediaCrawler'
$venvPath = Join-Path $PSScriptRoot '.venv-mediacrawler'
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'mediacrawler.lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not (Test-Path -LiteralPath $repoPath)) {
    git clone $manifest.repository $repoPath
    if ($LASTEXITCODE -ne 0) { throw 'Clone failed' }
    git -C $repoPath checkout --detach $manifest.commit
    if ($LASTEXITCODE -ne 0) { throw 'Pinned checkout failed' }
}
$actualCommit = git -C $repoPath rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $manifest.commit) {
    throw 'Existing checkout does not match mediacrawler.lock.json; inspect it before proceeding.'
}
if (-not (Test-Path -LiteralPath $venvPath)) {
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
$pythonPath = Join-Path $venvPath 'Scripts\python.exe'
$testedLock = Join-Path $PSScriptRoot 'requirements-mediacrawler-lock.txt'
if (Test-Path -LiteralPath $testedLock) {
    & $pythonPath -m pip --disable-pip-version-check install --no-input -r $testedLock
} else {
    & $pythonPath -m pip --disable-pip-version-check install --no-input -r (Join-Path $repoPath 'requirements.txt')
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Write-Host 'Ready. Run: .\.venv-mediacrawler\Scripts\python.exe pipeline.py status'
