#!/usr/bin/env python3

import asyncio
import websockets
import json
import contextlib
import not_my_board._jsonrpc as jsonrpc


async def export():
    async with Exporter() as exporter:
        await exporter.serve_forever()

class Exporter:
    _places = [
        {
            "boards": [
                {
                    "interfaces": [
                        "usb0",
                    ],
                    "compatible": [
                        "raspberry-pi",
                    ],
                },
            ],
        },
    ]

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            uri = "ws://localhost:2092/ws"
            headers = {"Authorization": "Bearer dummy-token-1"}
            self._ws = await stack.enter_async_context(
                    websockets.connect(uri, extra_headers=headers))
            self._receive_iterator = self._receive_iter()

            server_proxy = jsonrpc.Proxy(self._ws.send, self._receive_iterator)
            await server_proxy.register_exporter(self._places, _notification=True)

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def serve_forever(self):
        exporter_api = ExporterApi()
        ws_server = jsonrpc.Server(
                self._ws.send, self._receive_iterator, exporter_api)
        await ws_server.serve_forever()

    async def _receive_iter(self):
        try:
            while True:
                yield await self._ws.recv()
        except websockets.ConnectionClosedOK:
            pass


class ExporterApi:
    pass
