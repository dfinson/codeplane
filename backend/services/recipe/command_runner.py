"""Execute a claimed command with real authorization and bounded retained output."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import signal
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import DBAPIError

from backend.models.domain import ApprovalResolution, StateConflictError
from backend.models.events import EventKind, EventMetadata, new_event
from backend.models.schemas.recipes import (
    RecipeArtifactReceipt,
    RecipeAttemptResult,
    RecipeCodeReference,
    RecipeCommandResult,
    RecipeCommandRunner,
    RecipeOutputBinding,
)
from backend.persistence.database import serialized_write
from backend.services.action_policy.classifier import Action, ActionKind
from backend.services.adapters.capability_guard import enforce_command_capabilities
from backend.services.artifacts.artifact_service import ArtifactService
from backend.services.auth.permission_policy import is_hard_gated_command
from backend.services.git.git_service import GitError

if TYPE_CHECKING:
    from typing import BinaryIO

    from pydantic import JsonValue

    from backend.models.schemas.recipes import RecipeAttemptResponse, RecipeRunResponse, RecipeStepInstanceResponse
    from backend.services.recipe.windows_process import WindowsCommandJob
    from backend.services.runtime.service import RuntimeService

log = structlog.get_logger()
_CAPTURE_LIMIT = 50 * 1024 * 1024
_PREVIEW_LIMIT = 4096


class CaptureLimitError(RuntimeError):
    pass


async def _capture(stream: asyncio.StreamReader, destination: BinaryIO) -> None:
    size = 0
    while chunk := await stream.read(64 * 1024):
        available = _CAPTURE_LIMIT - size
        destination.write(chunk[:available])
        size += len(chunk)
        if size > _CAPTURE_LIMIT:
            raise CaptureLimitError("Command output exceeded the retained capture limit.")


async def _settle[T](task: asyncio.Task[T]) -> T:
    """Do not abandon owned cleanup when another cancellation arrives."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _stop(process: asyncio.subprocess.Process, windows_job: WindowsCommandJob | None = None) -> None:
    async with asyncio.timeout(10):
        await _stop_owned_process(process, windows_job)


async def _stop_owned_process(
    process: asyncio.subprocess.Process, windows_job: WindowsCommandJob | None = None
) -> None:
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    else:
        if windows_job is None:
            raise RuntimeError("No owned Windows process tree is available to confirm termination.")
        # Stop the bootstrap first so it cannot enroll or spawn after the tree check.
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
        # returncode observes leader exit independently of inherited pipe closure.
        while process.returncode is None:
            await asyncio.sleep(0.02)
        windows_job.terminate()
        while windows_job.active_processes():
            await asyncio.sleep(0.02)
    await process.wait()


