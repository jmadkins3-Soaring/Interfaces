"""Command-line interface for the Dark Web Threat Intelligence tool.

Examples
--------
    # One-off search for a single third party you want to vet
    export INTELX_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    python -m darkweb_intel search --company "Acme Corp" \\
        --selector acme.com --selector "@acme.com" --days 365 --html acme.html

    # Scan a whole watchlist of vendors from a YAML file
    python -m darkweb_intel monitor --watchlist config/watchlist.yaml --out-dir reports

    # Check remaining API quota for your key
    python -m darkweb_intel account
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .client import IntelXClient, IntelXError, DEFAULT_BASE_URL, PAID_BASE_URL
from .analyzer import ThreatAnalyzer
from .monitor import Target, scan_target
from . import report as report_mod


def _resolve_api_key(args: argparse.Namespace) -> str:
    key = args.api_key or os.environ.get("INTELX_KEY") or os.environ.get("INTELX_API_KEY")
    if not key:
        raise SystemExit(
            "No API key. Pass --api-key or set the INTELX_KEY environment variable."
        )
    return key


def _resolve_base_url(args: argparse.Namespace) -> str:
    if args.base_url:
        return args.base_url
    return PAID_BASE_URL if args.paid else DEFAULT_BASE_URL


def _build_client(args: argparse.Namespace) -> IntelXClient:
    return IntelXClient(
        api_key=_resolve_api_key(args),
        base_url=_resolve_base_url(args),
        timeout=args.timeout,
        proxy=args.proxy,
        verify=not args.insecure,
    )


def _load_watchlist(path: Path) -> list[Target]:
    text = path.read_text(encoding="utf-8")
    data: Any
    try:
        import yaml  # optional dependency

        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)  # YAML is a superset of JSON; accept JSON too

    if not isinstance(data, dict) or "targets" not in data:
        raise SystemExit("Watchlist must be a mapping with a top-level 'targets' list.")

    targets: list[Target] = []
    for entry in data["targets"]:
        company = entry.get("company") or entry.get("name")
        selectors = entry.get("selectors") or []
        if not company or not selectors:
            raise SystemExit(f"Each target needs 'company' and 'selectors': {entry!r}")
        targets.append(
            Target(
                company=company,
                selectors=list(selectors),
                buckets=list(entry["buckets"]) if entry.get("buckets") else _default_buckets(),
            )
        )
    return targets


def _default_buckets() -> list[str]:
    from .client import DARK_WEB_BUCKETS

    return list(DARK_WEB_BUCKETS)


def _emit_report(report: dict[str, Any], args: argparse.Namespace, *, stem: str) -> None:
    out_dir = Path(args.out_dir) if getattr(args, "out_dir", None) else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    # Console output is always shown unless suppressed.
    if not getattr(args, "quiet", False):
        print(report_mod.to_console(report))

    if getattr(args, "json", None):
        Path(args.json).write_text(report_mod.to_json(report), encoding="utf-8")
        print(f"[+] JSON written to {args.json}", file=sys.stderr)
    if getattr(args, "html", None):
        Path(args.html).write_text(report_mod.to_html(report), encoding="utf-8")
        print(f"[+] HTML written to {args.html}", file=sys.stderr)

    if out_dir:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_") or "report"
        (out_dir / f"{safe}.json").write_text(report_mod.to_json(report), encoding="utf-8")
        (out_dir / f"{safe}.html").write_text(report_mod.to_html(report), encoding="utf-8")
        print(f"[+] Report for '{stem}' written to {out_dir}/", file=sys.stderr)


def cmd_search(args: argparse.Namespace) -> int:
    client = _build_client(args)
    analyzer = ThreatAnalyzer(recent_days=args.recent_days)
    company = args.company or args.selector[0]
    buckets = args.buckets.split(",") if args.buckets else _default_buckets()
    target = Target(company=company, selectors=list(args.selector), buckets=buckets)

    report = scan_target(
        client,
        analyzer,
        target,
        max_results=args.max,
        days=args.days,
        fetch_previews=args.previews,
    )
    _emit_report(report, args, stem=company)
    # Non-zero exit if anything high or worse was found (useful for scripting).
    return 2 if report["summary"]["by_severity"].get("Critical") or \
        report["summary"]["by_severity"].get("High") else 0


def cmd_monitor(args: argparse.Namespace) -> int:
    client = _build_client(args)
    analyzer = ThreatAnalyzer(recent_days=args.recent_days)
    targets = _load_watchlist(Path(args.watchlist))

    worst = 0
    for target in targets:
        report = scan_target(
            client,
            analyzer,
            target,
            max_results=args.max,
            days=args.days,
            fetch_previews=args.previews,
        )
        _emit_report(report, args, stem=target.company)
        sev = report["summary"]["by_severity"]
        if sev.get("Critical") or sev.get("High"):
            worst = 2
    return worst


def cmd_account(args: argparse.Namespace) -> int:
    client = _build_client(args)
    try:
        info = client.account_info()
    except IntelXError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--api-key", help="Intelligence X API key (or set INTELX_KEY).")
    p.add_argument("--base-url", help="Override API base URL.")
    p.add_argument("--paid", action="store_true", help="Use the paid endpoint (2.intelx.io).")
    p.add_argument("--proxy", help="HTTP(S) proxy URL.")
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification.")
    p.add_argument("--timeout", type=int, default=60, help="Request timeout (seconds).")


def _add_scan_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max", type=int, default=100, help="Max results per selector.")
    p.add_argument("--days", type=int, help="Only records from the last N days.")
    p.add_argument("--recent-days", type=int, default=180,
                   help="Records newer than this are severity-escalated.")
    p.add_argument("--buckets", help="Comma-separated Intelligence X buckets to search.")
    p.add_argument("--previews", action="store_true",
                   help="Fetch text previews for top findings (uses more quota).")
    p.add_argument("--json", help="Write the JSON report to this path.")
    p.add_argument("--html", help="Write the HTML report to this path.")
    p.add_argument("--out-dir", help="Write per-company JSON+HTML into this directory.")
    p.add_argument("--quiet", action="store_true", help="Suppress the console table.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="darkweb_intel",
        description="Search Intelligence X for dark-web exposure of third-party companies.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="Search dark web exposure for one company.")
    s.add_argument("--company", help="Display name for the report.")
    s.add_argument("--selector", action="append", required=True,
                   help="A selector to search (domain/email/name). Repeatable.")
    _add_scan_opts(s)
    _add_common(s)
    s.set_defaults(func=cmd_search)

    m = sub.add_parser("monitor", help="Scan a watchlist of third parties from YAML/JSON.")
    m.add_argument("--watchlist", required=True, help="Path to watchlist file.")
    _add_scan_opts(m)
    _add_common(m)
    m.set_defaults(func=cmd_monitor)

    a = sub.add_parser("account", help="Show API key account / quota info.")
    _add_common(a)
    a.set_defaults(func=cmd_account)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except IntelXError as exc:
        print(f"[!] Intelligence X error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
