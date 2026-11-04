import asyncio
import base64
import json

from .model import Key, KmsError


async def handle(reader, writer, service):
    try:
        async with asyncio.timeout(10):
            request = json.loads(await reader.readline())
            result = await service.request(**request)
            if isinstance(result, Key):
                result = {
                    "key_id": result.key_id,
                    "session": result.session,
                    "path": result.path,
                    "expires_at": result.expires_at,
                    "material": base64.b64encode(result.material).decode(),
                }
            response = {"ok": result}
    except KmsError as error:
        response = {"error": str(error)}
    except (ValueError, TypeError, KeyError, TimeoutError):
        response = {"error": "bad_request"}
    try:
        writer.write(json.dumps(response).encode() + b"\n")
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def start_server(service, host="127.0.0.1", port=9400):
    return await asyncio.start_server(
        lambda reader, writer: handle(reader, writer, service),
        host,
        port,
        limit=8192,
    )


class Client:
    def __init__(self, host="127.0.0.1", port=9400):
        self.host = host
        self.port = port

    async def request(self, operation, **fields):
        try:
            async with asyncio.timeout(fields.get("timeout_s", 1) + 0.5):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                try:
                    writer.write(
                        json.dumps({"operation": operation, **fields}).encode() + b"\n"
                    )
                    await writer.drain()
                    response = json.loads(await reader.readline())
                finally:
                    writer.close()
                    await writer.wait_closed()
        except (OSError, TimeoutError) as error:
            raise KmsError("api_unavailable") from error
        if "error" in response:
            raise KmsError(response["error"])
        result = response["ok"]
        if operation == "state":
            return result
        return Key(
            result["key_id"],
            result["session"],
            result["path"],
            result["expires_at"],
            base64.b64decode(result["material"]),
        )
