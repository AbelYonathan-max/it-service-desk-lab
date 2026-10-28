# Health check of the lab's domain controller, for the lab workflow's "doctor" action and for
# troubleshooting from a Bastion session. Read-only; prints a short report.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

function Write-Section([string]$Title) { "`n== $Title" }

Write-Section 'Server'
$os = Get-CimInstance Win32_OperatingSystem
$uptime = (Get-Date) - $os.LastBootUpTime
'{0} ({1}), up {2} days {3} hours, time zone {4}' -f $os.Caption, $os.Version, $uptime.Days, $uptime.Hours, (Get-TimeZone).Id
foreach ($volume in Get-Volume | Where-Object { $_.DriveLetter -in 'C', 'F' }) {
    '{0}: {1:N1} GB free of {2:N1} GB' -f $volume.DriveLetter, ($volume.SizeRemaining / 1GB), ($volume.Size / 1GB)
}

Write-Section 'Active Directory'
$domain = $null
try {
    Import-Module ActiveDirectory -ErrorAction Stop
    $domain = Get-ADDomain
    'Domain {0} ({1}), domain mode {2}, PDC emulator {3}' -f $domain.DNSRoot, $domain.NetBIOSName, $domain.DomainMode, $domain.PDCEmulator
    $users = @(Get-ADUser -SearchBase "OU=Kestrel,$($domain.DistinguishedName)" -LDAPFilter '(employeeID=*)' -Properties LockedOut)
    'Staff accounts: {0} ({1} enabled, {2} locked out)' -f $users.Count, @($users | Where-Object Enabled).Count, @($users | Where-Object LockedOut).Count
    'Recycle Bin: {0}' -f [bool](Get-ADOptionalFeature -Filter "Name -eq 'Recycle Bin Feature'").EnabledScopes
}
catch {
    "Not available: $($_.Exception.Message)"
}

Write-Section 'dcdiag (problems only)'
$diag = @(& dcdiag.exe /q 2>&1)
if ($diag.Count) { $diag | Select-Object -First 25 } else { 'No problems found.' }

Write-Section 'DNS'
if ($domain) {
    $srv = @(Resolve-DnsName -Name "_ldap._tcp.dc._msdcs.$($domain.DNSRoot)" -Type SRV -ErrorAction SilentlyContinue)
    'Domain controller SRV records: {0}' -f $srv.Count
}
$web = Resolve-DnsName -Name 'login.microsoftonline.com' -ErrorAction SilentlyContinue | Select-Object -First 1
'Internet names through the forwarder: {0}' -f $(if ($web) { 'working' } else { 'NOT working' })

Write-Section 'File shares'
Get-SmbShare | Where-Object { -not $_.Special -and $_.Name -notin 'NETLOGON', 'SYSVOL' } |
    ForEach-Object { '{0} -> {1}' -f $_.Name, $_.Path }

Write-Section 'Fulfilment agent'
$tasks = @(Get-ScheduledTask -TaskPath '\ServiceDeskLab\' -ErrorAction SilentlyContinue)
if (-not $tasks.Count) { 'Not installed yet (stage 05).' }
foreach ($task in $tasks) {
    $info = $task | Get-ScheduledTaskInfo
    # 0x41303 (267011) is Task Scheduler's "has not run yet"; its last run time is then a 1999 placeholder.
    $last = if ($info.LastTaskResult -eq 267011) { 'not run yet' } else { 'last run {0:yyyy-MM-dd HH:mm} (result {1})' -f $info.LastRunTime, $info.LastTaskResult }
    '{0}: {1}, {2}, next {3:yyyy-MM-dd HH:mm}' -f $task.TaskName, $task.State, $last, $info.NextRunTime
}
$log = Get-ChildItem 'C:\ProgramData\ServiceDeskLab\logs' -Filter 'fulfilment-*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($log) {
    "Last lines of $($log.Name):"
    Get-Content $log.FullName -Tail 12
}

Write-Section 'Microsoft Entra Cloud Sync agent'
$agent = Get-Service -Name AADConnectProvisioningAgent -ErrorAction SilentlyContinue
if ($agent) { 'Provisioning agent: {0}' -f $agent.Status } else { 'Not installed yet.' }
