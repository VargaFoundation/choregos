"""A hosted agent that serves `/invocations_ws`, for the real-container test of the socket relay (S41-23).

`GET /readiness` answers 200. The socket greets the caller, answers each frame with
`{"type": "echo", "text": <frame>}`, closes with `4000 done` on the frame `bye`, and logs how the
caller ended it (`peer closed <code>`), which the test reads back from `docker logs`.
"""

import asyncio
import json
import os

from websockets.asyncio.server import serve


async def socket(connection):
    await connection.send("hello from the container")
    try:
        async for frame in connection:
            await connection.send(json.dumps({"type": "echo", "text": frame}))
            if frame == "bye":
                await connection.close(4000, "done")
                return
    finally:
        code = connection.close_code
        print(f"peer closed {code}", flush=True)


def only_ours(connection, request):
    if request.path == "/readiness":
        return connection.respond(200, "ok\n")
    if request.path != "/invocations_ws":
        return connection.respond(404, "not found\n")
    return None


async def main():
    async with serve(socket, "0.0.0.0", int(os.environ["PORT"]), process_request=only_ours):
        print("listening", flush=True)
        await asyncio.get_running_loop().create_future()


asyncio.run(main())
