"""Integration tests for the global Credential API (Story 3.1).

Exercises:
  GET    /api/settings/credentials
  GET    /api/settings/credentials/guidance
  POST   /api/settings/credentials
  DELETE /api/settings/credentials/{credential_id}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from backend.api.credentials import CreateCredentialRequest

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _isolated_codeplane_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.config.get_codeplane_dir", lambda: tmp_path)


class TestListCredentials:
    @pytest.mark.asyncio
    async def test_empty_list_initially(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/credentials")
        assert resp.status_code == 200
        assert resp.json() == {"credentials": []}


class TestProviderGuidance:
    @pytest.mark.asyncio
    async def test_returns_guidance_for_supported_providers(self, client: AsyncClient) -> None:
        resp = await client.get("/api/settings/credentials/guidance")
        assert resp.status_code == 200
        guidance = resp.json()["guidance"]
        assert set(guidance) == {"github", "jira", "azure_devops", "copilot"}
        for text in guidance.values():
            assert isinstance(text, str)
            assert text

    @pytest.mark.asyncio
    async def test_github_guidance_names_fine_grained_pat_scopes(self, client: AsyncClient) -> None:
        """Story 3.5 AC1 (NFR9): fine-grained scopes for tracker writes and PR creation."""
        resp = await client.get("/api/settings/credentials/guidance")
        github_guidance = resp.json()["guidance"]["github"]
        assert "Issues: Read & write" in github_guidance
        assert "Contents: Read & write" in github_guidance
        assert "Pull requests: Read & write" in github_guidance

    @pytest.mark.asyncio
    async def test_jira_guidance_states_full_account_scope_and_approval_gate(self, client: AsyncClient) -> None:
        """Story 3.5 AC2 (NFR9): Jira tokens can't be scoped below the full account;
        the approval gate, not token scope, is the real security boundary."""
        resp = await client.get("/api/settings/credentials/guidance")
        jira_guidance = resp.json()["guidance"]["jira"]
        assert "full account" in jira_guidance
        assert "approval" in jira_guidance.lower()
        assert "security boundary" in jira_guidance

    @pytest.mark.asyncio
    async def test_azure_devops_guidance_states_org_scope_and_approval_gate(self, client: AsyncClient) -> None:
        """Story 3.5 AC2 (NFR9): Azure DevOps PATs are organization-scoped, not
        project-scoped; the approval gate is the real security boundary."""
        resp = await client.get("/api/settings/credentials/guidance")
        azure_guidance = resp.json()["guidance"]["azure_devops"]
        assert "organization-scoped" in azure_guidance
        assert "Work Items: Read & write" in azure_guidance
        assert "Code: Read & write" in azure_guidance
        assert "approval gate" in azure_guidance

    @pytest.mark.asyncio
    async def test_no_oauth_route_registered_on_credentials_router(self, app: FastAPI) -> None:
        """Story 3.5 AC3/NFR3: PAT-only — no OAuth app connection route exists."""
        credential_paths = [
            route.path for route in app.routes if getattr(route, "path", "").startswith("/api/settings/credentials")
        ]
        assert credential_paths, "expected credential routes to be registered"
        assert not any("oauth" in path.lower() for path in credential_paths)

    def test_create_credential_request_has_no_oauth_field(self) -> None:
        """Story 3.5 AC3/NFR3: the create-Credential schema is PAT-only."""
        field_names = set(CreateCredentialRequest.model_fields)
        assert not any("oauth" in name.lower() for name in field_names)
        assert field_names == {"provider", "label", "base_url", "pat", "email"}


class TestCreateCredential:
    @pytest.mark.asyncio
    async def test_create_returns_no_secret_field(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/credentials",
            json={
                "provider": "github",
                "label": "My GitHub Projects",
                "baseUrl": "https://api.github.com",
                "pat": "ghp_sentinel_value_should_never_appear",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["provider"] == "github"
        assert body["label"] == "My GitHub Projects"
        assert body["baseUrl"] == "https://api.github.com"
        assert "id" in body
        assert "createdAt" in body
        # No secret/pat field anywhere in the response body.
        assert "pat" not in body
        assert "secret" not in body
        assert "encryptedSecret" not in body

    @pytest.mark.asyncio
    async def test_created_credential_appears_in_list(self, client: AsyncClient) -> None:
        await client.post(
            "/api/settings/credentials",
            json={
                "provider": "jira",
                "label": "Jira",
                "baseUrl": "https://x.atlassian.net",
                "pat": "tok",
                "email": "dev@example.com",
            },
        )
        resp = await client.get("/api/settings/credentials")
        creds = resp.json()["credentials"]
        assert len(creds) == 1
        assert creds[0]["provider"] == "jira"
        assert creds[0]["email"] == "dev@example.com"
        assert "pat" not in creds[0]

    @pytest.mark.asyncio
    async def test_jira_requires_account_email(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/settings/credentials",
            json={
                "provider": "jira",
                "label": "Jira",
                "baseUrl": "https://x.atlassian.net",
                "pat": "tok",
            },
        )

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_unknown_provider(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/credentials",
            json={"provider": "bitbucket", "label": "x", "baseUrl": "https://x", "pat": "p"},
        )
        assert resp.status_code == 422


class TestRemediateLegacyJiraCredential:
    @pytest.mark.asyncio
    async def test_lists_remediation_and_updates_email_without_replacing_token(
        self, client: AsyncClient, session_factory: object
    ) -> None:
        from backend.persistence.credential_repo import CredentialRepository

        async with session_factory() as session:  # type: ignore[operator]
            repo = CredentialRepository(session)
            await repo.create(
                credential_id="legacy-jira",
                provider="jira",
                label="Legacy Jira",
                base_url="https://x.atlassian.net",
                pat="existing-token",
                email=None,
            )
            await session.commit()

        listed = (await client.get("/api/settings/credentials")).json()["credentials"]
        assert listed[0]["requiresEmailUpdate"] is True
        assert listed[0]["email"] is None
        assert "pat" not in listed[0]

        updated = await client.patch(
            "/api/settings/credentials/legacy-jira/jira-email",
            json={"email": "dev@example.com"},
        )

        assert updated.status_code == 200
        assert updated.json()["email"] == "dev@example.com"
        assert updated.json()["requiresEmailUpdate"] is False
        assert "pat" not in updated.json()
        async with session_factory() as session:  # type: ignore[operator]
            assert await CredentialRepository(session).resolve_secret("legacy-jira") == "existing-token"

    @pytest.mark.asyncio
    async def test_remediation_rejects_non_jira_and_invalid_email(self, client: AsyncClient) -> None:
        created = (
            await client.post(
                "/api/settings/credentials",
                json={
                    "provider": "github",
                    "label": "GitHub",
                    "baseUrl": "https://api.github.com",
                    "pat": "token",
                },
            )
        ).json()

        non_jira = await client.patch(
            f"/api/settings/credentials/{created['id']}/jira-email",
            json={"email": "dev@example.com"},
        )
        invalid = await client.patch(
            "/api/settings/credentials/missing/jira-email",
            json={"email": "not-an-email"},
        )

        assert non_jira.status_code == 409
        assert invalid.status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_empty_pat(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/settings/credentials",
            json={"provider": "github", "label": "x", "baseUrl": "https://x", "pat": ""},
        )
        assert resp.status_code == 422


class TestDeleteCredential:
    @pytest.mark.asyncio
    async def test_delete_succeeds_for_unreferenced_credential(self, client: AsyncClient) -> None:
        created = (
            await client.post(
                "/api/settings/credentials",
                json={"provider": "azure_devops", "label": "ADO", "baseUrl": "https://dev.azure.com/org", "pat": "p"},
            )
        ).json()

        resp = await client.delete(f"/api/settings/credentials/{created['id']}")
        assert resp.status_code == 204

        listed = (await client.get("/api/settings/credentials")).json()["credentials"]
        assert listed == []

    @pytest.mark.asyncio
    async def test_delete_returns_404_for_unknown_id(self, client: AsyncClient) -> None:
        resp = await client.delete("/api/settings/credentials/does-not-exist")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_blocked_while_referenced_by_tracker_link(
        self, client: AsyncClient, session_factory: object
    ) -> None:
        import uuid

        from backend.models.db import TrackerLinkRow

        created = (
            await client.post(
                "/api/settings/credentials",
                json={"provider": "github", "label": "GH", "baseUrl": "https://api.github.com", "pat": "p"},
            )
        ).json()

        async with session_factory() as session:  # type: ignore[operator]
            session.add(
                TrackerLinkRow(
                    id=str(uuid.uuid4()),
                    project_id="proj-1",
                    credential_id=created["id"],
                    external_ref="ORG/1",
                    created_at="2026-01-01T00:00:00Z",
                )
            )
            await session.commit()

        resp = await client.delete(f"/api/settings/credentials/{created['id']}")
        assert resp.status_code == 409

        # Still present.
        listed = (await client.get("/api/settings/credentials")).json()["credentials"]
        assert len(listed) == 1
