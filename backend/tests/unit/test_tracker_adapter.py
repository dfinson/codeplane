from __future__ import annotations

import json

import httpx
import pytest

from backend.services.tracker_adapter import (
    AzureDevOpsTrackerAdapter,
    GitHubProjectsTrackerAdapter,
    JiraTrackerAdapter,
    TrackerAdapterError,
    TrackerTicket,
)


@pytest.mark.asyncio
async def test_github_projects_normalizes_issue_and_draft_items() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/graphql"
        assert request.headers["authorization"] == "Bearer secret"
        payload = json.loads(request.content)
        assert payload["variables"] == {"owner": "acme", "number": 7}
        return httpx.Response(
            200,
            json={
                "data": {
                    "organization": {
                        "projectV2": {
                            "items": {
                                "nodes": [
                                    {
                                        "id": "item-1",
                                        "content": {
                                            "number": 12,
                                            "title": "Fix checkout",
                                            "url": "https://github.com/acme/shop/issues/12",
                                            "repository": {"nameWithOwner": "acme/shop"},
                                        },
                                        "status": {"name": "In progress"},
                                    },
                                    {
                                        "id": "item-2",
                                        "content": {"title": "Draft card"},
                                        "status": None,
                                    },
                                ]
                            }
                        }
                    },
                    "user": None,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        tickets = await GitHubProjectsTrackerAdapter(client).fetch_tickets(
            base_url="https://api.github.com",
            external_ref="acme/7",
            token="secret",
        )

    assert tickets == [
        TrackerTicket(
            id="acme/shop#12",
            title="Fix checkout",
            status="In progress",
            url="https://github.com/acme/shop/issues/12",
        ),
        TrackerTicket(id="item-2", title="Draft card", status="No status", url=None),
    ]


@pytest.mark.asyncio
async def test_github_projects_rejects_invalid_external_ref() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(TrackerAdapterError, match="owner/project-number"):
            await GitHubProjectsTrackerAdapter(client).fetch_tickets(
                base_url="https://api.github.com",
                external_ref="not-a-project",
                token="secret",
            )


@pytest.mark.asyncio
async def test_jira_normalizes_issue_search_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/3/search/jql"
        assert request.headers["authorization"] == "Basic ZGV2QGFjbWUudGVzdDpzZWNyZXQ="
        assert 'project = "PAY"' in request.url.params["jql"]
        return httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "key": "PAY-42",
                        "fields": {
                            "summary": "Retry settlement",
                            "status": {"name": "To Do"},
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tickets = await JiraTrackerAdapter(client).fetch_tickets(
            base_url="https://acme.atlassian.net/",
            external_ref="PAY",
            token="secret",
            email="dev@acme.test",
        )

    assert tickets == [
        TrackerTicket(
            id="PAY-42",
            title="Retry settlement",
            status="To Do",
            url="https://acme.atlassian.net/browse/PAY-42",
        )
    ]


@pytest.mark.asyncio
async def test_azure_devops_queries_then_fetches_work_items() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path.endswith("/_apis/wit/wiql"):
            return httpx.Response(200, json={"workItems": [{"id": 5}, {"id": 9}]})
        assert request.url.path.endswith("/_apis/wit/workitems")
        assert request.url.params["ids"] == "5,9"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 5,
                        "url": "https://dev.azure.com/acme/_apis/wit/workItems/5",
                        "fields": {"System.Title": "Ship API", "System.State": "Active"},
                    },
                    {
                        "id": 9,
                        "url": "https://dev.azure.com/acme/_apis/wit/workItems/9",
                        "fields": {"System.Title": "Write docs", "System.State": "New"},
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tickets = await AzureDevOpsTrackerAdapter(client).fetch_tickets(
            base_url="https://dev.azure.com/acme",
            external_ref="Payments",
            token="secret",
        )

    assert calls == ["/acme/Payments/_apis/wit/wiql", "/acme/Payments/_apis/wit/workitems"]
    assert [ticket.status for ticket in tickets] == ["Active", "New"]


@pytest.mark.asyncio
async def test_adapter_wraps_http_errors_without_exposing_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret rejected")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TrackerAdapterError) as exc_info:
            await JiraTrackerAdapter(client).fetch_tickets(
                base_url="https://acme.atlassian.net",
                external_ref="PAY",
                token="secret",
                email="dev@acme.test",
            )

    assert "secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_github_comment_performs_one_provider_write() -> None:
    requests: list[httpx.Request] = []
    body = "  Ready for review.\nRésumé ✅\r\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "id": 1,
                "html_url": "https://github.com/acme/shop/issues/42?access_token=secret#comment",
                "body": "secret",
                "authorization": "Bearer secret",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await GitHubProjectsTrackerAdapter(client).write(
            base_url="https://api.github.com",
            external_ref="acme/7",
            token="secret",
            ticket_ref="acme/shop#42",
            action="comment",
            value=body,
        )

    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/repos/acme/shop/issues/42/comments"
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert json.loads(requests[0].content) == {"body": body}
    assert receipt == {"id": "1", "url": "https://github.com/acme/shop/issues/42", "http_status": 201}
    assert "secret" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_jira_transition_reads_options_then_performs_one_write() -> None:
    methods: list[str] = []
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"transitions": [{"id": "31", "name": "Done"}]},
            )
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await JiraTrackerAdapter(client).write(
            base_url="https://acme.atlassian.net",
            external_ref="PAY",
            token="secret",
            ticket_ref="PAY-42",
            action="transition",
            value=" done ",
            email="dev@acme.test",
        )

    assert methods == ["GET", "POST"]
    assert all(request.headers["authorization"] == "Basic ZGV2QGFjbWUudGVzdDpzZWNyZXQ=" for request in requests)
    assert all(request.url.path == "/rest/api/3/issue/PAY-42/transitions" for request in requests)
    assert json.loads(requests[1].content) == {"transition": {"id": "31"}}
    assert receipt == {"http_status": 204}


@pytest.mark.asyncio
async def test_azure_transition_performs_one_provider_write() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 42, "url": "https://dev.azure.com/acme/_apis/wit/workItems/42"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await AzureDevOpsTrackerAdapter(client).write(
            base_url="https://dev.azure.com/acme",
            external_ref="Payments",
            token="secret",
            ticket_ref="42",
            action="transition",
            value="Closed",
        )

    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/acme/Payments/_apis/wit/workitems/42"
    assert requests[0].headers["content-type"] == "application/json-patch+json"
    assert json.loads(requests[0].content) == [{"op": "add", "path": "/fields/System.State", "value": "Closed"}]
    assert receipt == {
        "id": "42",
        "url": "https://dev.azure.com/acme/_apis/wit/workItems/42",
        "http_status": 200,
    }


async def test_github_done_changes_issue_state_not_project_status() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 42, "state": "closed"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await GitHubProjectsTrackerAdapter(client).write(
            base_url="https://api.github.com",
            external_ref="acme/7",
            token="secret",
            ticket_ref="acme/shop#42",
            action="transition",
            value="Done",
        )
    assert len(requests) == 1
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/repos/acme/shop/issues/42"
    assert json.loads(requests[0].content) == {"state": "closed"}
    assert receipt == {"id": "42", "http_status": 200}


@pytest.mark.parametrize("status, uncertain", [(401, False), (422, False), (429, False), (408, True), (500, True)])
async def test_provider_http_rejection_is_distinct_from_ambiguous_submission(status, uncertain) -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, text="provider included secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TrackerAdapterError) as caught:
            await GitHubProjectsTrackerAdapter(client).write(
                base_url="https://api.github.com",
                external_ref="acme/7",
                token="secret",
                ticket_ref="acme/shop#42",
                action="comment",
                value="Exact comment",
            )
    assert len(requests) == 1
    assert caught.value.delivery_uncertain is uncertain
    assert caught.value.code == ("provider_response_ambiguous" if uncertain else "provider_rejected")
    assert str(caught.value) == f"Tracker provider returned HTTP {status}"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "failure, uncertain",
    [
        (httpx.ReadTimeout, True),
        (httpx.WriteTimeout, True),
        (httpx.RemoteProtocolError, True),
        (httpx.ConnectTimeout, False),
    ],
)
async def test_write_transport_failure_preserves_delivery_uncertainty(failure, uncertain) -> None:
    submitted: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        submitted.append(json.loads(request.content)["body"])
        raise failure("secret in transport diagnostics", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TrackerAdapterError) as caught:
            await GitHubProjectsTrackerAdapter(client).write(
                base_url="https://api.github.com",
                external_ref="acme/7",
                token="secret",
                ticket_ref="acme/shop#42",
                action="comment",
                value="  No retry marker.\n",
            )
    assert submitted == ["  No retry marker.\n"]
    assert caught.value.delivery_uncertain is uncertain
    assert caught.value.code == ("transport_ambiguous" if uncertain else "connection_not_established")
    assert "secret" not in str(caught.value)


async def test_jira_transition_read_failure_is_definite_nondelivery() -> None:
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return httpx.Response(503, text="secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TrackerAdapterError) as caught:
            await JiraTrackerAdapter(client).write(
                base_url="https://acme.atlassian.net",
                external_ref="PAY",
                token="secret",
                ticket_ref="PAY-42",
                action="transition",
                value="Done",
                email="dev@acme.test",
            )
    assert methods == ["GET"]
    assert caught.value.delivery_uncertain is False
    assert caught.value.code == "pre_dispatch_read_failed"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("content", [b"", b"not-json"])
async def test_successful_acknowledgement_does_not_require_json_body(content) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, content=content)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await GitHubProjectsTrackerAdapter(client).write(
            base_url="https://api.github.com",
            external_ref="acme/7",
            token="secret",
            ticket_ref="acme/shop#42",
            action="comment",
            value="Posted",
        )
    assert receipt == {"http_status": 201}


async def test_jira_comment_preserves_exact_text_in_document_payload() -> None:
    requests: list[httpx.Request] = []
    body = "  Ready.\nRésumé ✅\r\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"id": "comment-7"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await JiraTrackerAdapter(client).write(
            base_url="https://acme.atlassian.net",
            external_ref="PAY",
            token="secret",
            ticket_ref="PAY-42",
            action="comment",
            value=body,
            email="dev@acme.test",
        )
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/rest/api/3/issue/PAY-42/comment"
    assert json.loads(requests[0].content) == {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}],
        },
    }
    assert receipt == {"id": "comment-7", "http_status": 201}


async def test_azure_comment_preserves_exact_text() -> None:
    requests: list[httpx.Request] = []
    body = "  Ready.\nRésumé ✅\r\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 7})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await AzureDevOpsTrackerAdapter(client).write(
            base_url="https://dev.azure.com/acme",
            external_ref="Payments",
            token="secret",
            ticket_ref="42",
            action="comment",
            value=body,
        )
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/acme/Payments/_apis/wit/workItems/42/comments"
    assert requests[0].url.params["api-version"] == "7.1-preview.4"
    assert json.loads(requests[0].content) == {"text": body}
    assert receipt == {"id": "7", "http_status": 200}
