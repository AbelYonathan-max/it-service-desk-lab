# Entry point for the agent's scheduled tasks. Reads C:\ProgramData\ServiceDeskLab\agent.json,
# runs one job and records it in a transcript next to the audit log.
param([Parameter(Mandatory = $true)][ValidateSet('Fulfilment', 'Sync', 'Cleanup')][string]$Job)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$settings = Get-Content (Join-Path $PSScriptRoot 'agent.json') -Raw | ConvertFrom-Json
$env:SDLAB_KEYVAULT = $settings.keyVault
$env:SDLAB_UPN_SUFFIX = $settings.upnSuffix
$env:SDLAB_HOME_SHARE = $settings.homeShare
$env:SDLAB_LOG_DIR = $settings.logDir

$transcript = Join-Path $settings.logDir ('{0}-{1:yyyy-MM-dd}.log' -f $Job.ToLower(), (Get-Date))
Start-Transcript -Path $transcript -Append | Out-Null
try {
    Import-Module ServiceDeskLab
    if ($Job -ne 'Cleanup') {
        try { & (Get-Module ServiceDeskLab) { Get-LabServiceNowConnection } | Out-Null }
        catch { Write-Output "ServiceNow is not connected yet: $($_.Exception.Message)"; return }
    }
    switch ($Job) {
        'Fulfilment' { Invoke-LabFulfilment | Format-Table -AutoSize | Out-String -Width 200 }
        'Sync' { Sync-LabServiceNowUser | Format-List | Out-String }
        'Cleanup' { Remove-LabExpiredLeaver -Confirm:$false | Format-Table -AutoSize | Out-String }
    }
}
catch {
    Write-Output "Failed: $($_.Exception.Message)"
    exit 1
}
finally {
    Stop-Transcript | Out-Null
}
