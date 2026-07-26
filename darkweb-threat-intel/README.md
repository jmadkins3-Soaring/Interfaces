# darkweb-threat-intel

A small, standalone command-line tool for **third-party / supply-chain dark web
due diligence**. You give it a company (a domain, an email domain, a brand
name), and it searches [Intelligence X](https://intelx.io) for that company's
exposure across the dark web, breach dumps, stealer logs, and paste sites —
then scores every hit and produces a ranked report.

Built for personal consulting work: no XSIAM, no SIEM, no vendor lock-in — just
an API key and a terminal.

## What it does

- Searches Intelligence X buckets that actually matter for threat work
  (`darknet.tor`, `darknet.i2p`, `leaks.*`, `dumpster`, `pastes`).
- Scores each record into **Critical / High / Medium / Low / Info** based on the
  source bucket, escalation keywords (`password`, `ransom`, `combolist`,
  `private key`, `cvv`, …), and recency.
- Rolls findings up into a per-company **verdict** and severity breakdown.
- Outputs a **console table**, a **JSON** report, and a self-contained **HTML**
  report.
- Monitors a whole **watchlist** of vendors from a YAML/JSON file in one run.

## Requirements

- Python 3.10+
- An Intelligence X API key (free-tier keys work; paid keys unlock more sources
  and higher quota). Get one at https://intelx.io.
- `pip install -r requirements.txt` (`PyYAML` is optional — only needed for YAML
  watchlists; JSON watchlists work without it).

## Setup

```bash
cd darkweb-threat-intel
pip install -r requirements.txt
export INTELX_KEY="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

Free-tier keys use `https://free.intelx.io` (the default). If you have a paid
key, add `--paid` (or `--base-url https://2.intelx.io`).

## Usage

### Check your key / remaining quota

```bash
python -m darkweb_intel account
```

### Vet a single third party

```bash
python -m darkweb_intel search \
  --company "Acme Supplier" \
  --selector acme-supplier.com \
  --selector "@acme-supplier.com" \
  --days 365 \
  --html acme.html --json acme.json
```

- `--selector` is repeatable — add every identifier that represents the vendor.
- `--days N` restricts to records from the last N days.
- `--previews` pulls short text previews for the top findings (uses more quota).
- Exit code is `2` when any **High** or **Critical** finding exists, so you can
  wire it into scripts/cron.

### Monitor a watchlist of vendors

Copy `config/watchlist.example.yaml` to `config/watchlist.yaml`, edit it, then:

```bash
python -m darkweb_intel monitor \
  --watchlist config/watchlist.yaml \
  --days 90 \
  --out-dir reports
```

This writes one `<company>.json` and `<company>.html` per vendor into `reports/`.

## Watchlist format

```yaml
targets:
  - company: "Example Vendor Inc"
    selectors:
      - "examplevendor.com"
      - "@examplevendor.com"
      - "Example Vendor Inc"
    # buckets: optional — omit to use the default dark-web/leak/paste set
```

## How scoring works

| Source bucket            | Category                     | Base severity |
|--------------------------|------------------------------|---------------|
| `leaks.logs`             | Stealer / credential logs    | Critical      |
| `leaks.private`          | Private breach data          | Critical      |
| `leaks.public`, `dumpster` | Breach / leak data         | High          |
| `darknet.tor`, `darknet.i2p` | Dark web                 | High          |
| `pastes`                 | Paste site                   | Medium        |
| `documents.public`, `usenet`, `web.public` | Public records | Low  |
| `whois`                  | WHOIS / infrastructure       | Info          |

The base severity is then **escalated** when the record's title/preview contains
credential/breach keywords, or when the record is recent (within `--recent-days`,
default 180). Findings are de-duplicated across selectors by Intelligence X
system id and sorted worst-first.

## Project layout

```
darkweb_intel/
  client.py     # Intelligence X REST client (search, poll, preview, account)
  analyzer.py   # record -> scored ThreatFinding, severity + summary rollup
  monitor.py    # per-company scan across many selectors
  report.py     # JSON / console / HTML renderers
  cli.py        # argparse CLI: search | monitor | account
tests/          # offline, mocked unit tests
config/         # example watchlist
```

Run the tests with `python -m pytest`.

## Notes & ethics

This tool only *reads* threat-intelligence data that Intelligence X has already
indexed, for legitimate due-diligence and defensive monitoring of organizations
you have a business relationship with. Respect Intelligence X's terms of service
and applicable law. Generated reports can contain sensitive data — the
`.gitignore` keeps `reports/` and local watchlists out of version control.

---

*Soaring Heights — jmadkins3-Soaring*
