function ConvertTo-LabAscii {
    <#
    .SYNOPSIS
    Turns a name into plain lower-case ASCII for account names: accents dropped (Zoe, Jose),
    apostrophes and spaces removed (obrien, garciafernandez), hyphens kept.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)

    $decomposed = $Text.Normalize([Text.NormalizationForm]::FormD)
    $builder = New-Object System.Text.StringBuilder
    foreach ($character in $decomposed.ToCharArray()) {
        $category = [Globalization.CharUnicodeInfo]::GetUnicodeCategory($character)
        if ($category -ne [Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$builder.Append($character)
        }
    }
    $plain = $builder.ToString().ToLowerInvariant()
    # Letters with no decomposed form, mapped by hand. Code points keep this file ASCII, which
    # Windows PowerShell 5.1 needs to read it correctly without a byte order mark.
    $map = @{
        ([char]0x00F8) = 'o'   # o with stroke
        ([char]0x00E6) = 'ae'  # ae ligature
        ([char]0x0153) = 'oe'  # oe ligature
        ([char]0x00DF) = 'ss'  # sharp s
        ([char]0x0142) = 'l'   # l with stroke
        ([char]0x0111) = 'd'   # d with stroke
        ([char]0x00F0) = 'd'   # eth
        ([char]0x00FE) = 'th'  # thorn
    }
    foreach ($key in $map.Keys) { $plain = $plain.Replace([string]$key, $map[$key]) }
    $plain = $plain -replace '[^a-z0-9-]', ''
    $plain.Trim('-')
}

function ConvertTo-LabLdapFilterValue {
    <#
    .SYNOPSIS
    Escapes a value for an LDAP filter (RFC 4515), so names such as O'Brien or values with
    brackets cannot change the meaning of the filter.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    $builder = New-Object System.Text.StringBuilder
    foreach ($character in $Value.ToCharArray()) {
        switch ($character) {
            '\' { [void]$builder.Append('\5c') }
            '*' { [void]$builder.Append('\2a') }
            '(' { [void]$builder.Append('\28') }
            ')' { [void]$builder.Append('\29') }
            ([char]0) { [void]$builder.Append('\00') }
            default { [void]$builder.Append($character) }
        }
    }
    $builder.ToString()
}

function ConvertTo-LabDnValue {
    <#
    .SYNOPSIS
    Escapes a value for use inside a distinguished name (RFC 4514), for OU and CN names.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$Value)

    $escaped = $Value -replace '([,+"\\<>;=])', '\$1'
    if ($escaped.StartsWith('#') -or $escaped.StartsWith(' ')) { $escaped = '\' + $escaped }
    if ($escaped.EndsWith(' ')) { $escaped = $escaped.Substring(0, $escaped.Length - 1) + '\ ' }
    $escaped
}

function ConvertFrom-LabSecureString {
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][Security.SecureString]$SecureString)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
