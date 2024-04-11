#!/usr/bin/python3

import asyncio
import codecs
import contextlib
import json
import logging
import ssl
import urllib.parse

import h11
import websockets
import websockets.frames
import websockets.http11
import websockets.uri

import not_my_board._util as util

logger = logging.getLogger(__name__)
UTF8Decoder = codecs.getincrementaldecoder("utf-8")


class ProtocolError(Exception):
    pass


class Client:
    def __init__(self, ca_files=None):
        self._ssl_ctx = ssl.create_default_context()
        if ca_files:
            for ca_file in ca_files:
                self._ssl_ctx.load_verify_locations(cafile=ca_file)

    async def get_json(self, url):
        return await self._request_json("GET", url)

    async def post_form(self, url, params):
        content_type = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(params).encode()
        return await self._request_json("POST", url, content_type, body)

    async def _request_json(self, method, url, content_type=None, body=None):
        url = urllib.parse.urlsplit(url)
        headers = [
            ("Host", url.netloc),
            ("User-Agent", h11.PRODUCT_ID),
            ("Accept", "application/json"),
            ("Connection", "close"),
        ]
        if body is not None:
            headers += [
                ("Content-Type", content_type),
                ("Content-Length", str(len(body))),
            ]

        conn = h11.Connection(our_role=h11.CLIENT)

        to_send = conn.send(
            h11.Request(method=method, target=url.path or "/", headers=headers)
        )
        if body is not None:
            to_send += conn.send(h11.Data(body))
        to_send += conn.send(h11.EndOfMessage())

        if url.scheme == "https":
            default_port = 443
            ssl_ = self._ssl_ctx
        elif url.scheme == "http":
            default_port = 80
            ssl_ = False
        else:
            raise ValueError(f'Unknown scheme "{url.scheme}"')

        port = url.port or default_port

        async with util.connect(url.hostname, port, ssl=ssl_) as (reader, writer):
            writer.write(to_send)
            await writer.drain()

            async def receive_all():
                error_status = None
                while True:
                    event = conn.next_event()
                    if event is h11.NEED_DATA:
                        conn.receive_data(await reader.read(4096))
                    elif isinstance(event, h11.Response):
                        if event.status_code != 200:
                            error_status = event.status_code
                            error_data = b""
                    elif isinstance(event, h11.Data):
                        if error_status is None:
                            yield event.data
                        else:
                            error_data += event.data
                    elif isinstance(event, (h11.EndOfMessage, h11.PAUSED)):
                        break

                if error_status is not None:
                    raise ProtocolError(
                        f"Expected status code 200, got {error_status}: {error_data}"
                    )

            content = b"".join([data async for data in receive_all()])

        return json.loads(content)

    @contextlib.asynccontextmanager
    async def open_tunnel(self, proxy_host, proxy_port, target_host, target_port):
        headers = [
            ("Host", f"{target_host}:{target_port}"),
            ("User-Agent", h11.PRODUCT_ID),
        ]

        conn = h11.Connection(our_role=h11.CLIENT)
        to_send = conn.send(
            h11.Request(
                method="CONNECT", target=f"{target_host}:{target_port}", headers=headers
            )
        )

        async with util.connect(proxy_host, proxy_port) as (reader, writer):
            writer.write(to_send)
            writer.write(conn.send(h11.EndOfMessage()))
            await writer.drain()

            while True:
                event = conn.next_event()
                if event is h11.NEED_DATA:
                    conn.receive_data(await reader.read(4096))
                elif isinstance(event, h11.Response):
                    response = event
                    if response.status_code != 200:
                        raise ProtocolError(
                            f"Expected status code 200, got {event.status_code}"
                        )

                    yield reader, writer, conn.trailing_data[0]
                    break
                else:
                    raise ProtocolError(f"Unexpected event: {event}")

    @contextlib.asynccontextmanager
    async def websocket(self, url, auth=None):
        url = urllib.parse.urlsplit(url)

        if url.scheme == "http":
            ws_scheme = "ws"
        elif url.scheme == "https":
            ws_scheme = "wss"
        else:
            ws_scheme = url.scheme

        ws_uri = f"{ws_scheme}://{url.netloc}{url.path}"
        ws_uri = websockets.uri.parse_uri(ws_uri)
        protocol = websockets.ClientProtocol(ws_uri)

        ssl_ = self._ssl_ctx if ws_uri.secure else False

        connect = util.connect(ws_uri.host, ws_uri.port, ssl=ssl_)
        async with connect as (reader, writer):
            async with _WebsocketConnection(protocol, reader, writer, auth) as con:
                yield con


