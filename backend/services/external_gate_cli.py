"""Thin fail-closed stdin/stdout relay; this command never launches a tool."""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

import click
import httpx

from backend.models.schemas.external_gate import ExternalGateConsume, ExternalGateDecision, ExternalGatePhase
from backend.services.adapters.external_gate_wire import DIALECTS, decode_hook, verdict


def _endpoint(url: str, source: str) -> str:
    from backend.validators import validate_integration_binding_id

    validate_integration_binding_id(source)
    parsed = urlsplit(url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"http", "https"}
        or parsed.path.rstrip("/") not in {"", "/api"}
        or parsed.scheme == "http"
        and parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise ValueError("Gate URL must be a credential-free HTTPS API or loopback HTTP endpoint")
    return f"{parsed.scheme}://{parsed.netloc}/api/gate/{source}"


def relay(raw: str, *, url: str, source: str, credential: str) -> ExternalGateDecision:
    if not credential:
        raise ValueError("CPL_GATE_TOKEN is required")
    action = decode_hook(json.loads(raw))
    endpoint = _endpoint(url, source)
    headers = {"Authorization": f"Bearer {credential}"}
    path = f"{endpoint}/requests/{action.request_id}"
    with httpx.Client(
        headers=headers,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
    ) as client:

        def result(response: httpx.Response) -> ExternalGateDecision:
            response.raise_for_status()
            decision = ExternalGateDecision.model_validate(response.json())
            if decision.source_id != source or decision.request_id != action.request_id:
                raise ValueError("Gate response changed request identity")
            return decision

        try:
            state = result(client.post(f"{endpoint}/requests", json=action.model_dump(mode="json", by_alias=True)))
            digest = state.action_digest
            while state.phase == ExternalGatePhase.pending:
                state = result(client.get(path, params={"waitSeconds": 25}))
                if state.action_digest != digest:
                    raise ValueError("Gate response changed the approved action")
            if state.phase != ExternalGatePhase.allowed:
                return state
            body = ExternalGateConsume(**action.model_dump(), action_digest=digest)
            consumed = result(client.post(f"{path}/consume", json=body.model_dump(mode="json", by_alias=True)))
            if consumed.action_digest != digest or consumed.phase != ExternalGatePhase.consumed:
                raise ValueError("Gate consumption did not match the exact action")
            return consumed
        except BaseException:
            try:
                response = client.delete(path)
                response.raise_for_status()
            except Exception as exc:
                print(f"Gate retirement unconfirmed: {type(exc).__name__}", file=sys.stderr)
            raise


@click.command("gate")
@click.option("--stdin", "from_stdin", is_flag=True)
@click.option("--agent", type=click.Choice(DIALECTS), default=None)
@click.option("--format", "output_format", type=click.Choice(["claude-code", "json"]), default=None)
@click.option("--url", envvar="CPL_GATE_URL", default="http://127.0.0.1:8080")
@click.option("--source", envvar="CPL_GATE_SOURCE", default="")
def gate(from_stdin: bool, agent: str | None, output_format: str | None, url: str, source: str) -> None:
    """Translate a supported hook through a live, single-use CodePlane decision.

    Source credentials are issued for an existing Runtime execution. Supply the
    returned source and CPL_GATE_TOKEN; no session/default-owner lookup occurs.
    """
    dialect = agent or output_format or "claude-code"
    allowed, reason = False, "Gate unavailable"
    try:
        if not from_stdin:
            raise ValueError("--stdin is required")
        raw = sys.stdin.buffer.read(131073) if hasattr(sys.stdin, "buffer") else sys.stdin.read(131073).encode()
        if not raw or len(raw) > 131072:
            raise ValueError("Missing or oversized hook input")
        state = relay(raw.decode("utf-8"), url=url, source=source, credential=os.environ.get("CPL_GATE_TOKEN", ""))
        allowed, reason = state.decision == "allow", state.reason
    except (Exception, KeyboardInterrupt) as exc:
        reason = "Gate error; action denied"
        print(f"CodePlane gate failed closed: {type(exc).__name__}", file=sys.stderr)
    payload, code, error = verdict(allowed, dialect, reason)
    if payload is not None:
        click.echo(json.dumps(payload))
    if error:
        click.echo(error, err=True)
    if code:
        raise click.exceptions.Exit(code)
