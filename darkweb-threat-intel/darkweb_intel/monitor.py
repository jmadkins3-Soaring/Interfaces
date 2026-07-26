"""Scan one or many third-party companies for dark-web exposure.

A "scan" runs an Intelligence X search for each selector belonging to a
company (domain, email, brand name ...), scores the results, and produces a
single consolidated report per company.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .analyzer import ThreatAnalyzer, ThreatFinding
from .client import IntelXClient, DARK_WEB_BUCKETS
from . import report as report_mod


@dataclass
class Target:
    """A third-party company and the selectors that identify it."""

    company: str
    selectors: list[str]
    buckets: list[str] = field(default_factory=lambda: list(DARK_WEB_BUCKETS))


def scan_target(
    client: IntelXClient,
    analyzer: ThreatAnalyzer,
    target: Target,
    *,
    max_results: int = 100,
    days: int | None = None,
    fetch_previews: bool = False,
) -> dict[str, Any]:
    """Search every selector for one company and build a consolidated report."""
    date_from = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        date_from = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    all_findings: list[ThreatFinding] = []
    seen: set[str] = set()

    for selector in target.selectors:
        records = client.search(
            selector,
            max_results=max_results,
            buckets=target.buckets or None,
            date_from=date_from,
        )
        for finding in analyzer.analyze(records, matched_selector=selector):
            key = finding.system_id or f"{finding.bucket}:{finding.title}"
            if key in seen:
                continue
            seen.add(key)
            all_findings.append(finding)

    all_findings.sort(key=lambda f: (int(f.severity), f.xscore or 0), reverse=True)

    if fetch_previews:
        _enrich_previews(client, all_findings)

    summary = ThreatAnalyzer.summarize(all_findings)
    return report_mod.build_report(
        company=target.company,
        selectors=target.selectors,
        findings=all_findings,
        summary=summary,
    )


def _enrich_previews(
    client: IntelXClient, findings: list[ThreatFinding], limit: int = 20
) -> None:
    """Fetch text previews for the highest-severity findings only (quota-friendly)."""
    for finding in findings[:limit]:
        if finding.preview:
            continue
        try:
            text = client.read_preview(
                {
                    "storageid": finding.system_id,
                    "systemid": finding.system_id,
                    "media": finding.media_type,
                    "bucket": finding.bucket,
                }
            )
            finding.preview = (text or "").strip()[:500]
        except Exception:
            # Previews are best-effort enrichment; skip on any error.
            continue
