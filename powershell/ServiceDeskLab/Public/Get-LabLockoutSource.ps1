function Get-LabLockoutSource {
    <#
    .SYNOPSIS
    Lists account lockout events (4740) from the PDC emulator, with the computer they came from.

    .DESCRIPTION
    Every domain controller sends failed sign-ins on to the PDC emulator, which records the
    lockout. Event 4740 names the account and the "caller computer". Needs read access to the
    Security log on the PDC emulator (Event Log Readers).

    .EXAMPLE
    Get-LabLockoutSource -LookBackHours 4 | Group-Object CallerComputer
    #>
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [string]$Identity,
        [ValidateRange(1, 720)][int]$LookBackHours = 24
    )

    $pdc = (Get-ADDomain).PDCEmulator
    $filter = @{ LogName = 'Security'; Id = 4740; StartTime = (Get-Date).AddHours(-$LookBackHours) }
    $events = @()
    try {
        $events = @(Get-WinEvent -ComputerName $pdc -FilterHashtable $filter -ErrorAction Stop)
    }
    catch {
        # Get-WinEvent throws when nothing matches; only rethrow real failures.
        if ($_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') { throw }
    }

    foreach ($entry in $events) {
        $xml = [xml]$entry.ToXml()
        $data = @{}
        foreach ($item in $xml.Event.EventData.Data) { $data[$item.Name] = $item.'#text' }
        if ($Identity -and $data['TargetUserName'] -ne $Identity) { continue }
        # Event 4740 puts the caller computer's name in TargetDomainName (the Event Viewer shows it
        # as "Caller Computer Name").
        [pscustomobject]@{
            Time           = $entry.TimeCreated
            Account        = $data['TargetUserName']
            CallerComputer = $data['TargetDomainName']
            ReportedBy     = $pdc
        }
    }
}
