function Enable-LabDueStarter {
    <#
    .SYNOPSIS
    Enables future starters' accounts when their start date arrives.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    [OutputType([pscustomobject])]
    param([datetime]$Today = (Get-Date).Date)

    $config = Get-LabConfig
    $pending = @(Get-ADUser -SearchBase $config.Ou.Staff -SearchScope Subtree `
            -LDAPFilter '(msDS-cloudExtensionAttribute2=PendingStart)' -Properties 'msDS-cloudExtensionAttribute1', Description)
    foreach ($user in $pending) {
        $start = [datetime]::MinValue
        $ok = [datetime]::TryParseExact([string]$user.'msDS-cloudExtensionAttribute1', 'yyyy-MM-dd',
            [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$start)
        if (-not $ok -or $start -gt $Today) { continue }
        if ($PSCmdlet.ShouldProcess($user.SamAccountName, 'Enable on start date')) {
            Enable-ADAccount -Identity $user.DistinguishedName
            Set-ADUser -Identity $user.DistinguishedName -Clear 'msDS-cloudExtensionAttribute2' `
                -Description ([string]$user.Description -replace '\bStarts\b', 'Joined')
            Write-LabAudit -Action 'StarterEnabled' -Target $user.SamAccountName -Detail @{ startDate = $start.ToString('yyyy-MM-dd') }
            [pscustomobject]@{ SamAccountName = $user.SamAccountName; StartDate = $start }
        }
    }
}

function Initialize-LabHomeFolder {
    <#
    .SYNOPSIS
    Creates a user's home folder (H: drive) with access for that user only.

    .DESCRIPTION
    Runs on the file server. Inheritance from the Home root gives administrators and SYSTEM full
    control; the user gets Modify on their own folder and nothing on anyone else's. Skipped when
    the home root does not exist on this machine.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([Parameter(Mandatory = $true)][string]$SamAccountName)

    $config = Get-LabConfig
    $root = $config.FileServer.homeRoot
    if (-not $root -or -not (Test-Path -LiteralPath $root)) { return $null }
    $path = Join-Path $root $SamAccountName
    if (-not $PSCmdlet.ShouldProcess($path, 'Create home folder')) { return $null }

    if (-not (Test-Path -LiteralPath $path)) { New-Item -ItemType Directory -Path $path | Out-Null }
    $acl = Get-Acl -LiteralPath $path
    $rule = New-Object Security.AccessControl.FileSystemAccessRule(
        ('{0}\{1}' -f $config.NetbiosName, $SamAccountName), 'Modify', 'ContainerInherit, ObjectInherit', 'None', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $path -AclObject $acl
    $path
}
