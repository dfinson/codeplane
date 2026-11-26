"""Reversible provider admission, independent of provider-served model names."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.models.domain import AgentSDK, SDKModelMismatchError

if TYPE_CHECKING:
    from backend.config import CPLConfig


class IntegrationDisabledError(SDKModelMismatchError):
    """An installed integration is disabled by operator policy."""


class IntegrationPolicy:
    def __init__(self, config: CPLConfig) -> None:
        self._config = config

    @property
    def default_sdk(self) -> str:
        try:
            configured = AgentSDK(self._config.runtime.default_sdk)
        except ValueError as exc:
            raise SDKModelMismatchError(f"Unknown default SDK: {self._config.runtime.default_sdk!r}") from exc
        if self._config.runtime.copilot_only:
            return AgentSDK.copilot.value
        return self.require_enabled(configured).value

    def enabled(self, sdk: AgentSDK | str) -> bool:
        return not self._config.runtime.copilot_only or sdk == AgentSDK.copilot

    def require_enabled(self, sdk: AgentSDK | str) -> AgentSDK:
        try:
            provider = AgentSDK(sdk)
        except ValueError as exc:
            raise SDKModelMismatchError(f"Unknown SDK: {sdk!r}") from exc
        if not self.enabled(provider):
            raise IntegrationDisabledError(
                f"The {provider.value} integration is disabled by runtime.copilot_only. "
                "Its history is preserved. Re-enable the integration before executing it."
            )
        return provider
