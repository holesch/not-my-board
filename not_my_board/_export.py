#!/usr/bin/env python3

import asyncio
import websockets
import contextlib
import ipaddress
import socket
import functools
import h11
import email.utils
import datetime
import logging
import traceback
import not_my_board._jsonrpc as jsonrpc


logger = logging.getLogger(__name__)


async def export(place):
    async with Exporter(place) as exporter:
        await exporter.serve_forever()


def log_exception(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            await func(*args, **kwargs)
        except Exception:
            traceback.print_exc()
    return wrapper


class Exporter:
    def __init__(self, place):
        self._allowed_ips = []
        self._place = place

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            uri = "ws://localhost:2092/ws"
            headers = {"Authorization": "Bearer dummy-token-1"}
            self._ws = await stack.enter_async_context(
                    websockets.connect(uri, extra_headers=headers))
            self._receive_iterator = self._receive_iter()

            server_proxy = jsonrpc.Proxy(self._ws.send, self._receive_iterator)
            await server_proxy.register_exporter(self._place, _notification=True)

            self._http_server = await asyncio.start_server(
                    self._handle_client,
                    port=2192,
                    family=socket.AF_INET)
            await stack.enter_async_context(self._http_server)

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def serve_forever(self):
        exporter_api = ExporterApi(self)
        ws_server = jsonrpc.Server(
                self._ws.send, self._receive_iterator, exporter_api)

        tasks = [asyncio.create_task(coro) for coro in [
                    self._http_server.serve_forever(),
                    ws_server.serve_forever(),
                ]]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()

    async def _receive_iter(self):
        try:
            while True:
                yield await self._ws.recv()
        except websockets.ConnectionClosedOK:
            pass

    async def set_allowed_ips(self, ips):
        ips = list(map(ipaddress.ip_address, ips))
        print(f"setting allowed IPs: {ips}")
        self._allowed_ips = ips

    @log_exception
    async def _handle_client(self, reader, writer):
        host, port = writer.transport.get_extra_info("peername")
        client_ip = ipaddress.ip_address(host)
        con = HttpProxyConnection(reader, writer)
        if client_ip in self._allowed_ips:
            target = await con.receive_target()
            logger.info(f"Proxy CONNECT target: {target}")

            writer.write(
                    b"HTTP/1.1 200 OK\r\n" +
                    b"Content-Length: 14\r\n" +
                    b"Connection: close\r\n" +
                    b"\r\n" +
                    b"Hello, World!\n")
            await writer.drain()
        else:
            await con.deny_request()


class ExporterApi:
    def __init__(self, exporter):
        self._exporter = exporter

    async def set_allowed_ips(self, ips):
        await self._exporter.set_allowed_ips(ips)


def format_date_time(dt=None):
    """Generate a RFC 7231 / RFC 9110 IMF-fixdate string"""
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return email.utils.format_datetime(dt, usegmt=True)


class HttpProxyConnection:
    def __init__(self, reader, writer):
        self._conn = h11.Connection(h11.SERVER)
        self._reader = reader
        self._writer = writer

    async def deny_request(self):
        body = b"This is a not-my-board export server. Your IP address is not allowed.\n"
        await self._send_response(403, body)

    async def receive_target(self):
        try:
            while True:
                event = self._conn.next_event()
                if event is h11.NEED_DATA:
                    data = await self._reader.read(8 * 1024)
                    self._conn.receive_data(data)
                elif type(event) is h11.Request:
                    request = event
                    if request.method == b"CONNECT":
                        await self._send_response(200)
                        return request.target
                    else:
                        body = b"This is a not-my-board export server. You probably want to use not-my-board, instead of connecting directly.\n"
                        headers = [("Allow", "CONNECT")]
                        await self._send_response(405, body, headers)
                        raise Exception(f"Unexpected Method: {request.method}")
                else:
                    raise Exception(f"Unexpected Event: {event}")
        except Exception as e:
            if self._conn.our_state in {h11.IDLE, h11.SEND_RESPONSE}:
                if isinstance(e, h11.RemoteProtocolError):
                    status_code = e.error_status_hint
                else:
                    status_code = 500
                await self._send_response(status_code, body=str(e).encode())
            raise

    async def _send_response(self, status, body=None, headers=None, content_type=None):
        all_headers = [
            ("Date", format_date_time()),
            ("Server", h11.PRODUCT_ID),
            ("Connection", "close"),
        ]

        if body is not None:
            if not content_type:
                content_type = "text/plain"
            all_headers += [
                ("Content-Type", f"{content_type}; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ]

        if headers:
            all_headers += headers

        res = h11.Response(status_code=status, headers=all_headers)
        end = h11.EndOfMessage()

        if body is not None:
            await self._send([res, h11.Data(data=body), end])
        else:
            if status != 200:
                await self._send([res, end])
            else:
                await self._send([res])

    async def _send(self, events):
        data = b"".join([self._conn.send(event) for event in events])
        self._writer.write(data)
        await self._writer.drain()
