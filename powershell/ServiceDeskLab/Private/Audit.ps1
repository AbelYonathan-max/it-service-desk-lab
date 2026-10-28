function Write-LabAudit {
    <#
    .SYNOPSIS
    Records what was done to an account, by whom and under which ticket, as one JSON line.

    .DESCRIPTION
    Lines go to a daily file in SDLAB_LOG_DIR (default C:\ProgramData\ServiceDeskLab\logs on
    Windows, the temp folder elsewhere). On the lab server the same entry also goes to the
    "ServiceDeskLab" Windows event log source, which Azure Monitor collects. Never pass passwords
    in -Detail.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Action,
        [Parameter(Mandatory = $true)][string]$Target,
        [string]$Reference,
        [hashtable]$Detail = @{},
        [ValidateSet('Information', 'Warning', 'Error')][string]$Level = 'Information'
    )

    $entry = [ordered]@{
        time      = (Get-Date).ToUniversalTime().ToString('o')
        action    = $Action
        target    = $Target
        reference = $Reference
        actor     = [Environment]::UserName
        host      = [Environment]::MachineName
        detail    = $Detail
    }
    $json = $entry | ConvertTo-Json -Compress -Depth 5

    $folder = $env:SDLAB_LOG_DIR
    if (-not $folder) {
        $folder = if ($env:ProgramData) { Join-Path $env:ProgramData 'ServiceDeskLab\logs' } else { Join-Path ([IO.Path]::GetTempPath()) 'ServiceDeskLab' }
    }
    try {
        if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder -Force | Out-Null }
        $file = Join-Path $folder ('audit-{0:yyyy-MM-dd}.jsonl' -f (Get-Date))
        [IO.File]::AppendAllText($file, $json + [Environment]::NewLine, (New-Object Text.UTF8Encoding $false))
    }
    catch {
        Write-Warning "Could not write the audit log: $($_.Exception.Message)"
    }

    if ($script:EventSourceReady -ne $false -and (Get-Command -Name Write-EventLog -ErrorAction SilentlyContinue)) {
        try {
            $eventId = switch ($Level) { 'Information' { 1000 } 'Warning' { 2000 } default { 3000 } }
            Write-EventLog -LogName Application -Source ServiceDeskLab -EventId $eventId -EntryType $Level -Message $json -ErrorAction Stop
            $script:EventSourceReady = $true
        }
        catch {
            # The source is registered by the install script; without it, the file log is enough.
            $script:EventSourceReady = $false
        }
    }
}
