$ErrorActionPreference = 'Stop'
$statePath = Join-Path $PSScriptRoot 'demo\workspace\lan-access\cloudflare-preview-state.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Output 'No preview state found.'; return }
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
$expected = @(
    @{Id=$state.tunnelPid;Executable='D:\codes\.tools\cloudflared\cloudflared.exe';Argument="http://127.0.0.1:$($state.port)"},
    @{Id=$state.gatewayPid;Executable='C:\Users\admin\anaconda3\python.exe';Argument='cloudflare-preview-config.json'}
)
foreach ($item in $expected) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($item.Id)"
    if (-not $process) { continue }
    if ($process.ExecutablePath -ne $item.Executable -or -not $process.CommandLine.Contains($item.Argument)) {
        throw "PID $($item.Id) no longer matches the preview process. No action taken on it."
    }
    Stop-Process -Id $item.Id
}
Write-Output 'Public preview stopped. Local workbench and production jobs continue running.'
