#!/usr/bin/env python3

import asyncio
import websockets
import json


async def export():
    uri = "ws://localhost:2092/ws"
    async with websockets.connect(uri, extra_headers={"Authorization": "Bearer dummy-token-1"}) as ws:
        msg = {
            "method": "register",
            "params": { 
                "type": "exporter",
                "places": [
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
                ],
            },
        }
        await ws.send(json.dumps(msg))

        while True:
            rsp = await ws.recv()
            print(f"Got: {rsp}")
