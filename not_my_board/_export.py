#!/usr/bin/env python3

import asyncio
import contextlib
import datetime
import email.utils
import ipaddress
import logging
import socket

import h11
import websockets

import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._usbip as usbip
import not_my_board._util as util

logger = logging.getLogger(__name__)


async def export(server_url, place):
    async with Exporter(server_url, place) as exporter:
        await exporter.serve_forever()


class Exporter:
    def __init__(self, server_url, export_desc_path):
        self._server_url = server_url
        self._ip_to_tasks_map = {}
        export_desc_content = export_desc_path.read_text()
        self._place = models.ExportDesc(**util.toml_loads(export_desc_content))

        tcp_targets = {
            f"{tcp.host}:{tcp.port}".encode()
            for part in self._place.parts
            for _, tcp in part.tcp.items()
        }
        self._usb_target = {b"usb.not-my-board.localhost:3240"}
        self._allowed_proxy_targets = tcp_targets | self._usb_target

        self._usbip_devices = [
            usbip.UsbIpDevice(usb.usbid)
            for part in self._place.parts
            for _, usb in part.usb.items()
        ]

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            self._usbip_server = await stack.enter_async_context(
                usbip.UsbIpServer(self._usbip_devices)
            )

            self._http_server = await stack.enter_async_context(
                util.Server(
                    self._handle_client, port=self._place.port, family=socket.AF_INET
                )
            )

            url = f"{self._server_url}/ws-exporter"
            auth = "Bearer dummy-token-1"
            self._ws = await stack.enter_async_context(util.ws_connect(url, auth))
            self._ws_server = jsonrpc.Server(self._ws.send, self._receive_iter(), self)

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    # TODO: hide from JSON-RPC interface
    async def serve_forever(self):
        await util.run_concurrently(
            self._http_server.serve_forever(), self._ws_server.serve_forever()
        )

    async def _receive_iter(self):
        try:
            while True:
                yield await self._ws.recv()
        except websockets.ConnectionClosedOK:
            pass

    async def get_place(self):
        return self._place.dict()

    async def set_allowed_ips(self, ips):
        new_ips = set(map(ipaddress.ip_address, ips))
        old_ips = set(self._ip_to_tasks_map)

        removed_ips = old_ips - new_ips
        added_ips = new_ips - old_ips

        if removed_ips:
            logger.info("Allowed IPs removed: %s", ", ".join(map(str, removed_ips)))

        if added_ips:
            logger.info("Allowed IPs added: %s", ", ".join(map(str, added_ips)))

        to_close = [
            task for ip in removed_ips for task in self._ip_to_tasks_map.pop(ip)
        ]

        for ip in added_ips:
            self._ip_to_tasks_map[ip] = set()

        await util.cancel_tasks(to_close)

    async def _handle_client(self, reader, writer):
        con = HttpProxyConnection(reader, writer, self._allowed_proxy_targets)
        host, _ = writer.transport.get_extra_info("peername")
        client_ip = ipaddress.ip_address(host)

        if client_ip in self._ip_to_tasks_map:
            task = asyncio.current_task()
            tasks = self._ip_to_tasks_map[client_ip]
            tasks.add(task)
            task.add_done_callback(tasks.discard)

            target, trailing_data = await con.receive_target()
            logger.info("Proxy CONNECT target: %s", target)

            await self._tunnel(reader, writer, target, trailing_data)
        else:
            await con.deny_request()

    async def _tunnel(self, client_r, client_w, target, trailing_data):
        if target == b"usb.not-my-board.localhost:3240":
            if trailing_data:
                raise ProtocolError("USB/IP implementation cannot handle trailing data")
            await self._usbip_server.handle_client(client_r, client_w)
        else:
            host, port = target.split(b":", 1)
            port = int(port)

            async with util.connect(host, port) as (remote_r, remote_w):
                remote_w.write(trailing_data)
                await remote_w.drain()
                await util.relay_streams(client_r, client_w, remote_r, remote_w)


def format_date_time(dt=None):
    """Generate a RFC 7231 / RFC 9110 IMF-fixdate string"""
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return email.utils.format_datetime(dt, usegmt=True)


class HttpProxyConnection:
    _msg_wrong_method = (
        b"This is a not-my-board export server. "
        b"You probably want to use not-my-board, instead of connecting directly.\n"
    )
    _msg_wrong_ip = (
        b"This is a not-my-board export server. Your IP address is not allowed.\n"
    )
    _msg_wrong_target = (
        b"This is a not-my-board export server. "
        b"The requested target is not allowed.\n"
    )

    def __init__(self, reader, writer, allowed_targets):
        self._conn = h11.Connection(h11.SERVER)
        self._reader = reader
        self._writer = writer
        self._allowed_targets = allowed_targets

    async def deny_request(self):
        await self._send_response(403, self._msg_wrong_ip)

    async def receive_target(self):
        try:
            while True:
                event = self._conn.next_event()
                if event is h11.NEED_DATA:
                    data = await self._reader.read(8 * 1024)
                    self._conn.receive_data(data)
                elif isinstance(event, h11.Request):
                    request = event
                    if request.method == b"CONNECT":
                        if request.target in self._allowed_targets:
                            await self._send_response(200)
                            return request.target, self._conn.trailing_data[0]
                        else:
                            await self._send_response(403, self._msg_wrong_target)
                            raise ProtocolError(
                                f"Forbidden target requested: {request.target}"
                            )
                    else:
                        headers = [("Allow", "CONNECT")]
                        await self._send_response(405, self._msg_wrong_method, headers)
                        raise ProtocolError(f"Unexpected Method: {request.method}")
                else:
                    raise ProtocolError(f"Unexpected Event: {event}")
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


class ProtocolError(Exception):
    pass
