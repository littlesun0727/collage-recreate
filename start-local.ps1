param(
    [int]$Port = 8796,
    [string]$Python = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe',
    [string]$VlmEnv = '',
    [string]$RevealEnv = '',
    [string]$CutoutModel = 'D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx',
    [string]$Data = ''
)
$ErrorActionPreference = 'Stop'
$repoPath = $PSScriptRoot
if (-not $VlmEnv) {
    $VlmEnv = if ($env:COLLAGE_VLM_ENV) { $env:COLLAGE_VLM_ENV } else { Join-Path $repoPath '.env' }
}
if (-not $RevealEnv) {
    $RevealEnv = if ($env:COLLAGE_REVEAL_ENV) { $env:COLLAGE_REVEAL_ENV } else { Join-Path $repoPath '.env' }
}
if (-not $Data) {
    $outputPath = if ($env:COLLAGE_OUTPUT_ROOT) { $env:COLLAGE_OUTPUT_ROOT } else { "$repoPath-output" }
    $Data = Join-Path $outputPath 'runtime'
}
foreach ($dependency in @($Python,$VlmEnv,$RevealEnv)) {
    if (-not (Test-Path -LiteralPath $dependency)) { throw "Missing dependency: $dependency" }
}
$url = "http://127.0.0.1:$Port"
$existing = $null
try { $existing = Invoke-RestMethod -Uri "$url/api/chat-config" -TimeoutSec 2 } catch {}
if ($existing) {
    if ($existing.controller -ne 'workflow-v6') { throw "Port $Port hosts another application. Choose another port." }
    Write-Output "Workbench ready: $url"
    return
}
New-Item -ItemType Directory -Path (Join-Path $Data 'tasks') -Force | Out-Null
$arguments = @('-B','-m','workbench','--runs',(Join-Path $Data 'tasks'),'--port',"$Port",
    '--enable-create','--enable-chat','--enable-editor','--enable-motion',
    '--media-root',(Join-Path $Data 'service'),'--chat-state',(Join-Path $Data 'chat.sqlite'),
    '--vlm-env',$VlmEnv,'--reveal-env',$RevealEnv)
if ($CutoutModel -and (Test-Path -LiteralPath $CutoutModel)) { $arguments += @('--cutout-model',$CutoutModel) }
$quoted = ($arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
$server = Start-Process -FilePath $Python -ArgumentList $quoted -WorkingDirectory $repoPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $Data 'server-out.log') -RedirectStandardError (Join-Path $Data 'server-error.log')
$server.Id | Set-Content -LiteralPath (Join-Path $Data 'server.pid')
for ($attempt=0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    $server.Refresh()
    if ($server.HasExited) { throw "Workbench failed. Inspect $Data/server-error.log" }
    try {
        $ready = Invoke-RestMethod -Uri "$url/api/chat-config" -TimeoutSec 2
        if ($ready.controller -eq 'workflow-v6') { Write-Output "Workbench ready: $url"; return }
    } catch {}
}
throw "Startup pending; inspect $Data/server-error.log"
