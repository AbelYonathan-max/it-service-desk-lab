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
$logs = 'C:\ProgramData\ServiceDeskLab\logs'
$log = Get-ChildItem $logs -Filter 'fulfilment-*.log' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($log) {
    # Every run's transcript opens and closes with a block about PowerShell itself; only the rest is news.
    $boilerplate = '^(\*+$|Windows PowerShell transcript (start|end)|(Start|End) time:|Username:|RunAs User:|Configuration Name:|Machine:|Host Application:|Process ID:|PS\w+:|BuildVersion:|CLRVersion:|WSManStackVersion:|SerializationVersion:)'
    $printed = @(Get-Content $log.FullName | Where-Object { $_.Trim() -and $_ -notmatch $boilerplate })
    "What the runs in $($log.Name) printed (last lines):"
    if ($printed.Count) { $printed | Select-Object -Last 12 } else { 'Nothing: no approved task has been waiting today.' }
}
$audit = Get-ChildItem $logs -Filter 'audit-*.jsonl' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($audit) {
    "Last entries in $($audit.Name):"
    foreach ($line in Get-Content $audit.FullName -Tail 8) {
        $entry = $line | ConvertFrom-Json
        $problem = if ($entry.detail.error) { ": $($entry.detail.error)" } else { '' }
        '{0} {1} {2} ({3}){4}' -f $entry.time, $entry.action, $entry.target, $entry.reference, $problem
    }
}

Write-Section 'Microsoft Entra Cloud Sync agent'
$agent = Get-Service -Name AADConnectProvisioningAgent -ErrorAction SilentlyContinue
if ($agent) { 'Provisioning agent: {0}' -f $agent.Status } else { 'Not installed yet.' }

# Bastion only says "Log in failed"; the Security log says why. This report can be public, so the
# names people typed are never printed, only whether they were the administrator's.
Write-Section 'Administrator sign-in'
$admin = $null
if ($domain) {
    $admin = Get-ADUser -Filter "objectSid -eq '$($domain.DomainSID.Value)-500'" -Properties LockedOut, badPwdCount, PasswordLastSet
    '{0}: enabled {1}, locked out {2}, wrong passwords since the last right one {3}, password set {4:yyyy-MM-dd HH:mm}' -f `
        $admin.SamAccountName, $admin.Enabled, $admin.LockedOut, $admin.badPwdCount, $admin.PasswordLastSet
    $xpath = "*[System[EventID=4624 and TimeCreated[timediff(@SystemTime) <= 10800000]] and " +
        "EventData[Data[@Name='TargetUserName']='$($admin.SamAccountName)' and Data[@Name='LogonType']='10']]"
    $signIns = @(Get-WinEvent -LogName Security -FilterXPath $xpath -MaxEvents 5 -ErrorAction SilentlyContinue)
    if ($signIns.Count) { 'Last remote desktop sign-in: {0:yyyy-MM-dd HH:mm:ss}' -f $signIns[0].TimeCreated }
    else { 'Remote desktop sign-ins in the last 3 hours: none' }
}
$reasons = @{
    '0xc0000064' = 'no account has that name'
    '0xc000006a' = 'wrong password'
    '0xc000006f' = 'outside the hours it may sign in'
    '0xc0000070' = 'not allowed from that computer'
    '0xc0000071' = 'password expired'
    '0xc0000072' = 'account disabled'
    '0xc0000193' = 'account expired'
    '0xc0000224' = 'password must be changed first'
    '0xc0000234' = 'account locked out'
    '0xc000015b' = 'not allowed to sign in that way'
}
$refused = @(Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 4625; StartTime = (Get-Date).AddHours(-3) } -MaxEvents 15 -ErrorAction SilentlyContinue)
if (-not $refused.Count) { 'Refused sign-ins in the last 3 hours: none' }
foreach ($refusal in $refused) {
    $data = @{}
    foreach ($field in ([xml]$refusal.ToXml()).Event.EventData.Data) { $data[$field.Name] = [string]$field.'#text' }
    $code = if ($data.SubStatus -and $data.SubStatus -ne '0x0') { $data.SubStatus.ToLower() } else { $data.Status.ToLower() }
    $reason = if ($reasons.ContainsKey($code)) { $reasons[$code] } else { "status $code" }
    $name = if ($admin -and $data.TargetUserName -eq $admin.SamAccountName) { 'the administrator' } else { 'another name' }
    $prefix = if (-not $data.TargetDomainName -or $data.TargetDomainName -eq '-') { 'no domain' }
    elseif ($domain -and $data.TargetDomainName -in $domain.NetBIOSName, $domain.DNSRoot) { $data.TargetDomainName }
    else { 'another domain' }
    '{0:yyyy-MM-dd HH:mm:ss} refused (logon type {1}): {2}, {3}: {4}' -f $refusal.TimeCreated, $data.LogonType, $name, $prefix, $reason
}
