$ErrorActionPreference = 'Stop'
$lanRoot = Join-Path $PSScriptRoot 'demo\workspace\lan-access'
$resultPath = Join-Path $lanRoot 'firewall-result.json'
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Administrator approval is required to add this firewall rule.'
    }
    $ruleName = 'Collage-Office-HTTPS-8443'
    $existing = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
    if ($existing) { throw 'Rule already exists. Inspect its scope before making any change.' }
    New-NetFirewallRule -Name $ruleName -DisplayName 'Collage Office HTTPS (Huawei-Guest subnet only)' -Direction Inbound -Action Allow -Enabled True -Profile Any -Protocol TCP -LocalPort 8443 -LocalAddress '7.249.217.32' -RemoteAddress '7.249.217.0/24' -InterfaceAlias 'WLAN' -Program 'C:\Users\admin\anaconda3\python.exe' -EdgeTraversalPolicy Block | Out-Null
    @{success=$true;rule=$ruleName;localAddress='7.249.217.32';remoteAddress='7.249.217.0/24';port=8443} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    @{success=$false;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
}
