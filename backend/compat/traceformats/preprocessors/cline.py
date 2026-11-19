# Derived from traceforge-toolkit 0.1.5. MIT license: backend/compat/traceforge.LICENSE.
"""Cline legacy UI history and explicitly versioned native SDK UI updates.

Flat records retain the legacy timestamp-backed contract. Native 4.1.3 UI
updates use an app-owned envelope, without modifying the captured message:
{"id": message["ts"], "_codeplane_source_format": {"producer": "cline",
"version": "4.1.3", "representation": "sdk-ui-update"}, "message": message}.
The shared decoder uses that actual native id, never an epoch-seeded substitute.
"""

from __future__ import annotations

import json
from typing import Any

from backend.compat.traceformats.preprocessors.registry import register_preprocessor

_COMPACTION_STATUSES = frozenset({"started", "completed", "skipped", "failed", "cancelled"})
_SDK_UI_FORMAT = {"producer": "cline", "version": "4.1.3", "representation": "sdk-ui-update"}


def _message(obj: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    sdk_ui = "_codeplane_source_format" in obj
    if not sdk_ui:
        message = dict(obj)
        message.pop("_codeplane_cline", None)
        return message, False
    if obj["_codeplane_source_format"] != _SDK_UI_FORMAT:
        raise ValueError("Unsupported Cline SDK UI producer version or representation")
    if set(obj) != {"id", "_codeplane_source_format", "message"} or not isinstance(obj.get("message"), dict):
        raise ValueError("Cline SDK UI capture requires an exact source envelope")
    message = dict(obj["message"])
    identity, supplied = message.get("ts"), obj["id"]
    if (
        isinstance(identity, bool)
        or not isinstance(identity, int)
        or identity <= 0
        or isinstance(supplied, bool)
        or not isinstance(supplied, int)
        or supplied != identity
    ):
        raise ValueError("Cline SDK UI identity must equal its actual positive native minter id")
    if not isinstance(message.get("partial"), bool):
        raise ValueError("Cline SDK UI update must declare its native partial state")
    message_type = message.get("type")
    subtype = message.get(message_type) if message_type in ("ask", "say") else None
    if not isinstance(subtype, str) or not subtype:
        raise ValueError("Cline SDK UI update requires its native compound discriminator")
    message["_codeplane_cline"] = {
        **_SDK_UI_FORMAT,
        "partial": message["partial"],
        "message_type": message_type,
        "subtype": subtype,
    }
    # This representation's ts is a monotonic identity, never wall-clock time.
    message.pop("ts")
    return message, True


@register_preprocessor("cline")
def preprocess_cline(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Synthesize compound type from Cline's type + say/ask subtype.

    Cline events have type="ask"|"say" with the subtype in the
    corresponding field. Synthesizes "say.api_req_started" etc.
    Parses JSON text field for structured subtypes into top-level fields.
    """
    obj, sdk_ui = _message(obj)
    msg_type = obj.get("type")  # "ask" or "say"
    subtype = obj.get(msg_type) if msg_type in ("ask", "say") else None

    if subtype:
        normalized = dict(obj)
        normalized["type"] = f"{msg_type}.{subtype}"

        # Parse JSON text field for known subtypes that embed structured data
        text = normalized.get("text")
        if text and subtype in ("api_req_started", "api_req_finished", "tool", "compaction"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    normalized["parsed"] = parsed
                    status = parsed.get("status")
                    if subtype == "compaction" and isinstance(status, str) and status in _COMPACTION_STATUSES:
                        normalized["type"] = f"{normalized['type']}.{status}"
                elif sdk_ui:
                    raise ValueError("Cline SDK UI structured text must contain an object")
            except (json.JSONDecodeError, ValueError) as exc:
                if sdk_ui:
                    raise ValueError("Malformed Cline SDK UI structured text") from exc
        if sdk_ui:
            if obj["partial"]:
                normalized["type"] = "sdk_ui.partial"
            elif msg_type == "ask":
                normalized["type"] = "sdk_ui.ask"
            elif subtype in ("text", "reasoning", "user_feedback", "user_feedback_diff", "error"):
                normalized["type"] = f"sdk_ui.{subtype}"
            else:
                # Final UI content omits native execution output/success. Keep
                # previews/banners and defaulted display metrics as UI evidence.
                normalized["type"] = "sdk_ui.evidence"
        return [normalized]
    return [obj]
