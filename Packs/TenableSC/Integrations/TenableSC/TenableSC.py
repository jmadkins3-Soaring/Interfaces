from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import urllib3
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from CommonServerPython import *  # noqa: F401

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DATE_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
DEFAULT_PAGE_SIZE = 200
DEFAULT_FETCH_LOOKBACK_DAYS = 2
DEFAULT_FETCH_LIMIT = 1000
DEFAULT_DATASET_NAME = 'tenable.sc_raw'


class TenableSCClient(BaseClient):
    def __init__(
        self,
        base_url: str,
        verify: bool,
        proxy: bool,
        auth_mode: str,
        access_key: str | None = None,
        secret_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int = 60,
        retries: int = 5,
        backoff_factor: float = 1.0,
    ) -> None:
        super().__init__(base_url=base_url, verify=verify, proxy=proxy)
        self.auth_mode = auth_mode
        self.session = Session()
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            status=retries,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['HEAD', 'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
            backoff_factor=backoff_factor,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.timeout = timeout
        self._headers: dict[str, str] = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

        if auth_mode == 'api_keys':
            # Manager step: configure Tenable.sc API-key authentication header used on every API call.
            if not access_key or not secret_key:
                raise DemistoException('Access Key and Secret Key are required for API key authentication.')
            self._headers['x-apikey'] = f'accessKey={access_key}; secretKey={secret_key}'
        elif auth_mode == 'token':
            # Manager step: obtain a session token from Tenable.sc before querying /analysis.
            if not username or not password:
                raise DemistoException('Username and Password are required for token authentication.')
            self._authenticate_token(username, password)
        else:
            raise DemistoException(f'Unsupported authentication mode: {auth_mode}')

    def _authenticate_token(self, username: str, password: str) -> None:
        # Manager step (API call): authenticate against Tenable.sc /token and cache token in headers.
        response = self._request('POST', '/token', data={'username': username, 'password': password}, add_headers=False)
        token = response.get('response', {}).get('token')
        if not token:
            raise DemistoException('Failed to retrieve token from TenableSC.')
        self._headers['X-SecurityCenter'] = str(token)

    def _request(self, method: str, url_suffix: str, params: dict[str, Any] | None = None,
                 data: dict[str, Any] | None = None, add_headers: bool = True) -> dict[str, Any]:
        url = f'{self.base_url}{url_suffix}'
        headers = self._headers if add_headers else {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        # Manager step (API call): execute HTTP request to Tenable.sc with retry/backoff and timeout settings.
        response: Response = self.session.request(
            method=method,
            url=url,
            params=params,
            json=data,
            verify=self._verify,
            proxies=self._proxies,
            timeout=self.timeout,
            headers=headers,
        )
        if response.status_code >= 400:
            raise DemistoException(f'TenableSC API error [{response.status_code}] {response.text}')

        try:
            return response.json()
        except ValueError:
            raise DemistoException(f'Unable to parse JSON response from TenableSC: {response.text}')

    def query_analysis(
        self,
        start_offset: int = 0,
        page_size: int = DEFAULT_PAGE_SIZE,
        since_time: datetime | None = None,
    ) -> list[dict[str, Any]]:
        all_rows: list[dict[str, Any]] = []
        current = start_offset

        while True:
            end_offset = current + page_size - 1
            filters: list[dict[str, Any]] = []
            if since_time:
                filters.append({
                    'filterName': 'lastSeen',
                    'operator': '>=',
                    'value': since_time.strftime(DATE_FORMAT),
                })

            payload: dict[str, Any] = {
                'type': 'vuln',
                'sourceType': 'cumulative',
                'query': {
                    'name': 'XSIAM Daily Vulnerability Pull',
                    'description': 'Generated by Cortex XSIAM TenableSC integration.',
                    'context': 'vuln',
                    'tool': 'vulndetails',
                    'filters': filters,
                },
                'startOffset': current,
                'endOffset': end_offset,
            }

            # Manager step (API call): retrieve a single page of vulnerability data from /analysis.
            response = self._request('POST', '/analysis', data=payload)
            rows = response.get('response', {}).get('results') or response.get('response', {}).get('usable') or []
            if not isinstance(rows, list):
                raise DemistoException('Unexpected /analysis response structure: expected results list.')

            all_rows.extend(rows)
            # Manager step: continue pagination until the API returns less than a full page.
            if len(rows) < page_size:
                break
            current += page_size

        return all_rows


def extract_time(item: dict[str, Any], fallback: datetime) -> datetime:
    candidate = item.get('lastSeen') or item.get('lastMitigated') or item.get('firstSeen')
    if candidate is None:
        return fallback

    if isinstance(candidate, (int, float)):
        return datetime.fromtimestamp(int(candidate), tz=timezone.utc)

    if isinstance(candidate, str):
        normalized = candidate.replace('Z', '+00:00')
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return fallback


def severity_to_dbot(score: Any) -> int:
    try:
        score_num = float(score)
    except (TypeError, ValueError):
        return 0

    if score_num >= 9:
        return 4
    if score_num >= 7:
        return 3
    if score_num >= 4:
        return 2
    if score_num > 0:
        return 1
    return 0


def build_dedupe_key(item: dict[str, Any]) -> str:
    plugin_id = item.get('pluginID') or item.get('pluginId') or item.get('plugin') or 'unknown-plugin'
    asset = item.get('assetUUID') or item.get('uuid') or item.get('ip') or item.get('dnsName') or 'unknown-asset'
    last_seen = item.get('lastSeen') or item.get('firstSeen') or 'unknown-seen'
    raw = f'{plugin_id}|{asset}|{last_seen}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()




def push_dataset_records(dataset_name: str, records: list[dict[str, Any]]) -> None:
    """Push records to a Cortex XSIAM dataset in parallel with incident creation."""
    if not records:
        return

    # Manager step (write to Cortex): send raw+formatted records to custom XSIAM dataset.
    try:
        # Prefer native events ingestion API when available in the running Cortex environment.
        demisto.events(records)
        return
    except Exception:
        pass

    # Fallback path for environments exposing dataset ingestion via automation command.
    res = demisto.executeCommand('xdr-dataset-push-events', {
        'dataset': dataset_name,
        'events': json.dumps(records),
    })
    if is_error(res):
        raise DemistoException(f'Failed pushing events to dataset {dataset_name}: {get_error(res)}')


def map_alert(item: dict[str, Any], fallback_time: datetime) -> dict[str, Any]:
    # Manager step: transform Tenable.sc vulnerability result into Cortex/XSIAM incident schema.
    occurred_dt = extract_time(item, fallback_time)
    occurred = occurred_dt.astimezone(timezone.utc).strftime(DATE_FORMAT)
    plugin_id = item.get('pluginID') or item.get('pluginId') or 'unknown-plugin'
    ip = item.get('ip') or item.get('dnsName') or item.get('assetUUID') or 'unknown-asset'
    severity = severity_to_dbot(item.get('severity') or item.get('cvssV3BaseScore') or item.get('cvssBaseScore'))
    dedupe_key = build_dedupe_key(item)

    return {
        'name': f'TenableSC Vulnerability {plugin_id} on {ip}',
        'occurred': occurred,
        'rawJSON': json.dumps(item),
        'type': 'TenableSC Vulnerability',
        'severity': severity,
        'vendor': 'TenableSC',
        'vendor_id': str(plugin_id),
        'dbotMirrorId': dedupe_key,
    }


def test_module(client: TenableSCClient) -> str:
    # Manager step (API call): run a minimal /analysis query to validate connectivity and credentials.
    _ = client.query_analysis(start_offset=0, page_size=1, since_time=None)
    return 'ok'


def query_analysis_command(client: TenableSCClient, args: dict[str, Any]) -> CommandResults:
    page_size = arg_to_number(args.get('page_size')) or DEFAULT_PAGE_SIZE
    start_offset = arg_to_number(args.get('start_offset')) or 0
    since = args.get('since')
    since_dt = parse_date_string(since) if since else None

    # Manager step (API call): execute analyst-requested /analysis query for troubleshooting.
    rows = client.query_analysis(start_offset=start_offset, page_size=page_size, since_time=since_dt)
    human_readable = tableToMarkdown('TenableSC Analysis Results', rows)

    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='TenableSC.Analysis',
        outputs_key_field='id',
        outputs=rows,
        raw_response=rows,
    )


def fetch_incidents(client: TenableSCClient, max_results: int, lookback_days: int, dataset_name: str) -> None:
    # Manager step: read previous fetch checkpoint from Cortex so we only ingest new/updated findings.
    last_run = demisto.getLastRun() or {}
    now = datetime.now(timezone.utc)
    lookback_start = now - timedelta(days=lookback_days)
    last_fetch = parse_date_string(last_run.get('last_fetch')) if last_run.get('last_fetch') else lookback_start
    if not last_fetch.tzinfo:
        last_fetch = last_fetch.replace(tzinfo=timezone.utc)

    # Manager step (API call): fetch paginated vulnerabilities from Tenable.sc /analysis since last checkpoint.
    rows = client.query_analysis(start_offset=0, page_size=min(max_results, DEFAULT_PAGE_SIZE), since_time=last_fetch)

    incidents: list[dict[str, Any]] = []
    dataset_records: list[dict[str, Any]] = []
    seen = set(last_run.get('seen_ids', []))
    max_seen_size = 5000

    for row in rows:
        dedupe = build_dedupe_key(row)
        if dedupe in seen:
            continue
        # Manager step: map raw Tenable.sc record into Cortex incident payload.
        alert = map_alert(row, fallback_time=now)
        incidents.append(alert)
        dataset_records.append({
            'dataset': dataset_name,
            'event_type': 'tenablesc_vulnerability',
            'event_timestamp': alert.get('occurred'),
            'dedupe_key': dedupe,
            'raw_tenable_output': row,
            'formatted_incident': alert,
        })
        seen.add(dedupe)
        if len(incidents) >= max_results:
            break

    next_seen = list(seen)[-max_seen_size:]
    # Manager step (write to Cortex): persist next fetch checkpoint and dedupe cache in LastRun state.
    demisto.setLastRun({'last_fetch': now.strftime(DATE_FORMAT), 'seen_ids': next_seen})
    # Manager step (write to Cortex): submit prepared incidents to XSIAM ingestion pipeline.
    demisto.incidents(incidents)
    # Manager step (write to Cortex): push raw+formatted fetch records to tenable.sc_raw dataset in parallel.
    push_dataset_records(dataset_name=dataset_name, records=dataset_records)


def main() -> None:
    # Manager step: normalize user-provided host/port into Tenable.sc REST base URL.
    params = demisto.params()
    host = params.get('url', '').strip().rstrip('/')
    port = str(params.get('port') or '443').strip()

    if not host.startswith('http://') and not host.startswith('https://'):
        host = f'https://{host}'

    base_url = f'{host}:{port}/rest'

    # Manager step: build API client with auth/retry/transport options configured in integration instance.
    client = TenableSCClient(
        base_url=base_url,
        verify=not params.get('insecure', False),
        proxy=params.get('proxy', False),
        auth_mode=params.get('auth_mode', 'api_keys'),
        access_key=params.get('credentials', {}).get('identifier') or params.get('access_key'),
        secret_key=params.get('credentials', {}).get('password') or params.get('secret_key'),
        username=params.get('username'),
        password=params.get('password'),
        timeout=arg_to_number(params.get('timeout')) or 60,
        retries=arg_to_number(params.get('retries')) or 5,
        backoff_factor=float(params.get('backoff_factor') or 1),
    )

    command = demisto.command()
    demisto.debug(f'Command being called is {command}')

    try:
        # Manager step: route Cortex command to the correct workflow (test, fetch, or ad-hoc query).
        if command == 'test-module':
            return_results(test_module(client))
        elif command == 'fetch-incidents':
            max_results = arg_to_number(params.get('max_fetch')) or DEFAULT_FETCH_LIMIT
            lookback_days = arg_to_number(params.get('first_fetch')) or DEFAULT_FETCH_LOOKBACK_DAYS
            dataset_name = params.get('dataset_name') or DEFAULT_DATASET_NAME
            fetch_incidents(client, max_results=max_results, lookback_days=lookback_days, dataset_name=dataset_name)
        elif command == 'tenablesc-query-analysis':
            return_results(query_analysis_command(client, demisto.args()))
        elif command == 'tenablesc-test':
            return_results(test_module(client))
        else:
            raise NotImplementedError(f'Command {command} is not implemented.')
    except Exception as exc:
        return_error(f'Failed to execute {command} command. Error: {str(exc)}')


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()
