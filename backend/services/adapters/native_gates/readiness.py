"""Exact SDK releases qualified by the native hook tests, without SDK imports."""

from importlib.metadata import PackageNotFoundError, version

from backend.services.adapters.contracts import AdapterReadiness

PACKAGES = {
    "crewai": (("crewai", "1.6.1"),),
    "langchain": (("langchain-core", "1.4.7"),),
    "langgraph": (("langgraph", "1.2.5"), ("langgraph-prebuilt", "1.1.0"), ("langchain-core", "1.4.7")),
    "semantic_kernel": (("semantic-kernel", "1.36.0"),),
    "maf": (("agent-framework-core", "1.10.0"),),
    "smolagents": (("smolagents", "1.26.0"),),
    "pydantic_ai": (("pydantic-ai-slim", "1.60.0"),),
    "openai_agents": (("openai-agents", "0.3.3"),),
}


class UnsupportedNativeHookError(RuntimeError):
    """Missing dependencies, unsupported versions or unavailable native hooks."""


def dependency_readiness(family: str) -> AdapterReadiness:
    installed = []
    for package, supported in PACKAGES[family]:
        try:
            found = version(package)
        except PackageNotFoundError:
            return AdapterReadiness(False, f"Missing optional dependency: {package}")
        installed.append(found)
        if found != supported:
            return AdapterReadiness(False, f"Unsupported {package} version, qualified release is {supported}", found)
    return AdapterReadiness(True, installed_version=installed[0])


def require_ready(family: str) -> None:
    readiness = dependency_readiness(family)
    if not readiness.ready:
        raise UnsupportedNativeHookError(readiness.reason)
