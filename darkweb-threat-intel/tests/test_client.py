import json

import pytest

from darkweb_intel.client import IntelXClient, IntelXError, RESULT_SUCCESS, RESULT_NO_MORE


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records calls and replays a scripted queue of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.headers = {}
        self.calls = []

    def mount(self, *_):
        pass

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self._responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self._responses.pop(0)


def _client(responses):
    session = FakeSession(responses)
    client = IntelXClient(api_key="test-key", session=session)
    return client, session


def test_requires_api_key():
    with pytest.raises(IntelXError):
        IntelXClient(api_key="")


def test_search_paginates_and_dedupes():
    responses = [
        FakeResponse(payload={"id": "sid-1", "status": 0}),                       # search_start
        FakeResponse(payload={"status": RESULT_SUCCESS, "records": [             # page 1
            {"systemid": "a", "bucket": "pastes", "name": "one"},
            {"systemid": "b", "bucket": "darknet.tor", "name": "two"},
        ]}),
        FakeResponse(payload={"status": RESULT_NO_MORE, "records": [             # page 2 (dupe + done)
            {"systemid": "a", "bucket": "pastes", "name": "one"},
        ]}),
        FakeResponse(status_code=200, text=""),                                  # terminate
    ]
    client, session = _client(responses)
    records = client.search("acme.com", max_results=100, poll_interval=0)
    ids = sorted(r["systemid"] for r in records)
    assert ids == ["a", "b"]
    # x-key header is set on the session
    assert session.headers["x-key"] == "test-key"


def test_search_start_rejects_invalid_term():
    responses = [FakeResponse(payload={"id": "", "status": 1})]
    client, _ = _client(responses)
    with pytest.raises(IntelXError):
        client.search_start("bad")


def test_http_error_raises():
    responses = [FakeResponse(status_code=401, text="unauthorized")]
    client, _ = _client(responses)
    with pytest.raises(IntelXError):
        client.account_info()


def test_search_respects_max_results():
    responses = [
        FakeResponse(payload={"id": "sid-2", "status": 0}),
        FakeResponse(payload={"status": RESULT_SUCCESS, "records": [
            {"systemid": "a", "bucket": "pastes"},
            {"systemid": "b", "bucket": "pastes"},
            {"systemid": "c", "bucket": "pastes"},
        ]}),
        FakeResponse(status_code=200, text=""),  # terminate
    ]
    client, _ = _client(responses)
    records = client.search("acme.com", max_results=2, poll_interval=0)
    assert len(records) == 2
