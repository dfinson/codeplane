"""Actual upstream Flowise operations with a bounded transport and common gate."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import httpx
from agentmesh.integrations.flowise import FlowiseCallRecord, TrustGatedFlowiseClient

from backend.services.adapters.native_gates.binding import NativeCall, NativeGateDeniedError
from backend.services.ingest.mapping import canonical_record
from backend.services.ingest.otel_decoder import decode_json_body

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.services.adapters.native_gates import GateAttachment


@dataclass(frozen=True, slots=True)
class FlowiseObservation:
    operation_id: str
    flow_id: str
    operation: Literal["predict", "upsert"]
    remote_session_id: str | None
    outcome: Literal["responded", "delivery_unknown"]
    status_code: int | None
    duration_ms: float


@dataclass
class FlowiseTarget:
    client: TrustGatedFlowiseClient
    observe: Callable[[FlowiseObservation], None] | None = None


class FlowiseDeliveryUnknownError(OSError):
    """The request has no confirmed remote result and must not be blindly retried."""

    outcome = "delivery_unknown"


def install(gate: GateAttachment, target: object) -> None:
    if not isinstance(target, FlowiseTarget) or not isinstance(target.client, TrustGatedFlowiseClient):
        raise TypeError("Flowise requires an actual TrustGatedFlowiseClient in a FlowiseTarget")
    client = target.client
    parsed = urlsplit(client.base_url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Flowise requires a configured credential-free HTTP service URL")
    gate.claim(client)

    def request(url: str, payload: dict[str, Any], flow_id: str) -> dict[str, Any]:
        if not flow_id or any(character in flow_id for character in "/?#\\"):
            raise ValueError("Flowise operation requires an unambiguous flow identifier")
        operation: Literal["predict", "upsert"]
        if url == f"{client.base_url}/api/v1/prediction/{flow_id}":
            operation = "predict"
        elif url == f"{client.base_url}/api/v1/vector/upsert/{flow_id}":
            operation = "upsert"
        else:
            raise ValueError("Flowise request is outside the configured prediction/upsert endpoint")
        body = canonical_record(payload)
        decode_json_body(body)
        call = NativeCall(
            name=f"flowise.{operation}",
            arguments={"endpoint": url, "flow_id": flow_id, "body": decode_json_body(body)},
            run_id=flow_id,
            definition="Remote Flowise request. Internal tools are not intercepted. Response loss is delivery_unknown.",
        )
        if not gate.allow_sync(call):
            raise NativeGateDeniedError("Flowise request denied before transport")
        started = time.monotonic()
        status: int | None = None
        result: dict[str, Any] | None = None
        try:
            with (
                httpx.Client(
                    timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
                    trust_env=False,
                    follow_redirects=False,
                ) as transport,
                transport.stream("POST", url, content=body, headers=client._build_headers()) as response,
            ):
                status = response.status_code
                response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(chunks) + len(chunk) > 1024 * 1024:
                        raise ValueError("Flowise response exceeds 1 MiB")
                    chunks.extend(chunk)
                decoded = decode_json_body(bytes(chunks))
                if not isinstance(decoded, dict):
                    raise ValueError("Flowise response must be an object")
                result = decoded
        except (httpx.HTTPError, ValueError) as exc:
            gate.binding.report_sync(call, None, None)
            raise FlowiseDeliveryUnknownError("Flowise did not confirm a valid bounded response") from exc
        else:
            gate.binding.report_sync(call, True, True)
            return result
        finally:
            duration = (time.monotonic() - started) * 1000
            if client.policy.audit_logging:
                client._call_log.append(
                    FlowiseCallRecord(
                        flow_id=flow_id,
                        endpoint=url,
                        timestamp=datetime.now(UTC),
                        caller_did=client.identity.did,
                        trust_score=client.identity.trust_score,
                        status_code=status or 0,
                        response_time_ms=duration,
                    )
                )
            if target.observe is not None:
                session = result.get("sessionId", result.get("chatId")) if result is not None else None
                target.observe(
                    FlowiseObservation(
                        call.operation_id,
                        flow_id,
                        operation,
                        session if isinstance(session, str) else None,
                        "responded" if result is not None else "delivery_unknown",
                        status,
                        duration,
                    )
                )

    gate.patch(client, "_make_request", request)
