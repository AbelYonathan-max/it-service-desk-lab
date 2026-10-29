# Stage 6: gets the domain controller ready for Microsoft Entra Cloud Sync, which copies staff
# accounts (and password hashes) from AD to Entra ID so people use one password for both.
#
# The Cloud Sync agent has to be registered by a person: it signs in to Entra ID interactively, with
# MFA, as a Hybrid Identity Administrator. This stage does everything around that:
# - turns on TLS 1.2 for .NET (a Cloud Sync prerequisite);
# - puts "Connect to Microsoft Entra ID.txt" on the desktop with the exact steps, the OU to sync and
#   the account to use;
# - makes sure the domain's administrator signs in with the password in Key Vault
#   (dc01-admin-password), which Bastion and the agent's wizard both use, and is not locked out;
# - reports whether the agent is already installed and running.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingConvertToSecureStringWithPlainText', '',
    Justification = 'The administrator password comes from Key Vault, which only returns it as text.')]
param([string]$KeyVault = '')
. (Join-Path $PSScriptRoot 'Common.ps1')
Import-Module ActiveDirectory
Import-LabModule
$config = Get-LabConfig -Force
$domain = Get-ADDomain

Write-Step 'TLS 1.2 for .NET and Schannel'
foreach ($key in 'HKLM:\SOFTWARE\Microsoft\.NETFramework\v4.0.30319', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\.NETFramework\v4.0.30319') {
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name 'SchUseStrongCrypto' -Value 1 -Type DWord
    Set-ItemProperty -Path $key -Name 'SystemDefaultTlsVersions' -Value 1 -Type DWord
}
foreach ($side in 'Client', 'Server') {
    $key = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\TLS 1.2\$side"
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name 'Enabled' -Value 1 -Type DWord
    Set-ItemProperty -Path $key -Name 'DisabledByDefault' -Value 0 -Type DWord
}

Write-Step 'Administrator password'
$adminUser = Get-ADUser -Filter "objectSid -eq '$($domain.DomainSID.Value)-500'" -Properties LockedOut
$admin = $adminUser.SamAccountName
if ($adminUser.LockedOut) {
    Unlock-ADAccount -Identity $adminUser
    Write-Step "$admin was locked out: unlocked."
}
if ($KeyVault) {
    $password = Get-VaultSecret -VaultName $KeyVault -Name 'dc01-admin-password'
    if (-not $password) { throw "Key Vault $KeyVault has no dc01-admin-password secret." }
    Add-Type -AssemblyName System.DirectoryServices.AccountManagement
    $context = New-Object System.DirectoryServices.AccountManagement.PrincipalContext('Domain', $domain.DNSRoot)
    if ($context.ValidateCredentials($admin, $password, 'Negotiate')) {
        Write-Step "$admin signs in with the Key Vault password."
    }
    else {
        Set-ADAccountPassword -Identity $adminUser -Reset -NewPassword (ConvertTo-SecureString -String $password -AsPlainText -Force)
        Unlock-ADAccount -Identity $adminUser
        if (-not $context.ValidateCredentials($admin, $password, 'Negotiate')) {
            throw "$admin still does not sign in with the Key Vault password after a reset."
        }
        Write-Step "$admin did not sign in with the Key Vault password: reset it to that password."
    }
    $context.Dispose()
    $password = $null
}

Write-Step 'Instructions on the desktop'
$steps = @"
CONNECT THIS DOMAIN TO MICROSOFT ENTRA ID (about 10 minutes, once)

You need: the lab admin work account (lab.admin@...) and its MFA, and this domain's administrator
password: Key Vault secret "dc01-admin-password" (Azure portal > Key vaults > kv-sdlab-... >
Secrets). Copy and paste work in Bastion.

1. Open Microsoft Edge on this server and go to https://entra.microsoft.com
   Sign in as lab.admin@... (your work account, not your personal one).
2. Entra ID > Entra Connect > Cloud sync > Manage > Download provisioning agent >
   Accept terms and download. Run the installer it downloads.
3. In the wizard:
   - Sign in again as lab.admin@...
   - Configure service account: "Create gMSA", with $($domain.NetBIOSName)\$admin and the password above.
   - Connect your Active Directory: Add directory > $($domain.DNSRoot), same account.
   - Confirm. The agent shows as Active in the portal within a minute or two.
4. Back in the portal: Cloud sync > Configurations > New configuration > AD to Microsoft Entra ID
   sync > choose $($domain.DNSRoot) > Create.
   - Scoping filters > Selected organizational units > add both:
       $($config.Ou.Staff)
       $($config.Ou.LicenceGroups)
     (The second brings the licence group. Leavers and admin accounts stay out of the cloud.)
   - Password hash sync stays on.
   - Overview > Enable configuration > Save.
5. Users appear in Entra ID within about 2 to 5 minutes, with the same passwords they use in AD.
   Then run the "Entra and Intune" workflow in GitHub with "apply": it gives the licence group
   Microsoft 365 Business Premium (group-based licensing) and checks the Intune policies.

Run the GitHub "Lab" workflow with action "doctor" to check the agent from here.
"@
$steps | Set-Content -Path 'C:\Users\Public\Desktop\Connect to Microsoft Entra ID.txt' -Encoding UTF8

$agent = Get-Service -Name AADConnectProvisioningAgent -ErrorAction SilentlyContinue
if ($agent) {
    Write-Step "Cloud Sync agent installed: $($agent.Status)."
}
else {
    Write-Step 'Cloud Sync agent not installed yet: follow the desktop note from a Bastion session.'
}
