"""Map TraceForge ``TitleUpdate`` values to CodePlane ``turn_summary`` payloads.

Extracted from the ``lifespan`` title-pipeline callback so the mapping is a
pure, importable, unit-testable function (the callback itself stays thin).

Only TF-native fields are surfaced — there is no synthetic CodePlane inference
of fields TF does not supply (``plan_item_id``, ``activity_status``). An
``activity``-kind update's native ``title`` *is* that activity's label, so it is
carried as ``activity_label`` for the timeline's activity-group header. A
``step``-kind update's ``title`` is the step's own title (not the activity
label), so ``activity_label`` is intentionally omitted for steps — the label
established by the parent activity update is preserved by the frontend reducer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from traceforge.types import TitleUpdate


def build_turn_summary_payload(update: TitleUpdate) -> dict[str, Any] | None:
    """Return the ``turn_summary`` payload for *update*, or ``None`` to skip it.

    ``session``-kind titles name the whole run and are handled by auto-naming,
    not the activity timeline, so they yield ``None`` (no event emitted).
    """
    if update.kind == "session":
        return None

    is_new_activity = update.kind == "activity"
    payload: dict[str, Any] = {
        "turn_id": update.segment_id,
        "title": update.title,
        "activity_id": update.parent_id or update.segment_id,
        "is_new_activity": is_new_activity,
        "kind": update.kind,
        "version": update.version,
    }
    # Only an activity-kind update natively carries the activity's label (its
    # title). Map it directly — no synthetic inference for step updates.
    if is_new_activity:
        payload["activity_label"] = update.title
    return payload
