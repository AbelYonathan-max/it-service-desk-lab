# Prints READY once this server is a domain controller with its directory services running, so the
# lab workflow knows the restart after promotion has finished. Read-only.
$role = (Get-CimInstance Win32_ComputerSystem).DomainRole
$running = @(Get-Service -Name NTDS, DNS, ADWS, Netlogon -ErrorAction SilentlyContinue | Where-Object Status -EQ 'Running')
if ($role -ge 4 -and $running.Count -eq 4) {
    'READY'
}
else {
    "WAITING: domain role $role, $($running.Count) of 4 directory services running"
}
