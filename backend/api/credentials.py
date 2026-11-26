"""Global integration Credential API (Story 3.1, CAP-6/CAP-7).

Register once, in Settings > Integrations, independent of any Project
(AD-6). No route in this module ever returns a decrypted or encrypted
secret — list/get/create responses expose only provider/label/base_url/id/
created_at.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Literal

import structlog
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.config import CPLConfig
from backend.models.schemas.base import CamelModel
from backend.persistence.credential_repo import CredentialReferencedError, CredentialRepository
from backend.services.events.export_binding import (
    ExportConfigurationError,
    destination_scope,
    encode_export_credential,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


class CredentialRoute(DishkaRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def without_secret_inputs(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Missing-field errors can carry the entire credential body.
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {"type": error["type"], "loc": error["loc"], "msg": error["msg"]} for error in exc.errors()
                        ]
                    },
                )

        return without_secret_inputs


router = APIRouter(prefix="/settings/credentials", tags=["credentials"], route_class=CredentialRoute)
log = structlog.get_logger()

# NFR9: per-provider PAT scope guidance, copy-paste only — never validated/enforced
# against TrackerLinks, since a Credential is deliberately global and reusable.
_PROVIDER_GUIDANCE: dict[str, str] = {
    "copilot": (
        "Use a GitHub token authorized for Copilot inference. Stored encrypted; never returned. "
        "The label is not a verified billing identity. Selecting this credential applies to "
        "managed Copilot jobs and their helpers only, not Git, gh, or tracker authentication. "
        "A missing, revoked, or unauthorized selected credential fails without ambient fallback."
    ),
    "otlp": (
        "Use only destination-scoped transport-auth headers as a JSON object. "
        "Choose the exact collector endpoint and protocol. The encrypted credential "
        "cannot authorize ingestion, control or tracker operations."
    ),
    "github": (
        "Use a fine-grained personal access token. Scope it to the repos you need: "
        "'Issues: Read & write' for tracker writes; add 'Contents: Read & write' + "
        "'Pull requests: Read & write' if PR creation will also be used. A scope broader "
        "than any single TrackerLink is expected, since this Credential may be attached "
        "to multiple Projects over time."
    ),
    "jira": (
        "Jira API tokens cannot be scoped down further than the full account — the token "
        "inherits every permission the account has. The codeplane_approval write-back gate, "
        "not token scope, is the real security boundary here."
    ),
    "azure_devops": (
        "Azure DevOps personal access tokens are organization-scoped, not project-scoped: "
        "scope to 'Work Items: Read & write' for tracker writes and 'Code: Read & write' for "
        "PR creation. One token covers every project in that org regardless of which project "
        "a TrackerLink references — again, the approval gate is the actual security boundary."
    ),
}


class CredentialResponse(CamelModel):
    id: str
    provider: str
    label: str
    base_url: str
    email: str | None
    requires_email_update: bool
    created_at: str


class CredentialListResponse(CamelModel):
    credentials: list[CredentialResponse] = Field(default_factory=list)


class CreateCredentialRequest(CamelModel):
    provider: Literal["github", "jira", "azure_devops", "otlp", "copilot"]
    label: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    pat: str = Field(min_length=1)
    email: str | None = None
    export_protocol: str | None = None

    @model_validator(mode="after")
    def validate_jira_email(self) -> CreateCredentialRequest:
        if self.email is not None:
            self.email = self.email.strip() or None
        if self.provider == "jira" and (
            self.email is None or "@" not in self.email or self.email.startswith("@") or self.email.endswith("@")
        ):
            raise ValueError("Jira credentials require the account email used to create the API token")
        return self


class UpdateJiraCredentialRequest(CamelModel):
    email: str = Field(min_length=3)

    @model_validator(mode="after")
    def validate_email(self) -> UpdateJiraCredentialRequest:
        self.email = self.email.strip()
        if "@" not in self.email or self.email.startswith("@") or self.email.endswith("@"):
            raise ValueError("Enter the Jira account email used to create the API token")
        return self


class ProviderGuidanceResponse(CamelModel):
    guidance: dict[str, str] = Field(default_factory=dict)


def _to_response(data: dict[str, Any]) -> CredentialResponse:
    return CredentialResponse(
        **data,
        requires_email_update=data["provider"] == "jira" and not data["email"],
    )


@router.get("", response_model=CredentialListResponse)
async def list_credentials(sf: FromDishka[async_sessionmaker[AsyncSession]]) -> CredentialListResponse:
    async with sf() as session:
        repo = CredentialRepository(session)
        rows = await repo.list_all()
    return CredentialListResponse(credentials=[_to_response(r) for r in rows])


@router.get("/guidance", response_model=ProviderGuidanceResponse)
async def get_provider_guidance() -> ProviderGuidanceResponse:
    """Return per-provider PAT scope guidance (NFR9). Static, no DB access."""
    return ProviderGuidanceResponse(guidance=_PROVIDER_GUIDANCE)


@router.post("", response_model=CredentialResponse, status_code=201)
async def create_credential(
    body: CreateCredentialRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> CredentialResponse:
    secret = body.pat
    if body.provider == "otlp":
        try:
            destination_scope(body.base_url, body.export_protocol or "")
            secret = encode_export_credential(body.export_protocol or "", body.pat)
        except ExportConfigurationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif body.export_protocol is not None:
        raise HTTPException(status_code=400, detail="Export protocol is only valid for OTLP credentials")
    credential_id = str(uuid.uuid4())
    async with sf() as session:
        repo = CredentialRepository(session)
        result = await repo.create(
            credential_id=credential_id,
            provider=body.provider,
            label=body.label,
            base_url=body.base_url,
            pat=secret,
            email=body.email,
        )
        await session.commit()
    # Never log the PAT itself — only non-secret identifying fields (NFR1).
    log.info("credential.created", credential_id=credential_id, provider=body.provider, label=body.label)
    return _to_response(result)


@router.delete("/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: str,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
    config: FromDishka[CPLConfig],
) -> None:
    if credential_id in {config.export.credential_id, config.export.metrics_credential_id}:
        raise HTTPException(status_code=409, detail="Credential is still referenced by the export binding")
    async with sf() as session:
        repo = CredentialRepository(session)
        try:
            deleted = await repo.delete(credential_id)
        except CredentialReferencedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Credential not found")
        await session.commit()
    log.info("credential.deleted", credential_id=credential_id)


@router.patch("/{credential_id}/jira-email", response_model=CredentialResponse)
async def update_jira_credential_email(
    credential_id: str,
    body: UpdateJiraCredentialRequest,
    sf: FromDishka[async_sessionmaker[AsyncSession]],
) -> CredentialResponse:
    """Remediate a legacy Jira credential without reading or replacing its token."""
    async with sf() as session:
        repo = CredentialRepository(session)
        credential = await repo.get(credential_id)
        if credential is None:
            raise HTTPException(status_code=404, detail="Credential not found")
        if credential["provider"] != "jira":
            raise HTTPException(status_code=409, detail="Only Jira credentials have an account email")
        updated = await repo.update_email(credential_id, body.email)
        await session.commit()
    assert updated is not None
    log.info("credential.jira_email_updated", credential_id=credential_id)
    return _to_response(updated)
