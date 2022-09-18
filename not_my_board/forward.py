#!/usr/bin/env python3

import asyncio


class _BaseForwardProtocol(asyncio.Protocol):
    def connection_lost(self, exc):
        self._on_connection_lost.set_result(True)

    def data_received(self, data):
        self._other_transport.write(data)

    def pause_writing(self):
        self._other_transport.pause_reading()

    def resume_writing(self):
        self._other_transport.resume_reading()


class ForwardServerProtocol(_BaseForwardProtocol):
    def __init__(self, on_client_disconnect, on_server_disconnect, host, port):
        self._on_connection_lost = on_client_disconnect
        self._on_server_disconnect = on_server_disconnect
        self._host = host
        self._port = port

    def connection_made(self, transport):
        self._transport = transport
        self._transport.pause_reading()

        async def connect_other_transport():
            loop = asyncio.get_running_loop()
            transport, protocol = await loop.create_connection(
                lambda: ForwardClientProtocol(self._on_server_disconnect, self._transport),
                self._host, self._port)
            self._other_transport = transport
            self._transport.resume_reading()

        asyncio.create_task(connect_other_transport())


class ForwardClientProtocol(_BaseForwardProtocol):
    def __init__(self, on_connection_lost, other_transport):
        self._on_connection_lost = on_connection_lost
        self._other_transport = other_transport

    def connection_made(self, transport):
        self._transport = transport


async def forward_connection(transport, port, host='127.0.0.1'):
    loop = asyncio.get_running_loop()
    on_client_disconnect = loop.create_future()
    on_server_disconnect = loop.create_future()
    protocol = ForwardServerProtocol(
            on_client_disconnect, on_server_disconnect, host, port)
    protocol.connection_made(transport)
    transport.set_protocol(protocol)
    await on_client_disconnect
    await on_server_disconnect
