$ErrorActionPreference = 'Stop'
$repoPath = $PSScriptRoot
$lanRoot = Join-Path $repoPath 'demo\workspace\lan-access'
$pythonPath = 'C:\Users\admin\anaconda3\python.exe'
$configPath = Join-Path $lanRoot 'config.json'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'LAN configuration is missing.' }
$lanConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$lanUrl = "https://$($lanConfig.bind):$($lanConfig.port)/"
$pidPath = Join-Path $lanRoot 'gateway.pid'
if (Test-Path -LiteralPath $pidPath) {
    $savedPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    $existing = Get-Process -Id $savedPid -ErrorAction SilentlyContinue
    if ($existing -and $existing.Path -eq $pythonPath) {
        Write-Output "LAN gateway process exists: $lanUrl"
        return
    }
}
$gatewayArgs = @('-B','-m','workbench.lan_gateway','--config',$configPath)
$quotedArgs = ($gatewayArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '
$gateway = Start-Process -FilePath $pythonPath -ArgumentList $quotedArgs -WorkingDirectory $repoPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $lanRoot 'gateway-out.log') -RedirectStandardError (Join-Path $lanRoot 'gateway-error.log') -PassThru
$gateway.Id | Set-Content -LiteralPath $pidPath
Write-Output "Started office gateway: $lanUrl (PID $($gateway.Id))"
