#!/usr/bin/python3

import asyncio
import codecs
import contextlib
import ipaddress
import json
import logging
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional, Union

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
    def __init__(self, ca_files=None, proxies=None):
        self._ssl_ctx = ssl.create_default_context()
        if ca_files:
            for ca_file in ca_files:
                self._ssl_ctx.load_verify_locations(cafile=ca_file)

        if proxies is None:
            proxies = urllib.request.getproxies()

        self._proxies = {}
        for scheme in ("http", "https"):
            if scheme in proxies:
                self._proxies[scheme] = self._parse_url(proxies[scheme])
        self._no_proxy = proxies.get("no", "")

    async def get_json(self, url):
        return await self._request_json("GET", url)

    async def post_form(self, url, params):
        content_type = "application/x-www-form-urlencoded"
        body = urllib.parse.urlencode(params).encode()
        return await self._request_json("POST", url, content_type, body)

    async def _request_json(self, method, url, content_type=None, body=None):
        url = self._parse_url(url)
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

        async with self._connect(url) as (reader, writer):
            writer.write(to_send)
            await writer.drain()

            async def receive_all():
                error_status = None
                error_data = b""
                while True:
                    event = conn.next_event()
                    if event is h11.NEED_DATA:
                        conn.receive_data(await reader.read(4096))
                    elif isinstance(event, h11.Response):
                        if event.status_code != 200:
                            error_status = event.status_code
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
    async def _connect(self, url):
        proxy = self._get_proxy(url)
        if proxy:
            tunnel = self.open_tunnel(
                proxy.host, proxy.port, url.host, url.port, ssl_=proxy.ssl
            )
            async with tunnel as (reader, writer, trailing_data):
                if trailing_data:
                    raise ProtocolError("Unexpected trailing_data")
                if url.ssl:
                    await _start_tls(writer, url)
                yield reader, writer
        else:
            async with util.connect(url.host, url.port, ssl=url.ssl) as (
                reader,
                writer,
            ):
                yield reader, writer

    def _get_proxy(self, url):
        proxy = self._proxies.get(url.scheme)
        if proxy and not is_proxy_disabled(url.host, self._no_proxy):
            return proxy
        return None

    def _parse_url(self, url):
        url = urllib.parse.urlsplit(url)
        if url.scheme == "https":
            default_port = 443
            ssl_ = self._ssl_ctx
        elif url.scheme == "http":
            default_port = 80
            ssl_ = False
        else:
            raise ValueError(f'Unknown scheme "{url.scheme}"')

        port = url.port or default_port

        if not url.hostname:
            raise ValueError(f'No hostname in URL "{url}"')

        return _ParsedURL(
            url.scheme,
            url.netloc,
            url.hostname,
            port,
            url.path,
            url.query,
            url.fragment,
            url.username,
            url.password,
            ssl_,
        )

    @contextlib.asynccontextmanager
    async def open_tunnel(
        self, proxy_host, proxy_port, target_host, target_port, ssl_=False
    ):
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

        async with util.connect(proxy_host, proxy_port, ssl=ssl_) as (reader, writer):
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
    async def websocket(self, url):
        url = self._parse_url(url)

        ws_scheme = "ws" if url.scheme == "http" else "wss"
        ws_uri = f"{ws_scheme}://{url.netloc}{url.path}"
        ws_uri = websockets.uri.parse_uri(ws_uri)

        protocol = websockets.ClientProtocol(ws_uri)

        async with self._connect(url) as (reader, writer):
            async with _WebsocketConnection(protocol, reader, writer) as con:
                yield con


class _WebsocketConnection:
    _close_timeout = 10

    def __init__(self, protocol, reader, writer):
        self._protocol = protocol
        self._reader = reader
        self._writer = writer
        self._chunks = []
        self._decoder = None
        self._send_lock = asyncio.Lock()

    async def __aenter__(self):
        request = self._protocol.connect()

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


@dataclass
class _ParsedURL:
    scheme: str
    netloc: str
    host: str
    port: int
    path: str
    query: str
    fragment: str
    username: Optional[str]
    password: Optional[str]
    ssl: Union[bool, ssl.SSLContext]


def is_proxy_disabled(host, no_proxy_env):
    if not host or not no_proxy_env:
        return False

    if no_proxy_env == "*":
        return True

    def patterns(network_type=None):
        for pattern in no_proxy_env.split(","):
            pattern = pattern.strip()
            if pattern:
                if network_type is not None:
                    try:
                        pattern = network_type(pattern, strict=False)
                    except ValueError:
                        continue
                yield pattern

    is_disabled = False

    if host[0] == "[":
        # match IPv6
        return _is_proxy_disabled_ipv6(host, patterns(ipaddress.IPv6Network))
    else:
        try:
            addr = ipaddress.IPv4Address(host)
        except ValueError:
            # neither IPv4 nor IPv6 address, match hostname
            is_disabled = _is_proxy_disabled_host(host, patterns())
        else:
            # match IPv4
            for net in patterns(ipaddress.IPv4Network):
                if addr in net:
                    is_disabled = True
                    break

    return is_disabled


def _is_proxy_disabled_ipv6(host, disabled_networks):
    end = host.find("]")
    if end > 0:
        try:
            addr = ipaddress.IPv6Address(host[1:end])
        except ValueError:
            pass
        else:
            for net in disabled_networks:
                if addr in net:
                    return True
    return False


def _is_proxy_disabled_host(host, patterns):
    # ignore trailing dots in the host name
    if host[-1] == ".":
        host = host[:-1]

    # ignore case
    host = host.lower()

    for pattern in patterns:
        # ignore trailing dots in the pattern to check
        if pattern[-1] == ".":
            pattern = pattern[:-1]

        if pattern and pattern[0] == ".":
            # ignore leading pattern dot as well
            pattern = pattern[1:]

        if not pattern:
            continue

        # exact match: example.com matches 'example.com'
        if host == pattern.lower():
            return True

        # tail match: www.example.com matches 'example.com'
        # note: nonexample.com does not match 'example.com'
        if host.endswith(f".{pattern}"):
            return True

    return False


# pylint: disable=protected-access
# remove, if Python version < 3.11 is no longer supported
async def _start_tls(writer, url):
    if hasattr(writer, "start_tls"):
        await writer.start_tls(url.ssl, server_hostname=url.host)
    else:
        # backported from 3.11, commit 6217864f ("gh-79156: Add start_tls()
        # method to streams API (#91453)")
        protocol = writer._protocol
        await writer.drain()
        loop = asyncio.get_running_loop()
        new_transport = await loop.start_tls(
            writer._transport, protocol, url.ssl, server_hostname=url.host
        )
        writer._transport = new_transport

        protocol._stream_writer = writer
        protocol._transport = new_transport
        protocol._over_ssl = True
