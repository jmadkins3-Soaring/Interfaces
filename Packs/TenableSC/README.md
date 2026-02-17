# TenableSC

## What does this pack do?
This pack ingests vulnerability data from **Tenable.sc** into Cortex XSIAM using the `/analysis` endpoint (`type=vuln`) as the primary extraction path.

The integration is built for daily polling via the standard XSIAM **Fetch Incidents** mechanism.

## Setup
1. Add a new instance of **TenableSC**.
2. Configure:
   - **Tenable.sc Host** (hostname or URL)
   - **Port** (default `443`)
   - **Authentication Mode**:
     - `api_keys`: supply Access/Secret key pair
     - `token`: supply Username/Password
   - **Fetch incidents**: enable
   - **First fetch lookback (days)**: set backfill window
3. Optionally tune reliability settings:
   - Request timeout
   - Retry count for 429/5xx
   - Backoff factor

## Daily pull behavior
- Fetch uses `last_run.last_fetch` and a configurable lookback for initial/backfill behavior.
- Data is pulled with paginated POST calls to `/analysis`.
- Alerts are deduplicated using a stable key based on:
  - `pluginID`
  - Asset identity (`assetUUID`, `uuid`, or `ip`)
  - `lastSeen`

## Sizing guidance (daily polling)
To size daily ingestion safely:
1. Start with a low **Max incidents per fetch** value (for example 500).
2. Run for several days and observe:
   - Volume of fetched vulnerabilities per day
   - XSIAM ingestion utilization and processing lag
3. Increase in steps (500 → 1000 → 2000) until you meet desired coverage without backlog.
4. If backlog appears, either reduce `max_fetch` or split pulls (shorter intervals) to smooth throughput.

## Commands
- `!tenablesc-test` — validates credentials and connectivity by running a minimal `/analysis` query.
- `!tenablesc-query-analysis` — optional ad-hoc query helper for troubleshooting.

## Notes
- SSL validation can be disabled using **Trust any certificate** for test environments.
- This integration uses retry/backoff for transient Tenable.sc API errors (HTTP 429 and 5xx).
