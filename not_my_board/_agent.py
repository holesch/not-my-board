#!/usr/bin/env python3

import asyncio
import pathlib
import os
import not_my_board._jsonrpc as jsonrpc
import traceback


async def agent():
    runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
    srv = await asyncio.start_unix_server(handle_client, runtime_dir / "not-my-board.sock")
    async with srv:
        await srv.serve_forever()


async def handle_client(reader, writer):
    async def send(data):
        writer.write(data + b"\n")
        await writer.drain()

    try:
        socket_server = jsonrpc.Server(send, reader, SocketApi())
        await socket_server.serve_forever()
    except Exception:
        traceback.print_exc()


class SocketApi:
    async def reserve(self, preset):
        return f"Reserving {preset}"
