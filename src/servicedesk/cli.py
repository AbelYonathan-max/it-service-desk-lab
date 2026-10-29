"""Command line: ``sdlab``.

    sdlab sn check                 can we reach the instance, and which release is it?
    sdlab sn plan                  what `apply` would change, without changing anything
    sdlab sn apply                 make the instance match servicenow/config
    sdlab sn history               load three months of simulated tickets (once)
    sdlab sn smoke-test            prove the whole process works on the instance
    sdlab sn connect-lab VAULT     create the fulfilment agent's integration user, store it in Key Vault
    sdlab report MONTH             the monthly SLA report as an Excel workbook
    sdlab m365 plan                what `apply` would change in Intune and licensing
    sdlab m365 apply               Intune policies from lab/intune, licence for the licence group
    sdlab m365 status              staff synced, licences, PCs and policy results
    sdlab holidays                 refresh the bank holidays from GOV.UK

ServiceNow commands read SERVICENOW_INSTANCE, SERVICENOW_USERNAME (default admin) and
SERVICENOW_PASSWORD from the environment. Microsoft 365 commands use the Azure CLI's sign-in (or
GRAPH_TOKEN).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from servicedesk.graph import GraphError
from servicedesk.intune import IntuneError
from servicedesk.snow import Connection, InstanceAsleepError, ServiceNow, ServiceNowError


def _summary(lines: Sequence[str]) -> None:
    """Also write to the GitHub Actions job summary when running there."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def _connect() -> ServiceNow:
    return ServiceNow(Connection.from_environment())


