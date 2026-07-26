"""Turn raw Intelligence X records into scored, human-meaningful threat findings.

Intelligence X returns loosely-structured records spanning many "buckets"
(darknet, leaks, pastes, whois, public documents ...). For third-party risk
work we care about *what kind of exposure* a record represents and *how urgent*
it is. This module maps each record onto a normalized :class:`ThreatFinding`
with a :class:`Severity`, a category, and a short rationale.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Iterable


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.title()


# Bucket-prefix -> (category, base severity). Longest prefix wins.
_BUCKET_RULES: list[tuple[str, str, Severity]] = [
    ("leaks.logs", "Stealer / credential logs", Severity.CRITICAL),
    ("leaks.private", "Private breach data", Severity.CRITICAL),
    ("leaks.public", "Public breach data", Severity.HIGH),
    ("leaks", "Breach data", Severity.HIGH),
    ("dumpster", "Aggregated leak dump", Severity.HIGH),
    ("darknet.tor", "Dark web (Tor)", Severity.HIGH),
    ("darknet.i2p", "Dark web (I2P)", Severity.HIGH),
    ("darknet", "Dark web", Severity.HIGH),
    ("pastes", "Paste site", Severity.MEDIUM),
    ("documents.public", "Public document", Severity.LOW),
    ("whois", "WHOIS / infrastructure", Severity.INFO),
    ("usenet", "Usenet", Severity.LOW),
    ("web.public", "Public web", Severity.LOW),
]

# Keywords that, when found in a record name/preview, raise the severity because
# they strongly imply active credential or access exposure.
_ESCALATION_KEYWORDS = {
    "password": Severity.HIGH,
    "passwd": Severity.HIGH,
    "credential": Severity.HIGH,
    "combolist": Severity.HIGH,
    "combo list": Severity.HIGH,
    "ransom": Severity.CRITICAL,
    "breach": Severity.HIGH,
    "database dump": Severity.HIGH,
    "db dump": Severity.HIGH,
    "cvv": Severity.CRITICAL,
    "credit card": Severity.CRITICAL,
    "ssn": Severity.CRITICAL,
    "api key": Severity.HIGH,
    "secret key": Severity.HIGH,
    "private key": Severity.CRITICAL,
    "rdp": Severity.HIGH,
    "vpn": Severity.HIGH,
}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


@dataclass
class ThreatFinding:
    """A normalized, scored dark-web exposure record."""

    system_id: str
    title: str
    category: str
    severity: Severity
    bucket: str
    date: str | None
    media_type: int
    xscore: int | None
    rationale: str
    matched_selector: str
    preview: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.label
        d["severity_score"] = int(self.severity)
        return d


def _match_bucket(bucket: str) -> tuple[str, Severity]:
    bucket_l = (bucket or "").lower()
    best: tuple[str, Severity] | None = None
    best_len = -1
    for prefix, category, sev in _BUCKET_RULES:
        if bucket_l.startswith(prefix) and len(prefix) > best_len:
            best = (category, sev)
            best_len = len(prefix)
    return best or ("Uncategorized", Severity.LOW)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class ThreatAnalyzer:
    """Scores Intelligence X records and rolls them up into a company report."""

    def __init__(self, recent_days: int = 180, now: datetime | None = None) -> None:
        self.recent_days = recent_days
        self._now = now or datetime.now(timezone.utc)

    def analyze_record(
        self, record: dict[str, Any], matched_selector: str
    ) -> ThreatFinding:
        bucket = record.get("bucket") or record.get("bucketh") or ""
        category, base_sev = _match_bucket(bucket)
        title = record.get("name") or record.get("description") or "(untitled record)"
        preview = record.get("preview") or ""

        severity = base_sev
        reasons: list[str] = [f"source '{bucket or 'unknown'}' → {category.lower()}"]

        # Keyword escalation from the title + preview text.
        haystack = f"{title} {preview}".lower()
        for keyword, kw_sev in _ESCALATION_KEYWORDS.items():
            if keyword in haystack and kw_sev > severity:
                severity = kw_sev
                reasons.append(f"mentions '{keyword}'")

        # Recency escalation: fresh exposure is more actionable.
        dt = _parse_date(record.get("date") or record.get("added"))
        if dt is not None:
            age_days = (self._now - dt).days
            if 0 <= age_days <= self.recent_days and severity < Severity.CRITICAL:
                severity = Severity(min(int(severity) + 1, int(Severity.CRITICAL)))
                reasons.append(f"recent ({age_days}d old)")

        xscore = record.get("xscore")
        try:
            xscore = int(xscore) if xscore is not None else None
        except (TypeError, ValueError):
            xscore = None

        tags = []
        if _EMAIL_RE.search(haystack):
            tags.append("contains-email")
        raw_tags = record.get("tags")
        if isinstance(raw_tags, list):
            tags.extend(str(t.get("value", t)) if isinstance(t, dict) else str(t) for t in raw_tags)

        return ThreatFinding(
            system_id=record.get("systemid") or record.get("storageid") or "",
            title=title,
            category=category,
            severity=severity,
            bucket=bucket,
            date=record.get("date") or record.get("added"),
            media_type=int(record.get("media", 0) or 0),
            xscore=xscore,
            rationale="; ".join(reasons),
            matched_selector=matched_selector,
            preview=preview.strip()[:500],
            tags=tags,
        )

    def analyze(
        self, records: Iterable[dict[str, Any]], matched_selector: str
    ) -> list[ThreatFinding]:
        findings = [self.analyze_record(r, matched_selector) for r in records]
        findings.sort(key=lambda f: (int(f.severity), f.xscore or 0), reverse=True)
        return findings

    @staticmethod
    def summarize(findings: list[ThreatFinding]) -> dict[str, Any]:
        """Aggregate findings into counts + an overall risk verdict."""
        by_sev: dict[str, int] = {s.label: 0 for s in Severity}
        by_category: dict[str, int] = {}
        for f in findings:
            by_sev[f.severity.label] += 1
            by_category[f.category] = by_category.get(f.category, 0) + 1

        top = max((f.severity for f in findings), default=Severity.INFO)
        if top >= Severity.CRITICAL:
            verdict = "CRITICAL — active/credential exposure found; investigate immediately."
        elif top >= Severity.HIGH:
            verdict = "HIGH — dark web or breach exposure found; review promptly."
        elif top >= Severity.MEDIUM:
            verdict = "MEDIUM — paste/site exposure found; monitor."
        elif findings:
            verdict = "LOW — only low-signal public records found."
        else:
            verdict = "CLEAN — no dark web exposure found for the supplied selectors."

        return {
            "total_findings": len(findings),
            "by_severity": by_sev,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "highest_severity": top.label,
            "verdict": verdict,
        }
