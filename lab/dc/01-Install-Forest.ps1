# Stage 1: turns the fresh Windows Server VM into the first domain controller of a new forest.
#
# - Formats the data disk as F: (host caching is off on that disk, as Microsoft advises for the
#   AD database, so writes are not lost if the host fails).
# - Installs AD DS and DNS, then creates corp.kestrel.internal with the database, logs and
#   SYSVOL on F:.
# - The Directory Services Restore Mode password comes from Key Vault (Terraform made it).
# - Restarts the server 30 seconds after finishing; the workflow waits for AD to come up.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingConvertToSecureStringWithPlainText', '',
    Justification = 'The DSRM password comes from Key Vault, which only returns it as text.')]
param([Parameter(Mandatory = $true)][string]$KeyVault)
. (Join-Path $PSScriptRoot 'Common.ps1')

$role = (Get-CimInstance Win32_ComputerSystem).DomainRole
if ($role -ge 4) {
    Write-Step "Already a domain controller (role $role). Nothing to do."
    return
}

Write-Step 'Growing C: to fill the OS disk'
# The small-disk image has a 30 GB C:; Terraform gives the disk more room than that.
try {
    $largest = (Get-PartitionSupportedSize -DriveLetter C).SizeMax
    if ($largest - (Get-Partition -DriveLetter C).Size -gt 1GB) { Resize-Partition -DriveLetter C -Size $largest }
}
catch {
    Write-Step "Could not grow C: ($($_.Exception.Message)); carrying on with its current size."
}

Write-Step 'Preparing the data disk'
$raw = Get-Disk | Where-Object PartitionStyle -EQ 'RAW' | Sort-Object Number | Select-Object -First 1
if ($raw) {
    Initialize-Disk -Number $raw.Number -PartitionStyle GPT
    New-Partition -DiskNumber $raw.Number -UseMaximumSize -DriveLetter F |
        Format-Volume -FileSystem NTFS -NewFileSystemLabel 'Data' -Confirm:$false | Out-Null
}
if (-not (Test-Path 'F:\')) { throw 'The data disk did not come up as F:.' }

Write-Step 'Installing AD DS, DNS and the management tools'
Install-WindowsFeature -Name AD-Domain-Services, DNS, GPMC, RSAT-AD-Tools, RSAT-DNS-Server -IncludeManagementTools | Out-Null

$dsrm = Get-VaultSecret -VaultName $KeyVault -Name 'dsrm-password'
if (-not $dsrm) { throw "Key Vault $KeyVault has no dsrm-password secret. Run the Terraform deploy first." }
$secure = ConvertTo-SecureString -String $dsrm -AsPlainText -Force
$dsrm = $null

$config = Get-Content $env:SDLAB_CONFIG -Raw | ConvertFrom-Json
Write-Step "Creating the forest $($config.domain.dnsName) ($($config.domain.netbiosName))"
Import-Module ADDSDeployment
Install-ADDSForest -DomainName $config.domain.dnsName -DomainNetbiosName $config.domain.netbiosName `
    -ForestMode WinThreshold -DomainMode WinThreshold -InstallDns `
    -DatabasePath 'F:\NTDS' -LogPath 'F:\NTDS' -SysvolPath 'F:\SYSVOL' `
    -SafeModeAdministratorPassword $secure -NoRebootOnCompletion -Force -WarningAction SilentlyContinue | Out-Null

Write-Step 'Promotion finished. Restarting in 30 seconds.'
& shutdown.exe /r /t 30 /c 'Restarting to finish promotion to domain controller' /d p:4:2
