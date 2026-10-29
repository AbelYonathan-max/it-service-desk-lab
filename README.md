# IT service desk and Microsoft lab

The IT service desk of a fictional council, Kestrel Borough Council, built end to end:

- **ServiceNow**, configured entirely by code: teams, priority-based SLAs on UK working hours,
  a service catalogue for starters, leavers, moves and shared drive access, approvals, routing
  and a knowledge base.
- **A Microsoft lab on Azure**, built with Terraform: a Windows Server domain controller (Active
  Directory, DNS, Group Policy, file shares) and a Windows 11 PC joined to Entra ID and managed by
  Intune. No public IP addresses; access through Azure Bastion.
- **PowerShell automation**: an agent on the domain controller carries out approved ServiceNow
  requests in Active Directory and closes the tickets.
- **Intune policies and Microsoft 365 licensing**, applied through Microsoft Graph.
- **A monthly SLA report in Excel**, measured from the tickets themselves.

Every person, ticket and number in it is made up.

## How a request goes through

1. A manager asks for a new starter in the ServiceNow portal and approves it.
2. ServiceNow creates a task for the "Identity and Access Automation" team.
3. The agent on the domain controller picks the task up within two minutes, creates the account,
   groups, home drive and licence group membership, saves the temporary password in Key Vault,
   writes what it did in the task and closes it.
4. Entra Cloud Sync copies the account to Entra ID, where group-based licensing gives it
   Microsoft 365.

Leavers, moves and shared drive access work the same way. Anything the agent cannot do goes to
the Identity and Access team with the reason.

## Results

From the running lab, 29 October 2026:

- **End to end**: the ServiceNow smoke test passes every check. A shared drive request goes to the
  data owner, is approved, and the agent on the domain controller carries it out in Active
  Directory and closes the task.
- **SLA report** for September 2026, from the simulated history ([docs/reports](docs/reports)):
  98.8% of incidents responded to within target, 91.8% resolved within target, and 88.9% of
  requests completed on time against a 90% target.
- **Entra ID**: Cloud Sync copies all 74 staff accounts from Active Directory, with password hash
  sync. Group-based licensing gives the 18 members of `GG-LIC-M365-BusinessPremium` Microsoft 365
  Business Premium, with no licence errors.
- **Intune**: the Windows 11 PC is joined to Entra ID and enrolled, with the 6 policies applied
  from Git. BitLocker encrypts it with XTS-AES 256 and keeps the recovery key in Entra ID, and
  Windows LAPS manages its local administrator password.

## What's where

| Folder | What it holds |
|---|---|
| `servicenow/config`, `servicenow/scripts` | The ServiceNow configuration and its server-side scripts |
| `docs/kb` | Knowledge base articles, published to ServiceNow |
| `lab/config` | The council's departments, shared drives and staff list (builds AD and ServiceNow's users) |
| `lab/dc`, `lab/ws` | Build stages for the domain controller and the PC, the agent's entry point, health checks |
| `lab/intune` | Intune policies: compliance, update ring, BitLocker, Windows LAPS, Defender, device security |
| `powershell/ServiceDeskLab` | PowerShell module: joiners, movers, leavers, password resets, lockouts, share access, the agent |
| `infra/terraform`, `infra/bootstrap` | The Azure lab, and the one-off set-up script for Azure Cloud Shell |
| `src/servicedesk` | The `sdlab` command line: ServiceNow configuration, simulated history, SLA report, Intune |
| `docs/reports` | A sample SLA report: September 2026, from the simulated history |
| `.github/workflows` | CI and the workflows that run everything |

## Running it

The account steps (ServiceNow developer instance, Azure, Microsoft 365 trial, Cloud Sync) are in
[docs/setup.md](docs/setup.md). Everything else runs from the **Actions** tab:

| Workflow | Actions |
|---|---|
| Lab | `deploy`, `build`, `start`, `stop`, `doctor`, `postpone`, `destroy` |
| ServiceNow | `check`, `plan`, `apply`, `history`, `smoke-test`, `connect-lab`, `report` |
| Entra and Intune | `plan`, `apply`, `status` |

The first time, in this order: Lab `deploy` (the set-up script starts it), ServiceNow `apply`,
`history`, `connect-lab`, then `smoke-test` with "wait for agent" ticked. Once Microsoft 365 and
Cloud Sync are set up: Entra and Intune `apply`, then, once the PC has synced with Intune (about 15
minutes), Lab `build` with stage `01-Start-Encryption` to start BitLocker on the PC.

On your own machine (Python 3.12 or later):

```bash
pip install -e ".[dev]"
pytest
sdlab report 2026-09 --sample   # the SLA report from the simulated history, no ServiceNow needed
```

## Tests

CI runs on every push: 128 Python tests (pytest), 63 PowerShell tests (Pester, on PowerShell 7 and
on Windows PowerShell 5.1, which the domain controller runs), Terraform tests with mocked
providers, and ruff, mypy, PSScriptAnalyzer, bandit, pip-audit, shellcheck and actionlint. The
ServiceNow scripts are checked as ES5, the JavaScript the instance runs.

## Cost

See [What it costs](docs/setup.md#what-it-costs). The VMs shut down at 19:00 every day.

## Licence

[MIT](LICENSE)
