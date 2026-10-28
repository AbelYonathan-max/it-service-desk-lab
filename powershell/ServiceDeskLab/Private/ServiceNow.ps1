function Get-LabServiceNowConnection {
    <#
    .SYNOPSIS
    Builds the ServiceNow connection from Key Vault (on the lab server) or environment variables
    (for testing).

    .DESCRIPTION
    On the domain controller, the instance name and the integration user's credentials live in
    Key Vault (secrets servicenow-instance, servicenow-username, servicenow-password) and are read
    with the VM's managed identity. SDLAB_SN_INSTANCE, SDLAB_SN_USERNAME and SDLAB_SN_PASSWORD
    override that.
    #>
    [CmdletBinding()]
    param([switch]$Force)

    if ($script:SnConnection -and -not $Force) { return $script:SnConnection }

    $instance = $env:SDLAB_SN_INSTANCE
    $user = $env:SDLAB_SN_USERNAME
    $password = $env:SDLAB_SN_PASSWORD
    if (-not ($instance -and $user -and $password)) {
        if (-not $env:SDLAB_KEYVAULT) {
            throw 'No ServiceNow connection. Set SDLAB_KEYVAULT (the lab Key Vault) or SDLAB_SN_INSTANCE, SDLAB_SN_USERNAME and SDLAB_SN_PASSWORD.'
        }
        $instance = Get-LabKeyVaultSecret -VaultName $env:SDLAB_KEYVAULT -Name 'servicenow-instance'
        $user = Get-LabKeyVaultSecret -VaultName $env:SDLAB_KEYVAULT -Name 'servicenow-username'
        $password = Get-LabKeyVaultSecret -VaultName $env:SDLAB_KEYVAULT -Name 'servicenow-password'
    }

    $base = if ($instance -match '^https://') { $instance.TrimEnd('/') } else { 'https://{0}.service-now.com' -f $instance }
    $pair = '{0}:{1}' -f $user, $password
    $script:SnConnection = [pscustomobject]@{
        BaseUri    = $base
        UserName   = $user
        AuthHeader = 'Basic ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($pair))
    }
    $script:SnConnection
}

function Invoke-LabSnApi {
    <#
    .SYNOPSIS
    Calls the ServiceNow REST API and returns the "result" part of the reply.

    .DESCRIPTION
    Retries on 429 (rate limited) and 5xx with exponential back-off. A sleeping developer instance
    answers with a web page instead of JSON; that becomes a clear error instead of a parse failure.
    #>
    [CmdletBinding()]
    param(
        [ValidateSet('GET', 'POST', 'PATCH', 'PUT', 'DELETE')][string]$Method = 'GET',
        [Parameter(Mandatory = $true)][string]$Path,
        [hashtable]$Query = @{},
        $Body,
        [ValidateRange(1, 6)][int]$MaxAttempts = 4
    )

    $connection = Get-LabServiceNowConnection
    $uri = $connection.BaseUri + $Path
    if ($Query.Count) {
        $pairs = foreach ($key in ($Query.Keys | Sort-Object)) {
            '{0}={1}' -f [uri]::EscapeDataString([string]$key), [uri]::EscapeDataString([string]$Query[$key])
        }
        $uri += '?' + ($pairs -join '&')
    }
    $request = @{
        Method          = $Method
        Uri             = $uri
        Headers         = @{ Authorization = $connection.AuthHeader; Accept = 'application/json' }
        UseBasicParsing = $true
        TimeoutSec      = 60
        ErrorAction     = 'Stop'
    }
    if ($null -ne $Body) {
        $request.ContentType = 'application/json; charset=utf-8'
        $request.Body = [Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Depth 6 -Compress))
    }

    for ($attempt = 1; ; $attempt++) {
        try {
            $response = Invoke-RestMethod @request
        }
        catch {
            $status = 0
            if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
            if ($attempt -lt $MaxAttempts -and ($status -eq 429 -or $status -ge 500)) {
                Start-Sleep -Seconds ([math]::Pow(2, $attempt))
                continue
            }
            throw
        }
        if ($response -is [string]) {
            if ($response -match 'hibernat') {
                throw 'The ServiceNow developer instance is asleep. Wake it at https://developer.servicenow.com, then try again.'
            }
            throw "ServiceNow answered $Method $Path with a web page instead of JSON."
        }
        if ($null -eq $response) { return $null }
        return $response.result
    }
}

function Get-LabSnRequestVariable {
    <#
    .SYNOPSIS
    Returns a requested item's catalogue variables as a hashtable of name to value.
    #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param([Parameter(Mandatory = $true)][string]$RequestItemSysId)

    $rows = @(Invoke-LabSnApi -Path '/api/now/table/sc_item_option_mtom' -Query @{
            sysparm_query                  = "request_item=$RequestItemSysId"
            sysparm_fields                 = 'sc_item_option.item_option_new.name,sc_item_option.value'
            sysparm_exclude_reference_link = 'true'
            sysparm_limit                  = '200'
        })
    $variables = @{}
    foreach ($row in $rows) {
        $name = $row.'sc_item_option.item_option_new.name'
        if ($name) { $variables[$name] = [string]$row.'sc_item_option.value' }
    }
    $variables
}

function Get-LabSnUserName {
    <#
    .SYNOPSIS
    Turns a ServiceNow sys_user sys_id (a reference variable's value) into the AD sign-in name.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$SysId)

    if (-not $SysId) { return $null }
    $user = Invoke-LabSnApi -Path "/api/now/table/sys_user/$SysId" -Query @{ sysparm_fields = 'user_name' }
    if (-not $user) { throw "ServiceNow user $SysId not found." }
    $user.user_name
}

function Update-LabSnTask {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '',
        Justification = 'Private; Invoke-LabFulfilment checks ShouldProcess before any task is touched.')]
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$SysId,
        [hashtable]$Fields = @{},
        [switch]$ByDisplayValue
    )

    $query = @{ sysparm_fields = 'sys_id,number,state' }
    if ($ByDisplayValue) { $query.sysparm_input_display_value = 'true' }
    Invoke-LabSnApi -Method PATCH -Path "/api/now/table/sc_task/$SysId" -Query $query -Body $Fields
}

function ConvertFrom-LabSnDate {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Value)

    if (-not $Value) { return $null }
    [datetime]::ParseExact($Value.Substring(0, 10), 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
}
