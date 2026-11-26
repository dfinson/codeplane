"""Isolated browser fixture. Explicit test contracts are not production pin evidence."""

import hashlib
import os

import uvicorn

import backend.lifespan as app_lifespan
from backend.app_factory import create_app
from backend.models.execution_contracts import ExecutionContract, ImplementationPin
from backend.services.completers.voice_service import VoiceService

# Voice model loading is unrelated to these browser scenarios and may download
# hundreds of MiB. No voice endpoint is exercised by this fixture.
VoiceService._ensure_model = lambda self: None

original_wire = app_lifespan._wire_core_services


async def wire_qualification(*args, **kwargs):
    services = await original_wire(*args, **kwargs)
    runtime = services.runtime_service
    implementation = ImplementationPin(id="browser-fixture", sha256=hashlib.sha256(b"browser-fixture").hexdigest())
    contract = ExecutionContract(
        id="browser-fixture",
        policy=implementation,
        parser=implementation,
        grouping=implementation,
        title=implementation,
    )
    runtime.configure_execution_contracts(available={contract.id: contract}, selected_for_new=contract.id)
    return services


app_lifespan._wire_core_services = wire_qualification
app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["RECIPE_BROWSER_PORT"]), log_level="warning")
