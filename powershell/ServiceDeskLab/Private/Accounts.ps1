function Get-LabRandomNumber {
    <#
    .SYNOPSIS
    Returns a cryptographically random whole number from 0 up to (not including) -Maximum, with no
    modulo bias.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)][ValidateRange(1, 65536)][int]$Maximum)

    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $bytes = New-Object byte[] 4
        $limit = [uint32]::MaxValue - ([uint32]::MaxValue % [uint32]$Maximum)
        do {
            $generator.GetBytes($bytes)
            $value = [BitConverter]::ToUInt32($bytes, 0)
        } while ($value -ge $limit)
        [int]($value % [uint32]$Maximum)
    }
    finally { $generator.Dispose() }
}

function New-LabPassword {
    <#
    .SYNOPSIS
    Makes a temporary passphrase: three random words, two digits and a symbol, for example
    Lantern-Orbit-Meadow-47!. Long and easy to read out over the phone, and it meets the AD
    complexity rules. The user must change it at first sign-in.

    -AsSecureString returns it ready for the AD cmdlets, so the text never leaves this function.
    #>
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '',
        Justification = 'Only makes a value; changes nothing.')]
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingConvertToSecureStringWithPlainText', '',
        Justification = 'The passphrase is generated here, not typed, stored or logged.')]
    [CmdletBinding()]
    [OutputType([string], [Security.SecureString])]
    param(
        [ValidateRange(3, 6)][int]$Words = 3,
        [switch]$AsSecureString
    )

    $list = @(
        'Acorn', 'Amber', 'Anchor', 'Apple', 'Arrow', 'Aspen', 'Badger', 'Bakery', 'Bamboo', 'Banner',
        'Basket', 'Beacon', 'Berry', 'Bison', 'Blossom', 'Bramble', 'Breeze', 'Bridge', 'Brook', 'Bucket',
        'Cabin', 'Cactus', 'Candle', 'Canyon', 'Carbon', 'Castle', 'Cedar', 'Cherry', 'Cider', 'Clover',
        'Comet', 'Copper', 'Coral', 'Cotton', 'Crane', 'Crystal', 'Daisy', 'Dawn', 'Delta', 'Dolphin',
        'Dragon', 'Eagle', 'Echo', 'Ember', 'Falcon', 'Feather', 'Fern', 'Fiddle', 'Forest', 'Fossil',
        'Fox', 'Galaxy', 'Garden', 'Garnet', 'Ginger', 'Glacier', 'Granite', 'Harbour', 'Hazel', 'Heron',
        'Hollow', 'Honey', 'Island', 'Ivory', 'Jasmine', 'Jigsaw', 'Kettle', 'Kite', 'Lagoon', 'Lantern',
        'Lemon', 'Lilac', 'Linen', 'Maple', 'Marble', 'Meadow', 'Meteor', 'Mint', 'Monsoon', 'Mosaic',
        'Nectar', 'Nutmeg', 'Oak', 'Ocean', 'Olive', 'Orbit', 'Orchid', 'Otter', 'Paddle', 'Pebble',
        'Pepper', 'Pine', 'Planet', 'Poppy', 'Prism', 'Quartz', 'Quill', 'Rabbit', 'Raven', 'Ribbon',
        'River', 'Rocket', 'Saffron', 'Sail', 'Salmon', 'Sapphire', 'Shell', 'Silver', 'Sparrow', 'Spruce',
        'Stone', 'Summit', 'Sunrise', 'Thistle', 'Thunder', 'Tiger', 'Timber', 'Topaz', 'Tulip', 'Valley',
        'Velvet', 'Violet', 'Walnut', 'Willow', 'Window', 'Winter', 'Yarrow', 'Zephyr'
    )
    $symbols = '!', '?', '#', '%', '+', '='
    $chosen = for ($i = 0; $i -lt $Words; $i++) { $list[(Get-LabRandomNumber -Maximum $list.Count)] }
    $digits = '{0:D2}' -f (Get-LabRandomNumber -Maximum 100)
    $passphrase = '{0}-{1}{2}' -f ($chosen -join '-'), $digits, $symbols[(Get-LabRandomNumber -Maximum $symbols.Count)]
    if ($AsSecureString) { return ConvertTo-SecureString -String $passphrase -AsPlainText -Force }
    $passphrase
}

