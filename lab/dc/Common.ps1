# Helpers shared by the domain controller build stages. Dot-source it from a stage script.
# Stages run as SYSTEM through Azure Run Command, so they log plainly to standard output, which
# the GitHub Actions workflow shows. Every stage is safe to run again.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$script:RepoRoot = Split-Path (Split-Path $PSScriptRoot)
$env:SDLAB_CONFIG = Join-Path $script:RepoRoot 'lab\config\org.json'

function Write-Step {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Output ('[{0:HH:mm:ss}] {1}' -f (Get-Date), $Message)
}

function Get-VaultSecret {
    # Reads a Key Vault secret with the VM's managed identity. Returns $null if it does not exist.
    param(
        [Parameter(Mandatory = $true)][string]$VaultName,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $tokenUri = 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fvault.azure.net'
    $token = (Invoke-RestMethod -Uri $tokenUri -Headers @{ Metadata = 'true' } -UseBasicParsing).access_token
    try {
        (Invoke-RestMethod -Uri ('https://{0}.vault.azure.net/secrets/{1}?api-version=7.4' -f $VaultName, $Name) `
                -Headers @{ Authorization = "Bearer $token" } -UseBasicParsing).value
    }
    catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) { return $null }
        throw
    }
}

function Wait-Until {
    # Retries a script block until it returns something truthy, or gives up after -Minutes.
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Condition,
        [Parameter(Mandatory = $true)][string]$What,
        [int]$Minutes = 15
    )
    $deadline = (Get-Date).AddMinutes($Minutes)
    while ($true) {
        try {
            $result = & $Condition
            if ($result) { return $result }
        }
        catch {
            $last = $_.Exception.Message
        }
        if ((Get-Date) -gt $deadline) { throw "Gave up waiting for $What after $Minutes minutes. Last error: $last" }
        Start-Sleep -Seconds 15
    }
}

function Import-LabModule {
    Import-Module (Join-Path $script:RepoRoot 'powershell\ServiceDeskLab\ServiceDeskLab.psd1') -Force
}
