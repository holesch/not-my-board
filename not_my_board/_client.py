#!/usr/bin/env python3

import asyncio
import not_my_board._jsonrpc as jsonrpc
import pathlib
import os
import pprint
import contextlib

async def reserve():
    async with agent_proxy() as proxy:
        result = await proxy.reserve("raspberry-pi")
        pprint.pprint(result)


async def return_reservation(place_id):
    async with agent_proxy() as proxy:
        await proxy.return_reservation(place_id)


@contextlib.asynccontextmanager
async def agent_proxy():
    runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])
    reader, writer = await asyncio.open_unix_connection(runtime_dir / "not-my-board.sock")

    async def send(data):
        writer.write(data + b"\n")
        await writer.drain()

    async with jsonrpc.Proxy(send, reader) as proxy:
        yield proxy
