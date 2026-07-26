from datetime import datetime, timezone

from darkweb_intel.analyzer import Severity, ThreatAnalyzer


NOW = datetime(2026, 7, 26, tzinfo=timezone.utc)


def _analyzer():
    return ThreatAnalyzer(recent_days=180, now=NOW)


def test_bucket_longest_prefix_wins():
    rec = {"systemid": "1", "name": "dump", "bucket": "leaks.logs.stealer", "date": "2020-01-01 00:00:00"}
    finding = _analyzer().analyze_record(rec, "acme.com")
    # leaks.logs -> Critical base, and old date so no recency bump beyond cap
    assert finding.severity == Severity.CRITICAL
    assert finding.category == "Stealer / credential logs"


def test_keyword_escalates_paste():
    rec = {"systemid": "2", "name": "acme employee password list", "bucket": "pastes",
           "date": "2019-01-01 00:00:00"}
    finding = _analyzer().analyze_record(rec, "acme.com")
    # base MEDIUM, escalated to HIGH by 'password'
    assert finding.severity == Severity.HIGH
    assert "password" in finding.rationale


def test_recency_bumps_severity():
    rec = {"systemid": "3", "name": "acme mention", "bucket": "web.public",
           "date": "2026-07-01 00:00:00"}
    finding = _analyzer().analyze_record(rec, "acme.com")
    # web.public base LOW -> recent bump to MEDIUM
    assert finding.severity == Severity.MEDIUM
    assert "recent" in finding.rationale


def test_email_tag_detected():
    rec = {"systemid": "4", "name": "leak contact bob@acme.com", "bucket": "pastes",
           "date": "2019-01-01 00:00:00"}
    finding = _analyzer().analyze_record(rec, "acme.com")
    assert "contains-email" in finding.tags


def test_sorting_and_summary():
    recs = [
        {"systemid": "a", "name": "note", "bucket": "whois", "date": "2019-01-01 00:00:00"},
        {"systemid": "b", "name": "ransom leak of acme", "bucket": "darknet.tor",
         "date": "2019-01-01 00:00:00"},
        {"systemid": "c", "name": "acme paste", "bucket": "pastes", "date": "2019-01-01 00:00:00"},
    ]
    findings = _analyzer().analyze(recs, "acme.com")
    # Highest severity first
    assert findings[0].system_id == "b"
    summary = ThreatAnalyzer.summarize(findings)
    assert summary["total_findings"] == 3
    assert summary["highest_severity"] == "Critical"
    assert summary["by_severity"]["Critical"] >= 1


def test_summarize_clean_when_empty():
    summary = ThreatAnalyzer.summarize([])
    assert summary["total_findings"] == 0
    assert "CLEAN" in summary["verdict"]
