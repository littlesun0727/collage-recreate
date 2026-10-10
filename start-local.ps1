param([int]$Port = 8795)
$ErrorActionPreference = 'Stop'
$repoPath = $PSScriptRoot
$pythonPath = 'D:/codes/visual-recreate-validation/clean-env/Scripts/python.exe'
$sdkPath = 'D:/codes/collage_batch/node_modules/@openai/codex-sdk/dist/index.js'
$cliPath = 'C:/Users/admin/AppData/Local/Programs/OpenAI/Codex/bin/codex.exe'
$modelPath = 'D:/codes/visual-recreate-validation/models/birefnet-lite-fp32.onnx'
$dataPath = 'D:/codes/collage_outputs/local-workbench'
$url = "http://127.0.0.1:$Port"
foreach ($requiredPath in @($pythonPath,$sdkPath,$cliPath,$modelPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Missing dependency: $requiredPath" }
}
$runningConfig = $null
try { $runningConfig = Invoke-RestMethod -Uri "$url/api/chat-config" -TimeoutSec 2 } catch {}
if ($runningConfig) {
    if (-not $runningConfig.create_enabled) { throw "Port $Port already hosts a workbench without automatic creation. Choose another port." }
    Write-Output "Workbench ready: $url"
    return
}
New-Item -ItemType Directory -Path "$dataPath/tasks" -Force | Out-Null
$arguments = @('-B','-m','workbench','--runs',"$dataPath/tasks",'--port',"$Port",
    '--enable-create','--enable-chat','--enable-editor','--enable-motion',
    '--media-root',"$dataPath/service",'--chat-state',"$dataPath/chat.sqlite",
    '--cutout-model',$modelPath,'--sdk-path',$sdkPath,'--codex-path',$cliPath)
$quotedArguments = ($arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
$serverProcess = Start-Process -FilePath $pythonPath -ArgumentList $quotedArguments -WorkingDirectory $repoPath -WindowStyle Hidden -PassThru -RedirectStandardOutput "$dataPath/server-out.log" -RedirectStandardError "$dataPath/server-error.log"
$serverProcess.Id | Set-Content -LiteralPath "$dataPath/server.pid"
for ($attempt=0;$attempt -lt 40;$attempt++) {
    Start-Sleep -Milliseconds 500
    $serverProcess.Refresh()
    if ($serverProcess.HasExited) { throw "Workbench failed. See $dataPath/server-error.log" }
    try {
        $ready = Invoke-RestMethod -Uri "$url/api/chat-config" -TimeoutSec 2
        if ($ready.create_enabled) { Write-Output "Workbench ready: $url"; return }
    } catch {}
}
throw "Startup pending; check $dataPath/server-error.log"
