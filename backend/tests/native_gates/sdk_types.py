"""Typing-only interfaces for fixture subclasses of absent optional SDK bases.

Qualification always inherits the real installed SDK classes at runtime.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol


class ToolBase(Protocol):
    def forward(self, value: str) -> str: ...


class LLMBase(Protocol):
    def __init__(self, model: str) -> None:
        return None

    def call(self, *args: Any, **kwargs: Any) -> str: ...


class CallbackBase(Protocol):
    def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None: ...


class ModelBase(Protocol):
    async def get_response(self, *args: Any, **kwargs: Any) -> Any: ...

    def stream_response(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]: ...
