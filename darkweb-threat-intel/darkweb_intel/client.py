"""Intelligence X (intelx.io) API client.

Implements the subset of the public Intelligence X REST API needed for
third-party threat monitoring:

* ``POST /intelligent/search``        - start an asynchronous search
* ``GET  /intelligent/search/result`` - poll for results
* ``GET  /intelligent/search/terminate`` - stop a running search
* ``GET  /file/preview``              - fetch a short preview of a record
* ``GET  /file/read``                 - fetch full record content
* ``GET  /authenticate/info``         - account / quota information

Authentication is via the ``x-key`` request header. Free-tier keys use the
``https://free.intelx.io`` base URL; paid keys use ``https://2.intelx.io``.

See https://intelx.io/product for API access and documentation.
"""
from __future__ import annotations

import time
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Result status codes returned by /intelligent/search/result.
RESULT_SUCCESS = 0        # results returned, keep polling for more
RESULT_NO_MORE = 1        # search finished, no more results
RESULT_ID_NOT_FOUND = 2   # the search id has expired / is unknown
RESULT_EMPTY = 3          # no results yet, keep polling

# Sort orders accepted by /intelligent/search.
SORT_NONE = 0
SORT_XSCORE_ASC = 1
SORT_XSCORE_DESC = 2
SORT_DATE_ASC = 3
SORT_DATE_DESC = 4

DEFAULT_BASE_URL = "https://free.intelx.io"
PAID_BASE_URL = "https://2.intelx.io"

# A pragmatic default focused on genuinely threat-relevant sources rather than
# every public record. An empty list means "search all accessible buckets".
DARK_WEB_BUCKETS = [
    "darknet.tor",
    "darknet.i2p",
    "leaks.public.general",
    "leaks.public.wikileaks",
    "leaks.private.general",
    "leaks.logs",
    "pastes",
    "dumpster",
]


class IntelXError(Exception):
    """Raised for any Intelligence X API or transport failure."""


class IntelXClient:
    """Thin, retrying wrapper around the Intelligence X REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        user_agent: str = "darkweb-threat-intel/1.0",
        timeout: int = 60,
        retries: int = 4,
        backoff_factor: float = 1.0,
        proxy: str | None = None,
        verify: bool = True,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise IntelXError("An Intelligence X API key is required.")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

        self.session = session or requests.Session()
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            backoff_factor=backoff_factor,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update(
            {
                "x-key": api_key,
                "User-Agent": user_agent,
                "Accept": "application/json",
            }
        )
        self.verify = verify
        self.proxies = {"http": proxy, "https": proxy} if proxy else None

    # ------------------------------------------------------------------
    # Low-level request helpers
    # ------------------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=self.timeout,
                verify=self.verify,
                proxies=self.proxies,
            )
        except requests.RequestException as exc:  # network-level failure
            raise IntelXError(f"Request to {url} failed: {exc}") from exc

        if response.status_code == 401:
            raise IntelXError("Authentication failed (401): check your API key.")
        if response.status_code == 402:
            raise IntelXError("Payment required (402): API key quota exhausted.")
        if response.status_code >= 400:
            raise IntelXError(
                f"Intelligence X API error [{response.status_code}] on {path}: "
                f"{response.text[:500]}"
            )

        if not expect_json:
            return response.text

        # Some endpoints (empty result, terminate) legitimately return no body.
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise IntelXError(
                f"Unable to parse JSON from {path}: {response.text[:500]}"
            ) from exc

    # ------------------------------------------------------------------
    # Public API surface
    # ------------------------------------------------------------------
    def account_info(self) -> dict[str, Any]:
        """Return account / quota information for the configured API key."""
        return self._request("GET", "/authenticate/info")

    def search_start(
        self,
        term: str,
        *,
        max_results: int = 100,
        buckets: Iterable[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort: int = SORT_DATE_DESC,
        media: int = 0,
        timeout: int = 0,
    ) -> str:
        """Start a search and return the search id used to poll for results.

        ``term`` is the selector to search for — a company domain, email
        address, name, IP, or similar. ``date_from``/``date_to`` use the
        ``YYYY-MM-DD HH:MM:SS`` format expected by Intelligence X.
        """
        if not term or not term.strip():
            raise IntelXError("A non-empty search term is required.")

        body = {
            "term": term.strip(),
            "buckets": list(buckets) if buckets else [],
            "lookuplevel": 0,
            "maxresults": max_results,
            "timeout": timeout,
            "datefrom": date_from or "",
            "dateto": date_to or "",
            "sort": sort,
            "media": media,
            "terminate": [],
        }
        payload = self._request("POST", "/intelligent/search", json_body=body)
        status = payload.get("status")
        search_id = payload.get("id")
        if status == 1 or not search_id:
            raise IntelXError(f"Invalid search term '{term}' (status={status}).")
        if status == 2:
            raise IntelXError(f"Search error for term '{term}' (status={status}).")
        return search_id

    def search_result_page(
        self, search_id: str, *, limit: int = 100, preview_lines: int = 8
    ) -> dict[str, Any]:
        """Fetch a single page of results for an in-progress search."""
        params = {
            "id": search_id,
            "limit": limit,
            "statistics": 0,
            "previewlines": preview_lines,
        }
        return self._request("GET", "/intelligent/search/result", params=params)

    def search_terminate(self, search_id: str) -> None:
        """Terminate a running search to free server-side resources."""
        try:
            self._request(
                "GET",
                "/intelligent/search/terminate",
                params={"id": search_id},
                expect_json=False,
            )
        except IntelXError:
            # Terminate is best-effort cleanup; never mask the real error.
            pass

    def search(
        self,
        term: str,
        *,
        max_results: int = 100,
        buckets: Iterable[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        sort: int = SORT_DATE_DESC,
        media: int = 0,
        poll_interval: float = 1.0,
        max_polls: int = 30,
    ) -> list[dict[str, Any]]:
        """Run a complete search: start, poll to completion, return records.

        Blocks (with polite polling) until the search reports it is finished,
        the requested ``max_results`` is reached, or ``max_polls`` is hit.
        """
        search_id = self.search_start(
            term,
            max_results=max_results,
            buckets=buckets,
            date_from=date_from,
            date_to=date_to,
            sort=sort,
            media=media,
        )

        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        try:
            for poll in range(max_polls):
                page = self.search_result_page(search_id, limit=max_results)
                status = page.get("status", RESULT_EMPTY)
                for rec in page.get("records") or []:
                    key = rec.get("systemid") or rec.get("storageid") or id(rec)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                    records.append(rec)

                if len(records) >= max_results:
                    break
                if status in (RESULT_NO_MORE, RESULT_ID_NOT_FOUND):
                    break
                # status SUCCESS (more coming) or EMPTY (nothing yet): wait & retry.
                if poll < max_polls - 1:
                    time.sleep(poll_interval)
        finally:
            self.search_terminate(search_id)

        return records[:max_results]

    def read_preview(
        self, record: dict[str, Any], *, lines: int = 8
    ) -> str:
        """Fetch a short text preview for a search record."""
        params = {
            "sid": record.get("storageid"),
            "f": 0,
            "l": lines,
            "c": 1,
            "m": record.get("media", 0),
            "b": record.get("bucket", ""),
            "k": self.api_key,
        }
        return self._request("GET", "/file/preview", params=params, expect_json=False)

    def read_file(self, record: dict[str, Any]) -> str:
        """Fetch the full text content for a search record."""
        params = {
            "type": 0,
            "systemid": record.get("systemid"),
            "bucket": record.get("bucket", ""),
            "k": self.api_key,
        }
        return self._request("GET", "/file/read", params=params, expect_json=False)
