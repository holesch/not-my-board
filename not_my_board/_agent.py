#!/usr/bin/env python3

import asyncio
import pathlib
import os
import not_my_board._jsonrpc as jsonrpc
import not_my_board._http as http
from not_my_board._preset import Preset
import traceback
import contextlib
import websockets


async def agent():
    async with Agent() as agent:
        await agent.serve_forever()


class Agent:
    async def __aenter__(self):
        runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])

        async with contextlib.AsyncExitStack() as stack:
            self._unix_server = await asyncio.start_unix_server(
                self._handle_client,
                runtime_dir / "not-my-board.sock")
            await stack.enter_async_context(self._unix_server)

            uri = "ws://localhost:2092/ws"
            headers = {"Authorization": "Bearer dummy-token-1"}
            ws = await stack.enter_async_context(
                    websockets.connect(uri, extra_headers=headers))

            async def receive_iter():
                try:
                    while True:
                        yield await ws.recv()
                except websockets.ConnectionClosedOK:
                    pass

            self._server_proxy = jsonrpc.Proxy(ws.send, receive_iter())
            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def serve_forever(self):
        tasks = [asyncio.create_task(coro) for coro in [
                    self._unix_server.serve_forever(),
                    self._server_proxy.io_loop(),
                ]]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def _handle_client(self, reader, writer):
        async def send(data):
            writer.write(data + b"\n")
            await writer.drain()

        try:
            socket_api = SocketApi(self._server_proxy)
            socket_server = jsonrpc.Server(send, reader, socket_api)
            await socket_server.serve_forever()
        except Exception:
            traceback.print_exc()


class SocketApi:
    def __init__(self, server_proxy):
        self._server_proxy = server_proxy

    async def reserve(self, preset):
        preset = Preset.from_name(preset)
        response = await http.get_json("http://localhost:2092/api/v1/places")
        places = response["places"]
        candidates = await preset.filter(places)
        place = await self._server_proxy.reserve(candidates)
        return place

    async def return_reservation(self, place_id):
        await self._server_proxy.return_reservation(place_id)