class _WebsocketConnection:
    _close_timeout = 10

    def __init__(self, protocol, reader, writer, auth=None):
        self._protocol = protocol
        self._reader = reader
        self._writer = writer
        self._chunks = []
        self._decoder = None
        self._send_lock = asyncio.Lock()
        self._auth = auth

    async def __aenter__(self):
        request = self._protocol.connect()
        if self._auth:
            request.headers["Authorization"] = self._auth

        # sending handshake request
        self._protocol.send_request(request)
        await self._send_protocol_data()

        # wait for handshake response
        self._iterator = self._receive()
        await self._iterator.__anext__()
        logger.debug("WebSocket connection established")

        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def send(self, data):
        if isinstance(data, str):
            async with self._send_context():
                self._protocol.send_text(data.encode())
        elif isinstance(data, (bytes, bytearray, memoryview)):
            async with self._send_context():
                self._protocol.send_binary(data)
        else:
            raise TypeError("data can be either a string or bytes")

    async def receive_iter(self):
        async for message in self._iterator:
            yield message

    async def _receive(self):
        deadline = None
        while True:
            async with util.timeout_at(deadline) as timeout:
                data = await self._reader.read(4096)
                if data:
                    self._protocol.receive_data(data)

                    if self._protocol.close_expected():
                        logger.debug("Close expected, setting connection timeout")
                        now = asyncio.get_running_loop().time()
                        deadline = now + self._close_timeout
                        timeout.reschedule(deadline)

                    # handle pings
                    async with self._send_lock:
                        await self._send_protocol_data()

                    for message in self._handle_event():
                        yield message
                else:
                    logger.debug("Server closed connection")
                    self._protocol.receive_eof()
                    # maybe half close connection
                    async with self._send_lock:
                        await self._send_protocol_data()
                    raise self._protocol.close_exc

    def _handle_event(self):
        for event in self._protocol.events_received():
            if isinstance(event, websockets.frames.Frame):
                yield from self._defragment(event)
            elif isinstance(event, websockets.http11.Response):
                logger.debug("Received WebSocket handshake response: %s", event)
                if self._protocol.handshake_exc is not None:
                    raise self._protocol.handshake_exc
                yield

    def _defragment(self, frame):
        if frame.opcode is websockets.frames.Opcode.TEXT:
            self._decoder = UTF8Decoder(errors="strict")
        elif frame.opcode is websockets.frames.Opcode.BINARY:
            self._decoder = None
        elif frame.opcode is websockets.frames.Opcode.CONT:
            pass
        else:
            return

        if self._decoder is not None:
            data = self._decoder.decode(frame.data, final=frame.fin)
        else:
            data = frame.data

        self._chunks.append(data)

        if frame.fin:
            joiner = b"" if self._decoder is None else ""
            message = joiner.join(self._chunks)
            self._chunks.clear()
            yield message

    @contextlib.asynccontextmanager
    async def _send_context(self):
        async with self._send_lock:
            try:
                yield
            finally:
                await self._send_protocol_data()

    async def _send_protocol_data(self):
        for data in self._protocol.data_to_send():
            if data:
                self._writer.write(data)
                await self._writer.drain()
            else:
                if self._writer.can_write_eof():
                    self._writer.write_eof()
