"""Real standard MCP stdio peer with inert, locally recorded tool bodies."""

import asyncio
import json
import os
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

server = FastMCP("CodePlane stdio qualification")
record = Path(os.environ["CODEPLANE_MCP_FIXTURE_RECORD"])
counter = 0


def append(kind: str, **fields: object) -> None:
    with record.open("a") as output:
        output.write(json.dumps({"kind": kind, **fields}) + "\n")


@server.tool()
async def read_value(value: str, ctx: Context) -> str:
    global counter
    append("entered", value=value, request_id=str(ctx.request_id))
    if value == "counter":
        counter += 1
        return str(counter)
    if value == "wait":
        try:
            await asyncio.Event().wait()
        finally:
            append("cancelled", request_id=str(ctx.request_id))
    return value


if __name__ == "__main__":
    append("started", process_id=os.getpid())
    server.run(transport="stdio")
