#!/usr/bin/env python3

import asyncio
import not_my_board._jsonrpc as jsonrpc
import pathlib
import os

async def reserve():
    runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
    reader, writer = await asyncio.open_unix_connection(runtime_dir / "not-my-board.sock")

    async def send(data):
        writer.write(data + b"\n")
        await writer.drain()

    async with jsonrpc.Proxy(send, reader) as proxy:
        result = await proxy.reserve("raspberry-pi")
        print(result)