def cmd_check(_: argparse.Namespace) -> int:
    with _connect() as sn:
        info = sn.instance_info()
    print(f"Connected as {info['user']}. Release: {info['build']}.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    from servicedesk.apply import apply_all
    from servicedesk.config import load_config

    config = load_config()
    dry_run = args.command == "plan"
    with _connect() as sn:
        result = apply_all(sn, config, dry_run=dry_run, progress=lambda step: print(f"-- {step}", flush=True))
        requests = sn.requests_made
    changed = [c for c in result.changes if c.action != "same"]
    for change in changed:
        print(change)
        if dry_run:
            for difference in change.differences():
                print(f"      {difference}")
    counts = result.counts
    verb = "Would create" if dry_run else "Created"
    line = (
        f"{verb} {counts['create']}, {'would update' if dry_run else 'updated'} {counts['update']}, "
        + (f"{'would delete' if dry_run else 'deleted'} {counts['delete']}, " if counts["delete"] else "")
        + f"{counts['same']} already right ({requests} API calls)."
    )
    print(line)
    _summary(
        [
            f"### ServiceNow {'plan' if dry_run else 'apply'}",
            "",
            line,
            "",
            *(["```", *[str(c) for c in changed], "```"] if changed else []),
        ]
    )
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    from servicedesk.config import load_config
    from servicedesk.simulate import generate, write_history

    config = load_config()
    history = generate(config, seed=args.seed)
    with _connect() as sn:
        written = write_history(sn, config, history, progress=lambda text: print(text, flush=True))
    print(f"History loaded: {written}.")
    _summary(["### Simulated history", "", f"{written}"])
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    from servicedesk.config import load_config
    from servicedesk.smoke import run_smoke_test

    with _connect() as sn:
        results = run_smoke_test(sn, load_config(), wait_for_agent=args.wait_for_agent)
    failed = [r for r in results if not r.passed]
    for result in results:
        print(result)
    _summary(["### Smoke test", "", *[f"- {r}" for r in results]])
    return 1 if failed else 0


def cmd_connect_lab(args: argparse.Namespace) -> int:
    from servicedesk.config import load_config
    from servicedesk.integration import connect_lab

    with _connect() as sn:
        message = connect_lab(sn, load_config(), key_vault=args.key_vault)
    print(message)
    _summary(["### Lab connected to ServiceNow", "", message])
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from servicedesk.config import load_config
    from servicedesk.measure import month_bounds
    from servicedesk.report import build_report
    from servicedesk.sources import tickets_for_report

    config = load_config()
    start, _ = month_bounds(args.month)
    if args.sample:
        tickets, extras = tickets_for_report(config, source="sample", month=start, seed=args.seed)
    else:
        with _connect() as sn:
            tickets, extras = tickets_for_report(config, source=sn, month=start)
    path = Path(args.out or f"out/sla-report-{start:%Y-%m}.xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    headline = build_report(config, tickets, extras, month=start, path=path)
    print(f"Wrote {path}. {headline}")
    _summary([f"### SLA report {start:%B %Y}", "", headline])
    return 0


def cmd_m365(args: argparse.Namespace) -> int:
    from servicedesk.entra import license_group, tenant_status
    from servicedesk.graph import Graph, token_from_azure_cli
    from servicedesk.intune import Intune, load_policies
    from servicedesk.people import load_organisation

    policies = load_policies()
    group = load_organisation().groups["licence"]
    with Graph(token_from_azure_cli()) as graph:
        if args.command == "status":
            lines = tenant_status(graph, group, policies)
            print("\n".join(lines))
            _summary(lines)
            return 0
        apply = args.command == "apply"
        intune = Intune(graph, progress=lambda text: print(f"-- {text}", flush=True))
        result = intune.run(policies, apply=apply)
        licensing = license_group(graph, group, apply=apply)
    for change in result.changes:
        print(change)
    print(f"Licensing: {licensing.message}")
    verb = "would change" if not apply else "changed"
    line = f"Intune: {len(result.changed)} {verb}, {len(result.changes) - len(result.changed)} already right."
    print(line)
    _summary(
        [
            f"### Entra and Intune {args.command}",
            "",
            line,
            "",
            "```",
            *[str(c) for c in result.changes],
            "```",
            "",
            f"Licensing: {licensing.message}",
        ]
    )
    return 0


def cmd_holidays(_: argparse.Namespace) -> int:
    from servicedesk.holidays import refresh

    print(refresh())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sdlab", description="IT service desk lab tooling.")
    top = parser.add_subparsers(dest="area", required=True)

    sn = top.add_parser("sn", help="ServiceNow configuration and data").add_subparsers(
        dest="command", required=True
    )
    sn.add_parser("check", help="test the connection").set_defaults(func=cmd_check)
    sn.add_parser("plan", help="show what apply would change").set_defaults(func=cmd_apply)
    sn.add_parser("apply", help="make the instance match the repository").set_defaults(func=cmd_apply)
    history = sn.add_parser("history", help="load simulated ticket history (skips tickets already loaded)")
    history.add_argument(
        "--seed", type=int, help="another made-up history (default: the one in docs/reports)"
    )
    history.set_defaults(func=cmd_history)
    smoke = sn.add_parser("smoke-test", help="end-to-end test of incidents, SLAs and an automated request")
    smoke.add_argument(
        "--wait-for-agent", action="store_true", help="wait for the lab's agent to close the task"
    )
    smoke.set_defaults(func=cmd_smoke)
    connect = sn.add_parser(
        "connect-lab", help="integration user for the fulfilment agent, stored in Key Vault"
    )
    connect.add_argument("key_vault", help="name of the lab's Key Vault")
    connect.set_defaults(func=cmd_connect_lab)

    report = top.add_parser("report", help="monthly SLA report (Excel)")
    report.add_argument("month", help="YYYY-MM")
    report.add_argument("--out", help="output file (default out/sla-report-YYYY-MM.xlsx)")
    report.add_argument(
        "--sample", action="store_true", help="use the simulated history instead of ServiceNow"
    )
    report.add_argument("--seed", type=int, help="with --sample: another made-up history")
    report.set_defaults(func=cmd_report)

    m365 = top.add_parser("m365", help="Intune policies and Microsoft 365 licensing").add_subparsers(
        dest="command", required=True
    )
    m365.add_parser("plan", help="show what apply would change").set_defaults(func=cmd_m365)
    m365.add_parser("apply", help="apply lab/intune and the group licence").set_defaults(func=cmd_m365)
    m365.add_parser("status", help="staff synced, licences, PCs and policy results").set_defaults(
        func=cmd_m365
    )

    top.add_parser("holidays", help="refresh the bank holidays from GOV.UK").set_defaults(func=cmd_holidays)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except InstanceAsleepError as error:
        print(f"::warning::{error}" if os.environ.get("GITHUB_ACTIONS") else error, file=sys.stderr)
        return 3
    except (ServiceNowError, GraphError, IntuneError) as error:
        print(f"::error::{error}" if os.environ.get("GITHUB_ACTIONS") else error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
