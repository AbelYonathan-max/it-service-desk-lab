function Get-LabConfig {
    <#
    .SYNOPSIS
    Loads the organisation settings (domain, OUs, departments, shares) from org.json.

    .DESCRIPTION
    Looks for the file in this order: the -Path parameter, the SDLAB_CONFIG environment variable,
    the copy installed with the module, then the repository's lab/config folder. Distinguished
    names are worked out from the domain name, so the same file works in any domain.

    The UPN suffix comes from the SDLAB_UPN_SUFFIX environment variable when it is set (the lab
    uses the Entra tenant's onmicrosoft.com domain, so synced accounts can sign in to Microsoft
    365), otherwise from the AD DNS name.

    .EXAMPLE
    (Get-LabConfig).Departments | Format-Table Code, Name, Group, OuDn
    #>
    [CmdletBinding()]
    param(
        [string]$Path,
        [switch]$Force
    )

    if (-not $Path) { $Path = $env:SDLAB_CONFIG }
    if (-not $Path) {
        $installed = Join-Path (Join-Path $script:ModuleRoot 'config') 'org.json'
        $repo = Join-Path (Join-Path (Join-Path (Split-Path (Split-Path $script:ModuleRoot)) 'lab') 'config') 'org.json'
        foreach ($candidate in $installed, $repo) {
            if (Test-Path -LiteralPath $candidate) { $Path = $candidate; break }
        }
    }
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        throw 'Cannot find org.json. Pass -Path or set the SDLAB_CONFIG environment variable.'
    }

    $resolved = (Resolve-Path -LiteralPath $Path).ProviderPath
    if (-not $Force -and $script:LabConfig -and $script:LabConfigPath -eq $resolved -and
        $script:LabConfigUpn -eq $env:SDLAB_UPN_SUFFIX) {
        return $script:LabConfig
    }

    $raw = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8 | ConvertFrom-Json

    $domainDn = ($raw.domain.dnsName.Split('.') | ForEach-Object { "DC=$_" }) -join ','
    $rootDn = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.domain.rootOu), $domainDn
    $groupsDn = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.groups), $rootDn
    $devicesDn = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.devices), $rootDn
    $ou = [ordered]@{
        Root            = $rootDn
        Staff           = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.staff), $rootDn
        Leavers         = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.leavers), $rootDn
        Groups          = $groupsDn
        DepartmentGroups = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.departmentGroups), $groupsDn
        ResourceGroups  = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.resourceGroups), $groupsDn
        RoleGroups      = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.roleGroups), $groupsDn
        LicenceGroups   = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.licenceGroups), $groupsDn
        Devices         = $devicesDn
        Workstations    = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.workstations), $devicesDn
        Servers         = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.servers), $devicesDn
        ServiceAccounts = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.serviceAccounts), $rootDn
        Admin           = 'OU={0},{1}' -f (ConvertTo-LabDnValue $raw.ous.admin), $rootDn
    }

    $departments = foreach ($d in $raw.departments) {
        [pscustomobject]@{
            Code  = $d.code
            Name  = $d.name
            Key   = $d.key
            Share = $d.share
            Group = "GG-Dept-$($d.key)"
            OuDn  = 'OU={0},{1}' -f (ConvertTo-LabDnValue $d.name), $ou.Staff
        }
    }

    $shares = foreach ($s in $raw.shares) {
        [pscustomobject]@{
            Name            = $s.name
            Description     = $s.description
            Readers         = @($s.readers)
            Editors         = @($s.editors)
            Restricted      = [bool]$s.restricted
            OwnerEmployeeId = $s.ownerEmployeeId
            ReadGroup       = "DL-FS-$($s.name)-RO"
            ModifyGroup     = "DL-FS-$($s.name)-RW"
            # Plain text, not Join-Path: Join-Path fails when the drive does not exist on this machine.
            Path            = '{0}\{1}' -f $raw.fileServer.sharesRoot.TrimEnd('\'), $s.name
        }
    }

    $upnSuffix = $raw.domain.dnsName
    if ($env:SDLAB_UPN_SUFFIX) { $upnSuffix = $env:SDLAB_UPN_SUFFIX }

    $config = [pscustomobject]@{
        Path                = $resolved
        Organisation        = $raw.organisation
        DnsName             = $raw.domain.dnsName
        NetbiosName         = $raw.domain.netbiosName
        DomainDn            = $domainDn
        UpnSuffix           = $upnSuffix
        Ou                  = [pscustomobject]$ou
        Groups              = $raw.groups
        Departments         = @($departments)
        Shares              = @($shares)
        FileServer          = $raw.fileServer
        HomeShare           = $env:SDLAB_HOME_SHARE
        MaxSamLength        = [int]$raw.naming.maxSamAccountNameLength
        LeaverRetentionDays = [int]$raw.leaverRetentionDays
        StaleAccountDays    = [int]$raw.staleAccountDays
        ServiceNow          = $raw.serviceNow
    }

    $script:LabConfig = $config
    $script:LabConfigPath = $resolved
    $script:LabConfigUpn = $env:SDLAB_UPN_SUFFIX
    $config
}
