# Set-up: the steps that need your own accounts

Everything in this repository is built and run by code. Four steps need a person, because they
sign up for accounts, accept terms or approve a sign-in. About 40 minutes in total.

Never paste a password into a chat, an issue or a commit. The steps below put each one straight
into GitHub secrets, Azure Key Vault or your password manager.

## 1. ServiceNow developer instance (5 minutes, free)

1. Sign up at <https://developer.servicenow.com> and choose **Request instance** (the newest
   release offered). There is sometimes a waiting list; you get an email when it is ready.
2. When it is ready, the developer site shows the instance name (`dev123456`) and the `admin`
   password. Keep them for step 2.

An instance sleeps when it is not used; wake it by signing in at the developer site. After 90
days, an instance nobody has signed in to for 10 days can be reclaimed. If that happens, request a
new one and run the **ServiceNow** workflow with `apply`: the whole configuration is rebuilt from
this repository.

## 2. Azure set-up (10 minutes)

1. In the [Azure portal](https://portal.azure.com), click **>_** (Cloud Shell) in the top bar. The
   first time, choose **Bash**, then **No storage account required**, your subscription, **Apply**.
2. At the `$` prompt, paste this line (right-click > Paste, or Ctrl+Shift+V) and press Enter. The
   same line works every time: it fetches the latest copy of the repository first.

```bash
cd ~ && rm -rf it-service-desk-lab && git clone https://github.com/AbelYonathan-max/it-service-desk-lab.git && bash it-service-desk-lab/infra/bootstrap/bootstrap.sh AbelYonathan-max/it-service-desk-lab
```

- It asks you to sign in to GitHub with a one-time code: open <https://github.com/login/device>,
  enter the code and approve (to set the repository's variables). Press Enter at any yes/no question.
- It creates `lab.admin@<your tenant>.onmicrosoft.com` and prints its temporary password **once**.
  Save it in your password manager.
- It asks for the ServiceNow instance name and admin password. Press Enter to skip if step 1 is
  still waiting; run the command again later to add them.
- It then starts the lab deploy (about 40 minutes). Progress:
  <https://github.com/AbelYonathan-max/it-service-desk-lab/actions/workflows/lab.yml>

The script is safe to run again at any time. [What it does, step by step](../infra/bootstrap/bootstrap.sh).

## 3. Microsoft 365 Business Premium trial (10 minutes)

This gives the tenant Intune and Entra ID P1 for a month, free.

1. Open <https://admin.microsoft.com> in a private window and sign in as `lab.admin@…` with the
   temporary password. Choose a new password and set up Microsoft Authenticator when asked.
2. **Marketplace** > **Microsoft 365 Business Premium** > **Start free trial**. Microsoft may ask
   for a card.
3. **Billing** > **Your products** > the trial > turn **recurring billing** off, so it ends by
   itself after the month.
4. Run the Cloud Shell command from step 2 again. It turns on automatic Intune enrolment and
   Windows LAPS, and the next lab deploy joins the Windows 11 PC to Entra ID.

## 4. Connect Active Directory to Entra ID (10 minutes, after the lab is built)

Entra Cloud Sync copies the council's staff accounts from the domain controller to Entra ID. Its
agent has to be registered by a person signing in interactively (with MFA), so this one is by hand:

1. Azure portal > **vm-dc01** > **Connect** > **Bastion**. Sign in as `sdlab.admin`; the password is
   in the lab Key Vault (`kv-sdlab-…`) > **Secrets** > `dc01-admin-password`.
2. Follow the note **Connect to Microsoft Entra ID** on the desktop.

## What it costs

- ServiceNow developer instance: free.
- Microsoft 365 trial: free for a month with recurring billing off.
- Azure: covered by the free trial credit. Afterwards, roughly £10 a month for the disks while the
  lab exists, plus about 10p an hour while the two VMs run. The VMs shut down at 19:00 every day;
  run the **Lab** workflow with `stop` when you finish and `start` to carry on, or `destroy` to
  remove it all.
