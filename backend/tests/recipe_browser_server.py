"""Isolated browser fixture. Explicit test contracts are not production pin evidence."""

import hashlib
import os
from contextlib import asynccontextmanager

import uvicorn

from backend.app_factory import create_app
from backend.models.execution_contracts import ExecutionContract, ImplementationPin
from backend.services.completers.voice_service import VoiceService
from backend.services.runtime import RuntimeService

# Voice model loading is unrelated to these browser scenarios and may download
# hundreds of MiB. No voice endpoint is exercised by this fixture.
VoiceService._ensure_model = lambda self: None

app = create_app()
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(application):
    async with original_lifespan(application):
        runtime = await application.state.dishka_container.get(RuntimeService)
        implementation = ImplementationPin(id="browser-fixture", sha256=hashlib.sha256(b"browser-fixture").hexdigest())
        contract = ExecutionContract(
            id="browser-fixture",
            policy=implementation,
            parser=implementation,
            grouping=implementation,
            title=implementation,
        )
        runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
        yield


app.router.lifespan_context = lifespan

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["RECIPE_BROWSER_PORT"]), log_level="warning")
