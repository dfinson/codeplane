"""Provider-isolated tracker reads and evidence-classified explicit writes."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, urlparse

import httpx

if TYPE_CHECKING:
    from backend.models.schemas.tracker_operations import TrackerTargetKind


@dataclass(frozen=True)
class TrackerTicket:
    """Provider-neutral ticket state persisted by the tracker sync read model."""

    id: str
    title: str
    status: str
    url: str | None


class TrackerAdapterError(RuntimeError):
    """Provider error with explicit evidence about whether a write may have arrived."""

    def __init__(self, message: str, *, delivery_uncertain: bool = False, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.delivery_uncertain = delivery_uncertain
        self.code = code


class TrackerReferenceError(TrackerAdapterError):
    """Raised when a provider-specific external or ticket reference is invalid."""


def normalize_tracker_target(provider: str, action: str, value: str) -> tuple[TrackerTargetKind, str | None]:
    """Keep the existing provider mutation semantics explicit in approval and history."""
    if provider not in {"github", "jira", "azure_devops"}:
        raise TrackerAdapterError("Unsupported tracker provider", code="invalid_request")
    if action == "comment":
        return "comment", None
    if action != "transition":
        raise TrackerAdapterError("Unsupported tracker write action", code="invalid_request")
    if provider == "github":
        state = {
            "open": "open",
            "reopen": "open",
            "reopened": "open",
            "close": "closed",
            "closed": "closed",
            "done": "closed",
        }.get(value.strip().lower())
        if state is None:
            raise TrackerAdapterError("GitHub transition must target open or closed", code="invalid_request")
        return "github_issue_state", state
    if provider == "jira":
        return "jira_workflow_transition", value.strip()
    return "azure_devops_work_item_state", value


def _safe_tracker_url(value: str) -> str | None:
    try:
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        return parsed._replace(query="", fragment="").geturl()
    except ValueError:
        return None


def sanitize_tracker_text(value: str, *, limit: int = 1000) -> str:
    """Bound diagnostic/operator text; provider exception causes are never serialized."""
    value = re.sub(
        r"(?i)\b(authorization|password|secret|token|pat|api[_-]?key)\b\s*[:=]\s*\S+",
        r"\1=[redacted]",
        value,
    )
    value = re.sub(r"(?i)\b(bearer|basic)\s+\S+", r"\1 [redacted]", value)
    value = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "[redacted]", value)
    value = re.sub(r"\b[A-Za-z0-9_+/=-]{32,}\b", "[redacted]", value)
    value = re.sub(
        r"https?://[^\s<>]+",
        lambda match: _safe_tracker_url(match.group()) or "[redacted URL]",
        value,
    )
    return "".join(char for char in value if char in "\n\t" or ord(char) >= 32)[:limit]


def safe_tracker_receipt(payload: dict[str, Any]) -> dict[str, str | int]:
    receipt: dict[str, str | int] = {}
    identifier = payload.get("id")
    if isinstance(identifier, (str, int)) and re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", str(identifier)):
        receipt["id"] = str(identifier)
    url = payload.get("html_url") or payload.get("url") or payload.get("self")
    if isinstance(url, str):
        safe_url = _safe_tracker_url(url)
        if safe_url:
            receipt["url"] = safe_url
    status = payload.get("http_status")
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        receipt["http_status"] = status
    return receipt


class TrackerAdapterInterface(Protocol):
    async def test_connection(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        email: str | None = None,
    ) -> None: ...

    async def fetch_tickets(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        email: str | None = None,
    ) -> list[TrackerTicket]: ...

    async def write(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        ticket_ref: str,
        action: str,
        value: str,
        email: str | None = None,
    ) -> dict[str, str | int] | None: ...


class _HttpTrackerAdapter:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TrackerAdapterError("Tracker provider read failed", code="pre_dispatch_read_failed") from exc
        if not isinstance(payload, dict):
            raise TrackerAdapterError("Tracker provider returned an invalid response")
        return payload

    async def _request_no_content(self, method: str, url: str, **kwargs: Any) -> dict[str, str | int]:
        try:
            response = await self._client.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            definite = 400 <= status < 500 and status != 408
            raise TrackerAdapterError(
                f"Tracker provider returned HTTP {status}",
                delivery_uncertain=not definite,
                code="provider_rejected" if definite else "provider_response_ambiguous",
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise TrackerAdapterError(
                "Tracker connection was not established",
                code="connection_not_established",
            ) from exc
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise TrackerAdapterError("Tracker provider URL is invalid", code="invalid_request") from exc
        except httpx.HTTPError as exc:
            raise TrackerAdapterError(
                "Tracker submission may have reached the provider",
                delivery_uncertain=True,
                code="transport_ambiguous",
            ) from exc
        receipt: dict[str, str | int] = {"http_status": response.status_code}
        # A successful HTTP acknowledgement remains evidence even without a JSON response body.
        if response.content:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                receipt.update(safe_tracker_receipt(payload))
        return receipt

    async def test_connection(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        email: str | None = None,
    ) -> None:
        await self.fetch_tickets(  # type: ignore[attr-defined]
            base_url=base_url,
            external_ref=external_ref,
            token=token,
            email=email,
        )


class GitHubProjectsTrackerAdapter(_HttpTrackerAdapter):
    """Read GitHub Projects v2 items through the GraphQL API."""

    _QUERY = """
    query TrackerProject($owner: String!, $number: Int!) {
      organization(login: $owner) {
        projectV2(number: $number) {
          items(first: 100) {
            nodes {
              id
              content {
                ... on Issue { number title url repository { nameWithOwner } }
                ... on PullRequest { number title url repository { nameWithOwner } }
                ... on DraftIssue { title }
              }
              status: fieldValueByName(name: "Status") {
                ... on ProjectV2ItemFieldSingleSelectValue { name }
              }
            }
          }
        }
      }
      user(login: $owner) {
        projectV2(number: $number) {
          items(first: 100) {
            nodes {
              id
              content {
                ... on Issue { number title url repository { nameWithOwner } }
                ... on PullRequest { number title url repository { nameWithOwner } }
                ... on DraftIssue { title }
              }
              status: fieldValueByName(name: "Status") {
                ... on ProjectV2ItemFieldSingleSelectValue { name }
              }
            }
          }
        }
      }
    }
    """

    async def fetch_tickets(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        email: str | None = None,
    ) -> list[TrackerTicket]:
        del email
        owner, separator, raw_number = external_ref.partition("/")
        if not separator or not owner or not raw_number.isdigit():
            raise TrackerReferenceError("GitHub external ref must use owner/project-number")

        payload = await self._request_json(
            "POST",
            f"{base_url.rstrip('/')}/graphql",
            headers={"Authorization": "Bearer " + token},
            json={
                "query": self._QUERY,
                "variables": {"owner": owner, "number": int(raw_number)},
            },
        )
        data = payload.get("data")
        errors = payload.get("errors")
        if errors:
            if not isinstance(data, dict):
                raise TrackerAdapterError("GitHub Projects query failed")
            owner_field = "organization" if isinstance(data.get("organization"), dict) else "user"
            missing_field = "user" if owner_field == "organization" else "organization"
            # Only the unmatched owner lookup may fail, never the selected project's fields.
            if (
                not isinstance(data.get(owner_field), dict)
                or data.get(missing_field) is not None
                or not isinstance(errors, list)
                or any(
                    not isinstance(error, dict)
                    or error.get("type") != "NOT_FOUND"
                    or error.get("path") != [missing_field]
                    for error in errors
                )
            ):
                raise TrackerAdapterError("GitHub Projects query failed")
        if not isinstance(data, dict):
            raise TrackerAdapterError("GitHub Projects returned an invalid response")
        owner_data = data.get("organization") or data.get("user")
        if not isinstance(owner_data, dict):
            raise TrackerAdapterError("GitHub Project was not found")
        try:
            nodes = owner_data["projectV2"]["items"]["nodes"]
        except (KeyError, TypeError) as exc:
            raise TrackerAdapterError("GitHub Project was not found") from exc
        if not isinstance(nodes, list):
            raise TrackerAdapterError("GitHub Projects returned invalid items")

        tickets: list[TrackerTicket] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            content = node.get("content")
            if not isinstance(content, dict):
                continue
            status = node.get("status")
            number = content.get("number")
            repository = content.get("repository")
            name_with_owner = repository.get("nameWithOwner") if isinstance(repository, dict) else None
            if not name_with_owner and number and content.get("url"):
                parts = urlparse(str(content["url"])).path.strip("/").split("/")
                if len(parts) >= 4 and parts[-2] in {"issues", "pull"}:
                    name_with_owner = "/".join(parts[:2])
            ticket_id = (
                f"{name_with_owner}#{number}" if name_with_owner and number else str(number or node.get("id") or "")
            )
            tickets.append(
                TrackerTicket(
                    id=ticket_id,
                    title=str(content.get("title") or "Untitled"),
                    status=str(status.get("name") if isinstance(status, dict) else "No status"),
                    url=str(content["url"]) if content.get("url") else None,
                )
            )
        return tickets

    async def write(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        ticket_ref: str,
        action: str,
        value: str,
        email: str | None = None,
    ) -> dict[str, str | int] | None:
        del email
        del external_ref
        match = re.fullmatch(r"([^/\s]+)/([^#/\s]+)#(\d+)", ticket_ref)
        if match is None:
            raise TrackerReferenceError("GitHub ticket ref must use owner/repository#number")
        owner, repo, issue_number = match.groups()
        issue_url = f"{base_url.rstrip('/')}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/issues/{issue_number}"
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        }
        if action == "comment":
            return await self._request_no_content(
                "POST",
                f"{issue_url}/comments",
                headers=headers,
                json={"body": value},
            )
        if action == "transition":
            _, state = normalize_tracker_target("github", action, value)
            return await self._request_no_content(
                "PATCH",
                issue_url,
                headers=headers,
                json={"state": state},
            )
        raise TrackerAdapterError(f"Unsupported tracker write action: {action}")


class JiraTrackerAdapter(_HttpTrackerAdapter):
    """Read Jira project issues through the REST API v3 enhanced search."""

    async def fetch_tickets(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        email: str | None = None,
    ) -> list[TrackerTicket]:
        if re.fullmatch(r"[A-Z][A-Z0-9_]{1,19}", external_ref) is None:
            raise TrackerReferenceError("Jira external ref must be an uppercase project key")
        escaped_ref = external_ref.replace("\\", "\\\\").replace('"', '\\"')
        if not email:
            raise TrackerAdapterError("Jira credential is missing an account email")
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        payload = await self._request_json(
            "GET",
            f"{base_url.rstrip('/')}/rest/api/3/search/jql",
            headers={"Authorization": "Basic " + auth, "Accept": "application/json"},
            params={
                "jql": f'project = "{escaped_ref}" ORDER BY updated DESC',
                "fields": "summary,status",
                "maxResults": 100,
            },
        )
        issues = payload.get("issues")
        if not isinstance(issues, list):
            raise TrackerAdapterError("Jira returned invalid issues")

        tickets: list[TrackerTicket] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            fields = issue.get("fields")
            if not isinstance(fields, dict):
                continue
            key = str(issue.get("key") or issue.get("id") or "")
            status = fields.get("status")
            tickets.append(
                TrackerTicket(
                    id=key,
                    title=str(fields.get("summary") or "Untitled"),
                    status=str(status.get("name") if isinstance(status, dict) else "Unknown"),
                    url=f"{base_url.rstrip('/')}/browse/{quote(key, safe='-')}",
                )
            )
        return tickets

    async def write(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        ticket_ref: str,
        action: str,
        value: str,
        email: str | None = None,
    ) -> dict[str, str | int] | None:
        del external_ref
        if re.fullmatch(r"[A-Z][A-Z0-9_]*-\d+", ticket_ref) is None:
            raise TrackerReferenceError("Jira ticket ref must use PROJECT-123")
        issue_url = f"{base_url.rstrip('/')}/rest/api/3/issue/{quote(ticket_ref, safe='-')}"
        if not email:
            raise TrackerAdapterError("Jira credential is missing an account email")
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        headers = {"Authorization": "Basic " + auth, "Accept": "application/json"}
        if action == "comment":
            return await self._request_no_content(
                "POST",
                f"{issue_url}/comment",
                headers=headers,
                json={
                    "body": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": value}],
                            }
                        ],
                    }
                },
            )
        if action == "transition":
            payload = await self._request_json(
                "GET",
                f"{issue_url}/transitions",
                headers=headers,
            )
            transitions = payload.get("transitions")
            if not isinstance(transitions, list):
                raise TrackerAdapterError("Jira returned invalid transitions")
            transition_id = next(
                (
                    str(item["id"])
                    for item in transitions
                    if isinstance(item, dict)
                    and item.get("id") is not None
                    and str(item.get("name", "")).casefold() == value.strip().casefold()
                ),
                None,
            )
            if transition_id is None:
                raise TrackerAdapterError("Requested Jira transition is unavailable")
            return await self._request_no_content(
                "POST",
                f"{issue_url}/transitions",
                headers=headers,
                json={"transition": {"id": transition_id}},
            )
        raise TrackerAdapterError(f"Unsupported tracker write action: {action}")


class AzureDevOpsTrackerAdapter(_HttpTrackerAdapter):
    """Read Azure DevOps work items using WIQL 7.1."""

    async def fetch_tickets(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        email: str | None = None,
    ) -> list[TrackerTicket]:
        del email
        if not external_ref.strip() or any(char in external_ref for char in "\\/#?"):
            raise TrackerReferenceError("Azure DevOps external ref must be a project name")
        auth = base64.b64encode(f":{token}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        }
        project_url = f"{base_url.rstrip('/')}/{quote(external_ref, safe='')}"
        query_result = await self._request_json(
            "POST",
            f"{project_url}/_apis/wit/wiql",
            headers=headers,
            params={"api-version": "7.1", "$top": 100},
            json={
                "query": (
                    "SELECT [System.Id], [System.Title], [System.State] "
                    "FROM WorkItems ORDER BY [System.ChangedDate] DESC"
                )
            },
        )
        references = query_result.get("workItems")
        if not isinstance(references, list):
            raise TrackerAdapterError("Azure DevOps returned invalid work item references")
        ids = [str(item["id"]) for item in references if isinstance(item, dict) and item.get("id") is not None]
        if not ids:
            return []

        item_result = await self._request_json(
            "GET",
            f"{project_url}/_apis/wit/workitems",
            headers=headers,
            params={
                "ids": ",".join(ids),
                "fields": "System.Id,System.Title,System.State",
                "api-version": "7.1",
            },
        )
        items = item_result.get("value")
        if not isinstance(items, list):
            raise TrackerAdapterError("Azure DevOps returned invalid work items")

        tickets: list[TrackerTicket] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            tickets.append(
                TrackerTicket(
                    id=str(item.get("id") or ""),
                    title=str(fields.get("System.Title") or "Untitled"),
                    status=str(fields.get("System.State") or "Unknown"),
                    url=str(item["url"]) if item.get("url") else None,
                )
            )
        return tickets

    async def write(
        self,
        *,
        base_url: str,
        external_ref: str,
        token: str,
        ticket_ref: str,
        action: str,
        value: str,
        email: str | None = None,
    ) -> dict[str, str | int] | None:
        del email
        if not ticket_ref.isdigit():
            raise TrackerReferenceError("Azure DevOps ticket ref must be a numeric work item ID")
        auth = base64.b64encode(f":{token}".encode()).decode()
        project_url = f"{base_url.rstrip('/')}/{quote(external_ref, safe='')}"
        headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
        if action == "comment":
            return await self._request_no_content(
                "POST",
                f"{project_url}/_apis/wit/workItems/{ticket_ref}/comments",
                headers=headers,
                params={"api-version": "7.1-preview.4"},
                json={"text": value},
            )
        if action == "transition":
            return await self._request_no_content(
                "PATCH",
                f"{project_url}/_apis/wit/workitems/{ticket_ref}",
                headers={**headers, "Content-Type": "application/json-patch+json"},
                params={"api-version": "7.1"},
                json=[{"op": "add", "path": "/fields/System.State", "value": value}],
            )
        raise TrackerAdapterError(f"Unsupported tracker write action: {action}")


def build_tracker_adapters(client: httpx.AsyncClient) -> dict[str, TrackerAdapterInterface]:
    return {
        "github": GitHubProjectsTrackerAdapter(client),
        "jira": JiraTrackerAdapter(client),
        "azure_devops": AzureDevOpsTrackerAdapter(client),
    }
