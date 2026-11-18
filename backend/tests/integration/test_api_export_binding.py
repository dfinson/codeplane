import json

import pytest

from backend.config import CPLConfig, ExportConfig
from backend.persistence.credential_repo import CredentialRepository
from backend.persistence.project_repo import ProjectRepository
from backend.services.events.export_binding import ExportConfigurationError, resolve_binding


@pytest.fixture(autouse=True)
def isolate_key(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.config.get_codeplane_dir", lambda: tmp_path)


async def test_export_credential_is_encrypted_destination_scoped_and_not_a_tracker(
    app,
    client,
    session_factory,
    mock_tracker_sync_service,
):
    secret = "local-export-fixture-secret"
    response = await client.post(
        "/api/settings/credentials",
        json={
            "provider": "otlp",
            "label": "local export",
            "baseUrl": "https://collector.example.test/v1/traces",
            "exportProtocol": "http/protobuf",
            "pat": json.dumps({"Authorization": f"Bearer {secret}"}),
        },
    )
    assert response.status_code == 201, response.text
    credential_id = response.json()["id"]
    assert secret not in response.text
    assert secret not in (await client.get("/api/settings/credentials")).text
    config = ExportConfig(
        enabled=True,
        endpoint="https://collector.example.test/v1/traces",
        credential_id=credential_id,
    )
    async with session_factory() as session:
        repository = CredentialRepository(session)
        binding = await resolve_binding(config, repository)
        assert binding.trace.headers["authorization"] == f"Bearer {secret}"
        initial_key = binding.identity_key
        assert secret not in json.dumps(await repository.get(credential_id))
        await ProjectRepository(session).create("export-project", "Export project", [])
        await session.commit()
    async with session_factory() as session:
        assert (await resolve_binding(config, CredentialRepository(session))).identity_key == initial_key
        config.endpoint = "https://different.example.test/v1/traces"
        with pytest.raises(ExportConfigurationError, match="destination_mismatch"):
            await resolve_binding(config, CredentialRepository(session))
        config.endpoint = "https://collector.example.test"
        config.protocol = "grpc"
        with pytest.raises(ExportConfigurationError):
            await resolve_binding(config, CredentialRepository(session))
    attached = await client.post(
        "/api/projects/export-project/tracker-links",
        json={"credentialId": credential_id, "externalRef": "repo"},
    )
    assert attached.status_code == 422
    mock_tracker_sync_service.test_link.assert_not_awaited()
    app_config = await app.state.dishka_container.get(CPLConfig)
    app_config.export.credential_id = credential_id
    assert (await client.delete(f"/api/settings/credentials/{credential_id}")).status_code == 409
    app_config.export.credential_id = None
    assert (await client.delete(f"/api/settings/credentials/{credential_id}")).status_code == 204


@pytest.mark.parametrize(
    "endpoint,headers",
    [
        ("https://user:sentinel-secret@collector.example.test/v1/traces", {"authorization": "sentinel-secret"}),
        ("https://collector.example.test/v1/traces?token=sentinel-secret", {"authorization": "sentinel-secret"}),
        ("https://collector.example.test/v1/traces", {"host": "sentinel-secret"}),
        ("https://collector.example.test/v1/traces", {"authorization": "sentinel-secret\r\ninjected: value"}),
    ],
)
async def test_invalid_export_credentials_never_echo_transport_secrets(client, endpoint, headers):
    response = await client.post(
        "/api/settings/credentials",
        json={
            "provider": "otlp",
            "label": "fixture",
            "baseUrl": endpoint,
            "exportProtocol": "http/protobuf",
            "pat": json.dumps(headers),
        },
    )
    assert response.status_code == 400
    assert "sentinel-secret" not in response.text


async def test_disabled_export_does_not_resolve_credentials_or_create_identity_key(session_factory, tmp_path):
    class NoAccess:
        async def get(self, *args):
            raise AssertionError("disabled export accessed credentials")

    assert await resolve_binding(ExportConfig(credential_id="unavailable"), NoAccess()) is None
    assert not (tmp_path / "credential.key").exists()


@pytest.mark.parametrize("invalid", ["baseUrl", "label", "provider", "object-pat", "jira-email"])
async def test_pre_handler_credential_validation_never_serializes_secret_inputs(client, invalid):
    secret = "sentinel-private-credential"
    body = {
        "provider": "otlp",
        "label": "fixture",
        "baseUrl": "https://collector.example.test/v1/traces",
        "exportProtocol": "http/protobuf",
        "pat": json.dumps({"Authorization": f"Bearer {secret}"}),
    }
    if invalid == "object-pat":
        body["pat"] = {"nested": [{"secret": secret}]}
    elif invalid == "jira-email":
        body.update(provider="jira", email="invalid", pat=secret)
        body.pop("exportProtocol")
    else:
        body.pop(invalid)
    response = await client.post("/api/settings/credentials", json=body)
    assert response.status_code == 422
    assert secret not in response.text
    assert all(set(error) <= {"type", "loc", "msg"} for error in response.json()["detail"])
    assert (await client.get("/api/settings/credentials")).json()["credentials"] == []
