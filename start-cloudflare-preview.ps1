$ErrorActionPreference = 'Stop'
$repoPath = $PSScriptRoot
$privateRoot = Join-Path $repoPath 'demo\workspace\lan-access'
$statePath = Join-Path $privateRoot 'cloudflare-preview-state.json'
$pythonPath = 'C:\Users\admin\anaconda3\python.exe'
$cloudflaredPath = 'D:\codes\.tools\cloudflared\cloudflared.exe'
$gatewayPort = 8806
$gatewayProcess = $null
$tunnelProcess = $null
if (-not (Test-Path -LiteralPath $cloudflaredPath)) { throw 'cloudflared is not installed.' }
if (Test-Path -LiteralPath $statePath) {
    $oldState = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $oldTunnel = Get-Process -Id $oldState.tunnelPid -ErrorAction SilentlyContinue
    if ($oldTunnel -and $oldTunnel.Path -eq $cloudflaredPath) {
        throw "Preview already exists: $($oldState.url). Use stop-cloudflare-preview.ps1 before restarting."
    }
}
if (Get-NetTCPConnection -LocalPort $gatewayPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $gatewayPort is already occupied; refusing to publish an unknown service."
}
$baseConfig = Get-Content -LiteralPath (Join-Path $privateRoot 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$configPath = Join-Path $privateRoot 'cloudflare-preview-config.json'
$tunnelLog = Join-Path $privateRoot 'cloudflare-preview-tunnel.log'
try {
    # The target port has no listener until the exact generated origin is known.
    $tunnelArgs = @('tunnel','--no-autoupdate','--url',"http://127.0.0.1:$gatewayPort",'--protocol','http2','--edge-ip-version','4','--metrics','127.0.0.1:20242')
    $tunnelProcess = Start-Process -FilePath $cloudflaredPath -ArgumentList $tunnelArgs -WorkingDirectory $privateRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privateRoot 'cloudflare-preview-tunnel-out.log') -RedirectStandardError $tunnelLog -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    $publicOrigin = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        $tunnelProcess.Refresh()
        if ($tunnelProcess.HasExited) { throw "Tunnel exited. Diagnostic log: $tunnelLog" }
        if (Test-Path -LiteralPath $tunnelLog) {
            $logText = Get-Content -LiteralPath $tunnelLog -Raw -ErrorAction SilentlyContinue
            if ($logText) {
                $match = [regex]::Match($logText, 'https://[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com')
                if ($match.Success) { $publicOrigin = $match.Value; break }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $publicOrigin) { throw "Timed out creating the temporary hostname. Diagnostic log: $tunnelLog" }
    $config = @{bind='127.0.0.1';port=$gatewayPort;network='127.0.0.0/8';public_origin=$publicOrigin;users=$baseConfig.users}
    [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $gatewayArgs = @('-B','-m','workbench.lan_gateway','--config',$configPath)
    $quotedArgs = ($gatewayArgs | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $gatewayProcess = Start-Process -FilePath $pythonPath -ArgumentList $quotedArgs -WorkingDirectory $repoPath -WindowStyle Hidden -RedirectStandardOutput (Join-Path $privateRoot 'cloudflare-preview-gateway-out.log') -RedirectStandardError (Join-Path $privateRoot 'cloudflare-preview-gateway-error.log') -PassThru
    $state = @{url=$publicOrigin;tunnelPid=$tunnelProcess.Id;gatewayPid=$gatewayProcess.Id;port=$gatewayPort;createdAt=[DateTime]::UtcNow.ToString('o')}
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Write-Output "Temporary HTTPS entry: $publicOrigin"
    Write-Output 'Existing workbench username/password required. Verify access before sharing.'
} catch {
    foreach ($startedProcess in @($tunnelProcess,$gatewayProcess)) {
        if ($startedProcess) {
            $startedProcess.Refresh()
            if (-not $startedProcess.HasExited) { $startedProcess.Kill() }
        }
    }
    throw
}
