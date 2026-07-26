"""Render dark-web threat findings as JSON, a console table, or an HTML report."""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from .analyzer import Severity, ThreatFinding

_SEVERITY_COLORS = {
    "Critical": "#b00020",
    "High": "#d9480f",
    "Medium": "#b8860b",
    "Low": "#3a7d44",
    "Info": "#5a6570",
}


def build_report(
    company: str,
    selectors: list[str],
    findings: list[ThreatFinding],
    summary: dict[str, Any],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the canonical report structure used by every renderer."""
    ts = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "company": company,
        "selectors": selectors,
        "generated_at": ts,
        "summary": summary,
        "findings": [f.to_dict() for f in findings],
    }


def to_json(report: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(report, indent=indent, ensure_ascii=False)


def to_console(report: dict[str, Any], *, max_rows: int = 50) -> str:
    """Plain-text summary suitable for a terminal."""
    lines: list[str] = []
    summary = report["summary"]
    lines.append("=" * 78)
    lines.append(f"  DARK WEB THREAT REPORT — {report['company']}")
    lines.append(f"  Selectors : {', '.join(report['selectors'])}")
    lines.append(f"  Generated : {report['generated_at']}")
    lines.append("=" * 78)
    lines.append(f"  VERDICT: {summary['verdict']}")
    lines.append("")
    sev = summary["by_severity"]
    order = ["Critical", "High", "Medium", "Low", "Info"]
    counts = "  ".join(f"{k}:{sev.get(k, 0)}" for k in order)
    lines.append(f"  Findings: {summary['total_findings']}   [{counts}]")
    lines.append("-" * 78)

    if not report["findings"]:
        lines.append("  No records found.")
        return "\n".join(lines)

    header = f"  {'SEV':<8} {'DATE':<11} {'CATEGORY':<26} TITLE"
    lines.append(header)
    lines.append("-" * 78)
    for f in report["findings"][:max_rows]:
        date = (f.get("date") or "")[:10]
        title = (f.get("title") or "")[:60]
        lines.append(
            f"  {f['severity']:<8} {date:<11} {f['category'][:26]:<26} {title}"
        )
    remaining = len(report["findings"]) - max_rows
    if remaining > 0:
        lines.append(f"  ... and {remaining} more (see JSON/HTML report).")
    return "\n".join(lines)


def to_html(report: dict[str, Any]) -> str:
    """Self-contained HTML report."""
    e = html.escape
    summary = report["summary"]
    sev = summary["by_severity"]

    chips = "".join(
        f'<span class="chip" style="background:{_SEVERITY_COLORS[k]}">'
        f'{k}: {sev.get(k, 0)}</span>'
        for k in ["Critical", "High", "Medium", "Low", "Info"]
    )

    rows = []
    for f in report["findings"]:
        color = _SEVERITY_COLORS.get(f["severity"], "#5a6570")
        preview = e(f.get("preview") or "")
        rows.append(
            "<tr>"
            f'<td><span class="pill" style="background:{color}">{e(f["severity"])}</span></td>'
            f'<td class="mono">{e((f.get("date") or "")[:19])}</td>'
            f'<td>{e(f["category"])}</td>'
            f'<td class="src mono">{e(f["bucket"])}</td>'
            f'<td>{e(f["title"])}'
            + (f'<div class="preview">{preview}</div>' if preview else "")
            + "</td>"
            f'<td>{e(f["rationale"])}</td>'
            "</tr>"
        )
    rows_html = "\n".join(rows) or '<tr><td colspan="6">No records found.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dark Web Threat Report — {e(report['company'])}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background:#0f1117; color:#e8eaed; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color:#9aa0a6; font-size: 13px; margin-bottom: 16px; }}
  .verdict {{ padding:14px 16px; border-radius:8px; background:#1b1e27; border-left:4px solid #d9480f; font-weight:600; margin-bottom:16px; }}
  .chips {{ margin-bottom: 20px; }}
  .chip, .pill {{ display:inline-block; color:#fff; padding:3px 10px; border-radius:20px; font-size:12px; font-weight:600; margin-right:6px; }}
  .pill {{ padding:2px 8px; border-radius:4px; }}
  table {{ width:100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #262a34; vertical-align: top; }}
  th {{ color:#9aa0a6; font-weight:600; text-transform:uppercase; font-size:11px; letter-spacing:.04em; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
  .src {{ color:#9aa0a6; }}
  .preview {{ color:#9aa0a6; font-size:12px; margin-top:4px; white-space:pre-wrap; word-break:break-word; }}
  .overflow {{ overflow-x:auto; }}
  @media (prefers-color-scheme: light) {{
    body {{ background:#f6f7f9; color:#1a1c22; }}
    .verdict {{ background:#fff; }}
    th, td {{ border-color:#e3e6ea; }}
    .src, .meta, .preview {{ color:#5a6570; }}
  }}
</style></head><body><div class="wrap">
  <h1>Dark Web Threat Report — {e(report['company'])}</h1>
  <div class="meta">Selectors: {e(', '.join(report['selectors']))} &middot; Generated {e(report['generated_at'])}</div>
  <div class="verdict">{e(summary['verdict'])}</div>
  <div class="chips">{chips}</div>
  <div class="overflow"><table>
    <thead><tr><th>Severity</th><th>Date</th><th>Category</th><th>Source</th><th>Title</th><th>Why flagged</th></tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table></div>
  <p class="meta">Total findings: {summary['total_findings']} &middot; Highest severity: {e(summary['highest_severity'])}</p>
</div></body></html>"""