class CommandStepRunner:
    def __init__(self, host: RuntimeService) -> None:
        self._host = host

    async def _authorize(self, run: RecipeRunResponse, attempt: RecipeAttemptResponse, argv: list[str]) -> None:
        await enforce_command_capabilities(
            frozenset(attempt.capabilities),
            workspace=attempt.workspace.path,
            argv=argv,
        )
        command = shlex.join(argv)
        # Literal interpreter programs must reach the existing hard gate too.
        candidates = [command]
        executable = Path(argv[0]).name.lower()
        if executable in {"sh", "bash", "zsh", "dash", "powershell", "pwsh"}:
            for index, argument in enumerate(argv[:-1]):
                if argument.lower() in {"-c", "-command"}:
                    candidates.append(argv[index + 1])
        if any(is_hard_gated_command(candidate) for candidate in candidates):
            approvals = self._host._approval_service
            if approvals is None:
                raise StateConflictError("Explicit command authorization is unavailable.")
            approval = await approvals.create_request(
                run.job_id,
                "Approve this exact recipe command invocation",
                command,
                requires_explicit_approval=True,
            )
            await self._host._event_bus.publish(
                new_event(
                    run.job_id,
                    EventKind.approval_requested,
                    {"approval_id": approval.id, "description": approval.description, "proposed_action": command},
                    metadata=EventMetadata(
                        source_framework="codeplane.recipe",
                        recipe_run_id=run.id,
                        recipe_revision=run.snapshot.revision,
                        attempt_id=attempt.id,
                        step_instance_id=attempt.step_instance_id,
                        execution_epoch=self._host.execution_epoch,
                    ),
                )
            )
            resolution = await approvals.wait_for_resolution(approval.id)
            await self._host._event_bus.publish(
                new_event(
                    run.job_id,
                    EventKind.approval_resolved,
                    {"approval_id": approval.id, "resolution": resolution.value},
                    event_id=f"recipe-command-permission-{approval.id}",
                    metadata=EventMetadata(
                        source_framework="codeplane.recipe",
                        recipe_run_id=run.id,
                        recipe_revision=run.snapshot.revision,
                        step_instance_id=attempt.step_instance_id,
                        attempt_id=attempt.id,
                        execution_epoch=self._host.execution_epoch,
                    ),
                )
            )
            if resolution != ApprovalResolution.approved:
                raise StateConflictError("The exact command was not approved.")
        router = self._host._policy_routers.get(run.job_id)
        if router is None:
            raise StateConflictError("The owning Job's command policy is unavailable.")
        decision = await router.route(
            Action(kind=ActionKind.shell, command=command, job_id=run.job_id, workspace_path=attempt.workspace.path),
            cwd=attempt.workspace.path,
        )
        if not decision.proceed:
            raise StateConflictError("The owning Job's action policy rejected this command.")
        job = await self._host._get_job(run.job_id)
        if job is None or job.state != "running" or self._host._shutting_down:
            raise StateConflictError("The owning Job no longer admits command dispatch.")

    async def run(
        self, run: RecipeRunResponse, instance: RecipeStepInstanceResponse, attempt: RecipeAttemptResponse
    ) -> tuple[RecipeAttemptResult, dict[str, JsonValue]]:
        runner = attempt.runner
        if not isinstance(runner, RecipeCommandRunner) or any(not isinstance(arg, str) for arg in runner.argv):
            raise StateConflictError("Command dispatch requires an exactly resolved argv.")
        argv = [arg for arg in runner.argv if isinstance(arg, str)]
        git = self._host._git_service
        if git is None:
            raise StateConflictError("Command evidence requires the Git service.")
        process: asyncio.subprocess.Process | None = None
        windows_job: WindowsCommandJob | None = None
        pumps: list[asyncio.Task[None]] = []
        reason: str | None = None
        uncertain = False
        capture_complete = False
        stop_confirmed = False
        end_code: str | None = None
        stdout_receipt: RecipeArtifactReceipt | None = None
        stderr_receipt: RecipeArtifactReceipt | None = None
        stdout_preview: str | None = None
        stderr_preview: str | None = None
        with tempfile.TemporaryDirectory(prefix="codeplane-command-") as temporary:
            stdout_path, stderr_path = Path(temporary) / "stdout", Path(temporary) / "stderr"
            with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
                try:
                    await self._authorize(run, attempt, argv)
                    process_argv = argv
                    if os.name == "nt":
                        from backend.services.recipe.windows_process import WindowsCommandJob

                        windows_job = WindowsCommandJob()
                        process_argv = windows_job.launch_argv(argv)
                    creation = asyncio.create_task(
                        asyncio.create_subprocess_exec(
                            *process_argv,
                            cwd=attempt.workspace.path,
                            stdin=asyncio.subprocess.DEVNULL,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            start_new_session=os.name == "posix",
                        )
                    )
                    try:
                        process = await asyncio.shield(creation)
                    except asyncio.CancelledError:
                        await _settle(creation)
                        process = creation.result()
                        raise
                    assert process.stdout is not None and process.stderr is not None
                    pumps = [
                        asyncio.create_task(_capture(process.stdout, stdout_file)),
                        asyncio.create_task(_capture(process.stderr, stderr_file)),
                    ]
                    async with asyncio.timeout(runner.timeout_seconds):
                        await asyncio.gather(*pumps, process.wait())
                    capture_complete = True
                except asyncio.CancelledError:
                    reason = "Command interrupted before complete execution evidence was recorded."
                    uncertain = process is not None
                except (OSError, RuntimeError, StateConflictError, TimeoutError) as exc:
                    reason = str(exc)
                    uncertain = process is not None
                    log.warning("recipe_command_failed", attempt_id=attempt.id, reason=reason)
                finally:
                    if process is not None:
                        try:
                            await _settle(asyncio.create_task(_stop(process, windows_job)))
                            stop_confirmed = True
                        except (OSError, RuntimeError, TimeoutError) as exc:
                            uncertain = True
                            capture_complete = False
                            reason = f"Command stop is unconfirmed: {exc}"
                            log.error("recipe_command_stop_unconfirmed", attempt_id=attempt.id, exc_info=True)
                    if windows_job is not None:
                        try:
                            windows_job.close()
                        except OSError as exc:
                            uncertain = True
                            capture_complete = False
                            stop_confirmed = False
                            reason = f"Windows command ownership could not be closed: {exc}"
                            log.error("recipe_command_ownership_unconfirmed", attempt_id=attempt.id, exc_info=True)
                    for pump in pumps:
                        if not pump.done():
                            pump.cancel()
                    if pumps:
                        await asyncio.gather(*pumps, return_exceptions=True)
            if process is not None:
                try:
                    async with serialized_write(self._host._session_factory) as session:
                        artifacts = ArtifactService.from_session(session)
                        stdout_artifact, stdout_checksum = await artifacts.store_immutable_file(
                            run.job_id,
                            f"{attempt.id}.stdout.log",
                            stdout_path,
                        )
                        stderr_artifact, stderr_checksum = await artifacts.store_immutable_file(
                            run.job_id,
                            f"{attempt.id}.stderr.log",
                            stderr_path,
                        )
                    stdout_receipt = RecipeArtifactReceipt(
                        artifact_id=stdout_artifact.id,
                        checksum=stdout_checksum,
                        size_bytes=stdout_artifact.size_bytes,
                        complete=capture_complete,
                    )
                    stderr_receipt = RecipeArtifactReceipt(
                        artifact_id=stderr_artifact.id,
                        checksum=stderr_checksum,
                        size_bytes=stderr_artifact.size_bytes,
                        complete=capture_complete,
                    )
                    with stdout_path.open("rb") as source:
                        stdout_preview = source.read(_PREVIEW_LIMIT).decode("utf-8", errors="replace")
                    with stderr_path.open("rb") as source:
                        stderr_preview = source.read(_PREVIEW_LIMIT).decode("utf-8", errors="replace")
                except (OSError, RuntimeError, DBAPIError) as exc:
                    uncertain = True
                    reason = f"Command evidence could not be retained: {exc}"
                    log.error("recipe_command_evidence_unrecorded", attempt_id=attempt.id, exc_info=True)
                if stop_confirmed:
                    try:
                        end_code = await git.capture_worktree_commit(
                            cwd=attempt.workspace.path,
                            snapshot_ref=f"refs/codeplane/recipe-inputs/{attempt.id}-result",
                        )
                    except (OSError, GitError) as exc:
                        uncertain = True
                        reason = f"Final command code reference is unrecorded: {exc}"
                        log.error("recipe_command_code_unrecorded", attempt_id=attempt.id, exc_info=True)
        started = process is not None if windows_job is None else windows_job.command_started
        if windows_job is not None and process is None:
            started = False
        exit_status = process.returncode if process is not None else None
        if windows_job is not None and (started is not True or not capture_complete):
            exit_status = None
        if started is None:
            uncertain = True
            reason = reason or "Whether the Windows command started is unconfirmed."
        elif started is False:
            reason = reason or "The command did not start."
        evidence = RecipeCommandResult(
            argv=argv,
            started=started,
            exit_status=exit_status,
            stdout=None,
            stderr=None,
            stdout_artifact=stdout_receipt,
            stderr_artifact=stderr_receipt,
            stdout_preview=stdout_preview,
            stderr_preview=stderr_preview,
            workspace=attempt.workspace.path,
            start_code_ref=attempt.workspace.code_ref,
            end_code_ref=end_code,
            uncertain=uncertain,
        )
        outcome = "interrupted" if uncertain else "succeeded" if started and exit_status == 0 else "failed"
        if outcome == "succeeded" and runner.reconciles and end_code is not None:
            for step_id in runner.reconciles:
                references = [
                    attempt.inputs[name].value
                    for name, port in instance.definition.inputs.items()
                    if isinstance(port.binding, RecipeOutputBinding)
                    and port.binding.step_id == step_id
                    and port.type == "code_ref"
                    and attempt.inputs[name].present
                ]
                if not references:
                    outcome, reason = "failed", f"Reconciliation has no exact code input for '{step_id}'."
                    break
                try:
                    for value in references:
                        reference = RecipeCodeReference.model_validate(value)
                        ancestor = await git.merge_base(reference.commit, end_code, cwd=attempt.workspace.path)
                        if ancestor != reference.commit:
                            outcome = "failed"
                            reason = f"Reconciliation did not retain the code ancestry from '{step_id}'."
                            break
                except GitError as exc:
                    outcome, reason = "failed", f"Reconciliation code could not be verified: {exc}"
                if outcome == "failed":
                    break
        outputs: dict[str, JsonValue] = {}
        for name, port in instance.definition.outputs.items():
            if port.type == "code_ref" and end_code:
                outputs[name] = RecipeCodeReference(workspace=attempt.workspace.path, commit=end_code).model_dump(
                    mode="json"
                )
            elif port.type == "object":
                outputs[name] = evidence.model_dump(mode="json")
            elif name == "exit_status" and port.type == "integer" and exit_status is not None:
                outputs[name] = exit_status
        return RecipeAttemptResult(
            outcome=outcome,
            evidence=evidence,
            reason=reason,
            completed_at=datetime.now(UTC),
        ), outputs
