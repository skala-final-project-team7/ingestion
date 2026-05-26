from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from data_ingestion_agent.config import DataIngestionConfig
from data_ingestion_agent.confluence import (
    ConfluenceApiError,
    ConfluenceClient,
    ConfluenceRequest,
    ConfluenceResponse,
)


class FakeTransport:
    def __init__(self, responses: list[ConfluenceResponse | TimeoutError]) -> None:
        self.responses = responses
        self.requests: list[ConfluenceRequest] = []

    def send(self, request: ConfluenceRequest) -> ConfluenceResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, TimeoutError):
            raise response
        return response


def _runtime_value(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _client(
    tmp_path: Path,
    responses: list[ConfluenceResponse | TimeoutError],
    *,
    max_retries: int = 3,
    access_token: str | None = None,
) -> tuple[ConfluenceClient, FakeTransport, str, str]:
    cloud_id = _runtime_value("cloud")
    access_token = access_token or _runtime_value("token")
    config = DataIngestionConfig(
        cloud_id=cloud_id,
        access_token=access_token,
        output_dir=tmp_path,
        max_retries=max_retries,
        request_delay_seconds=0,
    )
    transport = FakeTransport(responses)
    return (
        ConfluenceClient(config=config, transport=transport, sleeper=lambda _: None),
        transport,
        cloud_id,
        access_token,
    )


def test_base_url_uses_injected_cloud_id(tmp_path: Path) -> None:
    client, _, cloud_id, _ = _client(tmp_path, [])

    assert (
        client.base_url
        == f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2"
    )


def test_authorization_header_is_sent_but_redacted_from_errors(
    tmp_path: Path,
) -> None:
    access_token = _runtime_value("token")
    client, transport, _, access_token = _client(
        tmp_path,
        [
            ConfluenceResponse(
                status_code=401,
                json_body={"message": f"denied {access_token}"},
            )
        ],
        access_token=access_token,
    )

    with pytest.raises(ConfluenceApiError) as raised_error:
        client.list_spaces()

    assert transport.requests[0].headers["Authorization"] == f"Bearer {access_token}"
    assert access_token not in str(raised_error.value)
    assert "Authorization" not in str(raised_error.value)


def test_list_spaces_follows_next_pagination(tmp_path: Path) -> None:
    client, transport, _, _ = _client(
        tmp_path,
        [
            ConfluenceResponse(
                status_code=200,
                json_body={
                    "results": [{"id": "space-1"}],
                    "_links": {"next": "/spaces?cursor=next-cursor"},
                },
            ),
            ConfluenceResponse(
                status_code=200,
                json_body={"results": [{"id": "space-2"}], "_links": {}},
            ),
        ],
    )

    spaces = client.list_spaces()

    assert spaces == [{"id": "space-1"}, {"id": "space-2"}]
    assert transport.requests[0].url.endswith("/spaces?limit=25")
    assert transport.requests[1].url.endswith("/spaces?cursor=next-cursor")


def test_list_page_descendants_follows_next_pagination(tmp_path: Path) -> None:
    homepage_id = _runtime_value("homepage")
    client, transport, _, _ = _client(
        tmp_path,
        [
            ConfluenceResponse(
                status_code=200,
                json_body={
                    "results": [{"id": "page-1"}],
                    "_links": {"next": f"/pages/{homepage_id}/descendants?cursor=2"},
                },
            ),
            ConfluenceResponse(
                status_code=200,
                json_body={"results": [{"id": "page-2"}], "_links": {}},
            ),
        ],
    )

    descendants = client.list_page_descendants(homepage_id)

    assert descendants == [{"id": "page-1"}, {"id": "page-2"}]
    assert transport.requests[0].url.endswith(
        f"/pages/{homepage_id}/descendants?limit=25"
    )
    assert transport.requests[1].url.endswith(
        f"/pages/{homepage_id}/descendants?cursor=2"
    )


def test_get_page_detail_requests_storage_body_and_version(
    tmp_path: Path,
) -> None:
    page_id = _runtime_value("page")
    client, transport, _, _ = _client(
        tmp_path,
        [
            ConfluenceResponse(
                status_code=200,
                json_body={"id": page_id, "body": {"storage": {"value": "<p>x</p>"}}},
            )
        ],
    )

    page_detail = client.get_page_detail(page_id)

    assert page_detail["id"] == page_id
    assert transport.requests[0].url.endswith(
        f"/pages/{page_id}?body-format=storage&include-version=true"
    )


def test_rate_limit_retries_then_succeeds(tmp_path: Path) -> None:
    client, transport, _, _ = _client(
        tmp_path,
        [
            ConfluenceResponse(status_code=429, json_body={"message": "rate limited"}),
            ConfluenceResponse(status_code=200, json_body={"results": [], "_links": {}}),
        ],
    )

    assert client.list_spaces() == []
    assert len(transport.requests) == 2


def test_server_error_retries_then_succeeds(tmp_path: Path) -> None:
    client, transport, _, _ = _client(
        tmp_path,
        [
            ConfluenceResponse(status_code=503, json_body={"message": "unavailable"}),
            ConfluenceResponse(status_code=200, json_body={"results": [], "_links": {}}),
        ],
    )

    assert client.list_spaces() == []
    assert len(transport.requests) == 2


def test_timeout_retries_then_succeeds(tmp_path: Path) -> None:
    client, transport, _, _ = _client(
        tmp_path,
        [
            TimeoutError("request timed out"),
            ConfluenceResponse(status_code=200, json_body={"results": [], "_links": {}}),
        ],
    )

    assert client.list_spaces() == []
    assert len(transport.requests) == 2


def test_unauthorized_is_auth_error_without_retry(tmp_path: Path) -> None:
    client, transport, _, _ = _client(
        tmp_path,
        [ConfluenceResponse(status_code=401, json_body={"message": "unauthorized"})],
    )

    with pytest.raises(ConfluenceApiError) as raised_error:
        client.list_spaces()

    assert raised_error.value.error_type == "auth_failure"
    assert raised_error.value.retryable is False
    assert raised_error.value.item_level is False
    assert raised_error.value.attempt_count == 1
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(403, "permission_failure"), (404, "item_not_found")],
)
def test_item_level_errors_are_classified(
    tmp_path: Path,
    status_code: int,
    error_type: str,
) -> None:
    client, _, _, _ = _client(
        tmp_path,
        [ConfluenceResponse(status_code=status_code, json_body={"message": "failed"})],
    )

    with pytest.raises(ConfluenceApiError) as raised_error:
        client.get_page_detail(_runtime_value("page"))

    assert raised_error.value.status_code == status_code
    assert raised_error.value.error_type == error_type
    assert raised_error.value.retryable is False
    assert raised_error.value.item_level is True


def test_max_retries_exceeded_raises_safe_retryable_error(
    tmp_path: Path,
) -> None:
    access_token = _runtime_value("token")
    client, transport, _, access_token = _client(
        tmp_path,
        [
            ConfluenceResponse(status_code=503, json_body={"message": access_token}),
            ConfluenceResponse(status_code=503, json_body={"message": access_token}),
        ],
        max_retries=1,
        access_token=access_token,
    )

    with pytest.raises(ConfluenceApiError) as raised_error:
        client.list_spaces()

    assert raised_error.value.error_type == "retry_exhausted"
    assert raised_error.value.retryable is True
    assert raised_error.value.attempt_count == 2
    assert len(transport.requests) == 2
    assert access_token not in str(raised_error.value)