function New-LabAccountName {
    <#
    .SYNOPSIS
    Works out a unique sign-in name for a new starter, following the council's convention.

    .DESCRIPTION
    UPN and email: firstname.lastname, in plain ASCII. A clash gets a number: james.smith2.
    sAMAccountName (the pre-Windows 2000 name) is limited to 20 characters, so long names use the
    first initial (c.montgomery-welling), still numbered on a clash.

    -Exists is a script block that receives a candidate sAMAccountName and UPN prefix and returns
    $true if either is taken. The caller supplies it, so this function does not depend on AD and
    is easy to test.
    #>
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '',
        Justification = 'Only works out a name; changes nothing.')]
    [CmdletBinding()]
    [OutputType([pscustomobject])]
    param(
        [Parameter(Mandatory = $true)][string]$FirstName,
        [Parameter(Mandatory = $true)][string]$LastName,
        [Parameter(Mandatory = $true)][scriptblock]$Exists,
        [ValidateRange(8, 64)][int]$MaxLength = 20
    )

    $first = ConvertTo-LabAscii -Text $FirstName
    $last = ConvertTo-LabAscii -Text $LastName
    if (-not $first -or -not $last) {
        throw "Cannot make an account name from '$FirstName $LastName'. Check the spelling, or set the name by hand."
    }

    $upnBase = '{0}.{1}' -f $first, $last
    $samBase = $upnBase
    if ($samBase.Length -gt $MaxLength) { $samBase = '{0}.{1}' -f $first.Substring(0, 1), $last }

    for ($n = 1; $n -lt 100; $n++) {
        $suffix = if ($n -eq 1) { '' } else { [string]$n }
        $sam = $samBase
        if ($sam.Length + $suffix.Length -gt $MaxLength) { $sam = $sam.Substring(0, $MaxLength - $suffix.Length) }
        $sam = $sam.TrimEnd('.', '-') + $suffix
        $upn = $upnBase + $suffix
        if (-not (& $Exists $sam $upn)) {
            return [pscustomobject]@{ SamAccountName = $sam; UpnPrefix = $upn }
        }
    }
    throw "Could not find a free account name for '$FirstName $LastName' after 99 tries."
}

function Resolve-LabUser {
    <#
    .SYNOPSIS
    Finds exactly one AD user from a sAMAccountName, UPN, email, employee ID or distinguished name.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Identity,
        [string[]]$Properties = @()
    )

    $value = ConvertTo-LabLdapFilterValue -Value $Identity.Trim()
    $filter = if ($Identity -match '^(CN|OU)=') {
        '(distinguishedName={0})' -f $value
    }
    else {
        '(|(sAMAccountName={0})(userPrincipalName={0})(mail={0})(employeeID={0}))' -f $value
    }
    $wanted = @('EmployeeID', 'Department', 'Title', 'Manager', 'MemberOf', 'Enabled', 'LockedOut') + $Properties |
        Select-Object -Unique
    $found = @(Get-ADUser -LDAPFilter "(&(objectCategory=person)(objectClass=user)$filter)" -Properties $wanted)
    if ($found.Count -eq 0) { throw "No user matches '$Identity'." }
    if ($found.Count -gt 1) {
        throw "'$Identity' matches $($found.Count) users ($(($found.SamAccountName) -join ', ')). Use the sAMAccountName or employee ID."
    }
    $found[0]
}

function Resolve-LabDepartment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Department,
        [Parameter(Mandatory = $true)]$Config
    )

    $match = @($Config.Departments | Where-Object {
            $_.Code -eq $Department -or $_.Name -eq $Department -or $_.Key -eq $Department
        })
    if ($match.Count -ne 1) {
        throw "Unknown department '$Department'. Use one of: $(($Config.Departments.Code) -join ', ')."
    }
    $match[0]
}

function Resolve-LabShare {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$Share,
        [Parameter(Mandatory = $true)]$Config
    )

    $match = @($Config.Shares | Where-Object { $_.Name -eq $Share })
    if ($match.Count -ne 1) {
        throw "Unknown shared drive '$Share'. Use one of: $(($Config.Shares.Name) -join ', ')."
    }
    $match[0]
}

function Get-LabGroupName {
    <#
    .SYNOPSIS
    Returns the names of the groups a user is a direct member of (from the MemberOf DNs).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([AllowNull()][AllowEmptyCollection()][string[]]$MemberOf)

    foreach ($dn in @($MemberOf)) {
        if ($dn -match '^CN=((?:\\.|[^,])+),') { $Matches[1] -replace '\\(.)', '$1' }
    }
}
