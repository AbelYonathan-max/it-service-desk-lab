function Get-LabImdsToken {
    <#
    .SYNOPSIS
    Gets an access token for the VM's managed identity from the Azure Instance Metadata Service.
    No secrets on disk: Azure gives the token only to code running on this VM.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$Resource = 'https://vault.azure.net')

    $uri = 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource={0}' -f [uri]::EscapeDataString($Resource)
    if ($env:SDLAB_IDENTITY_CLIENT_ID) { $uri += '&client_id={0}' -f $env:SDLAB_IDENTITY_CLIENT_ID }
    $cacheKey = $Resource + '|' + $env:SDLAB_IDENTITY_CLIENT_ID
    if (-not $script:ImdsTokens) { $script:ImdsTokens = @{} }
    $cached = $script:ImdsTokens[$cacheKey]
    if ($cached -and $cached.ExpiresOn -gt (Get-Date).AddMinutes(5)) { return $cached.Token }

    $response = Invoke-RestMethod -Uri $uri -Headers @{ Metadata = 'true' } -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
    $script:ImdsTokens[$cacheKey] = @{
        Token     = $response.access_token
        ExpiresOn = (Get-Date '1970-01-01Z').ToUniversalTime().AddSeconds([double]$response.expires_on).ToLocalTime()
    }
    $response.access_token
}

function Get-LabKeyVaultSecret {
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$VaultName,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $token = Get-LabImdsToken -Resource 'https://vault.azure.net'
    $uri = 'https://{0}.vault.azure.net/secrets/{1}?api-version=7.4' -f $VaultName, $Name
    (Invoke-RestMethod -Uri $uri -Headers @{ Authorization = "Bearer $token" } -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop).value
}

function Set-LabKeyVaultSecret {
    <#
    .SYNOPSIS
    Stores a secret in Key Vault with an expiry date, for example a starter's temporary password.
    #>
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)][string]$VaultName,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-zA-Z-]{1,127}$')][string]$Name,
        [Parameter(Mandatory = $true)][Security.SecureString]$Value,
        [ValidateRange(1, 365)][int]$ExpiresInDays = 7,
        [string]$ContentType = 'text/plain',
        [hashtable]$Tags = @{}
    )

    if (-not $PSCmdlet.ShouldProcess("$VaultName/$Name", 'Set secret')) { return }
    $token = Get-LabImdsToken -Resource 'https://vault.azure.net'
    $expires = [int64]((Get-Date).ToUniversalTime().AddDays($ExpiresInDays) - (Get-Date '1970-01-01Z').ToUniversalTime()).TotalSeconds
    $body = @{
        value       = ConvertFrom-LabSecureString -SecureString $Value
        contentType = $ContentType
        attributes  = @{ exp = $expires }
        tags        = $Tags
    } | ConvertTo-Json -Depth 4 -Compress
    $uri = 'https://{0}.vault.azure.net/secrets/{1}?api-version=7.4' -f $VaultName, $Name
    $null = Invoke-RestMethod -Method Put -Uri $uri -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) -UseBasicParsing -TimeoutSec 30 -ErrorAction Stop
    (Get-Date).AddDays($ExpiresInDays)
}

function ConvertTo-LabSecretName {
    <#
    .SYNOPSIS
    Makes a Key Vault secret name (letters, digits and hyphens only) from an account name.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][string]$SamAccountName
    )
    ('{0}-{1}' -f $Prefix, ($SamAccountName -replace '[^0-9a-zA-Z-]', '-')).ToLowerInvariant()
}
