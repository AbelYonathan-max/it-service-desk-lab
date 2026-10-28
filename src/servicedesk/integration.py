"""The fulfilment agent's ServiceNow account, and handing its credentials to the lab.

The agent on the domain controller signs in to ServiceNow as an integration user that:
- can only use the REST API (web_service_access_only), never the browser;
- has itil (read requests and their variables, update catalogue tasks) and user_admin (keep the
  user list in step with AD), and nothing else;
- is the only member of "Identity and Access Automation".

Its password is random, set here, checked, and written straight to the lab's Key Vault, where the
domain controller's managed identity reads it. Nobody sees it. Running this again rotates it.
"""

from __future__ import annotations

import os
import secrets
import string
import subprocess  # nosec B404
import tempfile
import time
from pathlib import Path

from servicedesk.config import Config
from servicedesk.snow import Connection, ServiceNow, ServiceNowError, quote

USER_NAME = "svc.sdlab.agent"
ROLES = ("itil", "user_admin")
SYMBOLS = "!#%+=?@"


def new_password(length: int = 32) -> str:
    """Random, with every character class ServiceNow's password policy might ask for."""
    alphabet = string.ascii_letters + string.digits + SYMBOLS
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in SYMBOLS for c in password)
        ):
            return password


def ensure_integration_user(sn: ServiceNow, config: Config) -> str:
    fields = {
        "first_name": "Fulfilment agent",
        "last_name": "(DC01)",
        "title": "Integration user for the lab's fulfilment agent",
        "web_service_access_only": True,
        "internal_integration_user": True,
        "active": True,
        "locked_out": False,
    }
    fields["password_needs_reset"] = False  # set apart: in the literal, scanners mistake it for a password
    user = sn.first("sys_user", f"user_name={USER_NAME}", fields=["sys_id"])
    user_id = (
        sn.update("sys_user", user["sys_id"], fields).get("sys_id", user["sys_id"])
        if user
        else sn.create("sys_user", {"user_name": USER_NAME, **fields})["sys_id"]
    )
    for role in ROLES:
        role_record = sn.first("sys_user_role", f"name={role}", fields=["sys_id"])
        if role_record is None:
            raise ServiceNowError(f"The instance has no {role} role.")
        if not sn.first(
            "sys_user_has_role", f"user={user_id}^role={role_record['sys_id']}", fields=["sys_id"]
        ):
            sn.create("sys_user_has_role", {"user": user_id, "role": role_record["sys_id"]})
    group_name = config.catalog["automation_group"]
    group = sn.first("sys_user_group", f"name={quote(group_name)}", fields=["sys_id"])
    if group is None:
        raise ServiceNowError(f"No group called {group_name}: run `sdlab sn apply` first.")
    if not sn.first("sys_user_grmember", f"group={group['sys_id']}^user={user_id}", fields=["sys_id"]):
        sn.create("sys_user_grmember", {"group": group["sys_id"], "user": user_id})
    return str(user_id)


def set_password(sn: ServiceNow, user_id: str, password: str) -> None:
    # The password field only takes plain text through the display-value path, which hashes it.
    sn.update("sys_user", user_id, {"user_password": password}, display_values=True)


def store_in_key_vault(vault: str, secrets_to_store: dict[str, str]) -> None:
    """Writes each secret with the Azure CLI (signed in by the workflow), never on a command line."""
    with tempfile.TemporaryDirectory() as directory:
        for name, value in secrets_to_store.items():
            path = Path(directory) / name
            path.write_text(value, encoding="utf-8")
            path.chmod(0o600)
            # The Azure CLI from PATH, with fixed arguments and no shell; the secret goes in a file.
            subprocess.run(  # noqa: S603  # nosec B603 B607
                [  # noqa: S607
                    "az",
                    "keyvault",
                    "secret",
                    "set",
                    "--vault-name",
                    vault,
                    "--name",
                    name,
                    "--file",
                    str(path),
                    "--encoding",
                    "utf-8",
                    "--output",
                    "none",
                ],
                check=True,
            )


def connect_lab(sn: ServiceNow, config: Config, *, key_vault: str) -> str:
    password = new_password()
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{password}", flush=True)
    user_id = ensure_integration_user(sn, config)
    set_password(sn, user_id, password)

    instance = sn.connection.instance
    for attempt in range(1, 4):
        try:
            with ServiceNow(Connection(instance, USER_NAME, password)) as agent:
                # Raises if the account cannot sign in or is not allowed to read catalogue tasks.
                agent.query("sc_task", "active=true", fields=["sys_id"], limit=1)
            break
        except ServiceNowError as error:
            if error.status != 401 or attempt == 3:
                raise ServiceNowError(f"The integration user cannot read catalogue tasks: {error}") from error
            time.sleep(5)

    store_in_key_vault(
        key_vault,
        {"servicenow-instance": instance, "servicenow-username": USER_NAME, "servicenow-password": password},
    )
    return (
        f"Integration user {USER_NAME} is ready (roles {', '.join(ROLES)}; REST API only) and can read catalogue "
        f"tasks. Its new password is in Key Vault {key_vault}; the fulfilment agent uses it from its next run."
    )
