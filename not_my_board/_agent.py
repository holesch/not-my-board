#!/usr/bin/env python3

import asyncio
import contextlib
import logging
import os
import pathlib
import traceback
import urllib.parse

import websockets

import not_my_board._http as http
import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._usbip as usbip
import not_my_board._util as util

logger = logging.getLogger(__name__)


async def agent(server_url):
    async with Agent(server_url) as a:
        await a.serve_forever()


class Agent:
    def __init__(self, server_url):
        self._server_url = server_url
        self._reserved_places = {}
        self._pending = set()

    async def __aenter__(self):
        runtime_dir = pathlib.Path(os.environ["XDG_RUNTIME_DIR"])

        async with contextlib.AsyncExitStack() as stack:
            url = urllib.parse.urlsplit(self._server_url)
            ws_scheme = "ws" if url.scheme == "http" else "wss"
            uri = f"{ws_scheme}://{url.netloc}/ws-agent"
            headers = {"Authorization": "Bearer dummy-token-1"}
            ws = await stack.enter_async_context(
                websockets.connect(uri, extra_headers=headers)
            )

            async def receive_iter():
                try:
                    while True:
                        yield await ws.recv()
                except websockets.ConnectionClosedOK:
                    pass

            self._server_proxy = jsonrpc.Proxy(ws.send, receive_iter())

            stack.push_async_callback(self._cleanup)

            self._unix_server = await stack.enter_async_context(
                util.UnixServer(self._handle_client, runtime_dir / "not-my-board.sock")
            )

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def _cleanup(self):
        for _, place in self._reserved_places.items():
            if place.is_attached:
                await place.detach()

    # TODO: hide from JSON-RPC interface
    async def serve_forever(self):
        await util.run_concurrently(
            self._unix_server.serve_forever(), self._server_proxy.io_loop()
        )

    async def _handle_client(self, reader, writer):
        async def send(data):
            writer.write(data + b"\n")
            await writer.drain()

        socket_server = jsonrpc.Server(send, reader, self)
        await socket_server.serve_forever()

    async def reserve(self, name, spec_file):
        if name in self._reserved_places:
            raise RuntimeError(f'A place named "{name}" is already reserved')

        if name in self._pending:
            raise RuntimeError(f'A place named "{name}" is currently being reserved')

        self._pending.add(name)
        try:
            spec_content = util.toml_loads(pathlib.Path(spec_file).read_text())
            spec = models.Spec(name=name, **spec_content)

            response = await http.get_json(f"{self._server_url}/api/v1/places")
            places = [models.Place(**p) for p in response["places"]]

            candidates = _filter_places(spec, places)
            candidate_ids = list(candidates)
            if not candidate_ids:
                raise RuntimeError("No matching place found")

            place_id = await self._server_proxy.reserve(candidate_ids)

            assert name not in self._reserved_places
            self._reserved_places[name] = candidates[place_id]
        finally:
            self._pending.remove(name)

    async def return_reservation(self, name, force=False):
        async with self._reserved_place(name) as reserved_place:
            if reserved_place.is_attached:
                if force:
                    await reserved_place.detach()
                else:
                    raise RuntimeError(f'Place "{name}" is still attached')
            await self._server_proxy.return_reservation(reserved_place.id)
            del self._reserved_places[name]

    async def attach(self, name):
        async with self._reserved_place(name) as reserved_place:
            await reserved_place.attach()

    async def detach(self, name):
        async with self._reserved_place(name) as reserved_place:
            await reserved_place.detach()

    @contextlib.asynccontextmanager
    async def _reserved_place(self, name):
        def check_reserved():
            if name not in self._reserved_places:
                raise RuntimeError(f'A place named "{name}" is not reserved')

        check_reserved()
        reserved_place = self._reserved_places[name]
        async with reserved_place.lock:
            # after waiting for the lock the place might have been returned, so
            # check again
            check_reserved()
            yield reserved_place

    async def list(self):
        return [
            {"place": name, "attached": place.is_attached}
            for name, place in self._reserved_places.items()
        ]

    async def status(self):
        usbip.refresh_vhci_status()
        return [
            {"place": name, **status}
            for name, place in self._reserved_places.items()
            for status in place.status
        ]


def _filter_places(spec, places):
    reserved_places = {}

    spec_part_sets = [
        (name, _part_to_set(spec_part)) for name, spec_part in spec.parts.items()
    ]

    for place in places:
        matching = _find_matching(spec_part_sets, place)
        if matching:
            reserved_places[place.id] = ReservedPlace(spec, place, matching)

    return reserved_places


def _find_matching(spec_part_sets, place):
    match_graph = {}
    for name, spec_part_set in spec_part_sets:
        match_graph[name] = []
        for place_part_idx, place_part in enumerate(place.parts):
            place_part_set = _part_to_set(place_part)
            if spec_part_set.issubset(place_part_set):
                match_graph[name].append(place_part_idx)

        if not match_graph[name]:
            return None

    matching = []
    for name, matches in match_graph.items():
        if len(matches) != 1:
            # Complex matching
            matching_dict = util.find_matching(match_graph)
            if len(match_graph) == len(matching_dict):
                return matching_dict.items()
            else:
                return None

        matching.append((name, matches[0]))

    return matching


def _part_to_set(part):
    return (
        {f"compatible:{c}" for c in part.compatible}
        | {f"usb:{k}" for k in part.usb}
        | {f"tcp:{k}" for k in part.tcp}
    )


class ReservedPlace:
    def __init__(self, spec, place, matching):
        self._spec = spec
        self._place = place
        self._tunnels = []
        self._stack = None
        self.lock = asyncio.Lock()
        proxy = str(place.host), place.port

        for name, place_part_idx in matching:
            spec_part = spec.parts[name]
            place_part = place.parts[place_part_idx]

            for usb_name, usb_spec in spec_part.usb.items():
                self._tunnels.append(
                    UsbTunnel(
                        part_name=name,
                        iface_name=usb_name,
                        proxy=proxy,
                        usbid=place_part.usb[usb_name].usbid,
                        vhci_port=usb_spec.vhci_port,
                    )
                )

            for tcp_name, tcp_spec in spec_part.tcp.items():
                self._tunnels.append(
                    TcpTunnel(
                        part_name=name,
                        iface_name=tcp_name,
                        proxy=proxy,
                        remote=(
                            place_part.tcp[tcp_name].host,
                            place_part.tcp[tcp_name].port,
                        ),
                        local_port=tcp_spec.local_port,
                    )
                )

    async def attach(self):
        if self._stack is not None:
            raise RuntimeError('Place "{self._spec.name}" is already attached')

        async with contextlib.AsyncExitStack() as stack:
            for tunnel in self._tunnels:
                await stack.enter_async_context(tunnel)
            self._stack = stack.pop_all()

    async def detach(self):
        if self._stack is None:
            raise RuntimeError('Place "{self._spec.name}" is not attached')

        try:
            await self._stack.aclose()
        finally:
            self._stack = None

    @property
    def id(self):
        return self._place.id

    @property
    def is_attached(self):
        return self._stack is not None

    @property
    def status(self):
        return [
            {
                "part": t.part_name,
                "interface": t.iface_name,
                "type": t.type_name,
                "attached": t.attached,
            }
            for t in self._tunnels
        ]


class UsbTunnel:
    _target = "usb.not-my-board.localhost", 3240
    _ready_timeout = 5

    def __init__(self, part_name, iface_name, proxy, usbid, vhci_port):
        self._part_name = part_name
        self._iface_name = iface_name
        self._name = f"{part_name}.{iface_name}"
        self._proxy = proxy
        self._usbid = usbid
        self._vhci_port = vhci_port

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            ready_event = asyncio.Event()
            await stack.enter_async_context(
                util.background_task(self._tunnel_task(ready_event))
            )
            logger.debug("%s: Attaching USB device", self._name)

            try:
                await asyncio.wait_for(ready_event.wait(), self._ready_timeout)
            except TimeoutError:
                logger.warning("%s: Attaching USB device timed out", self._name)

            self._stack = stack.pop_all()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def _tunnel_task(self, ready_event):
        retry_timeout = 1
        try:
            while True:
                try:
                    await self._attach()
                    ready_event.set()
                    retry_timeout = 1
                except Exception:
                    traceback.print_exc()
                    await asyncio.sleep(retry_timeout)
                    retry_timeout = min(2 * retry_timeout, 30)
        finally:
            usbip.detach(self._vhci_port)
            logger.debug("%s: USB device detached", self._name)

    async def _attach(self):
        tunnel = http.open_tunnel(*self._proxy, *self._target)
        async with tunnel as (reader, writer, trailing_data):
            if trailing_data:
                raise ProtocolError("USB/IP implementation cannot handle trailing data")
            await usbip.attach(reader, writer, self._usbid, self._vhci_port)
        logger.debug("%s: USB device attached", self._name)

    @property
    def part_name(self):
        return self._part_name

    @property
    def iface_name(self):
        return self._iface_name

    @property
    def type_name(self):
        return "USB"

    @property
    def attached(self):
        return usbip.is_attached(self._vhci_port)


class TcpTunnel:
    def __init__(self, part_name, iface_name, proxy, remote, local_port):
        self._part_name = part_name
        self._iface_name = iface_name
        self._name = f"{part_name}.{iface_name}"
        self._proxy = proxy
        self._remote = remote
        self._local_port = local_port
        self._is_attached = False

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            ready_event = asyncio.Event()
            await stack.enter_async_context(
                util.background_task(self._tunnel_task(ready_event))
            )
            await ready_event.wait()
            self._is_attached = True
            self._stack = stack.pop_all()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)
        self._is_attached = False

    async def _tunnel_task(self, ready_event):
        localhost = "127.0.0.1"
        async with util.Server(
            self._handle_client, localhost, self._local_port
        ) as server:
            ready_event.set()
            await server.serve_forever()

    async def _handle_client(self, client_r, client_w):
        logger.debug("%s: Opening tunnel", self._name)
        async with http.open_tunnel(*self._proxy, *self._remote) as (
            remote_r,
            remote_w,
            trailing_data,
        ):
            logger.debug("%s: Tunnel created, relaying data", self._name)
            client_w.write(trailing_data)
            await client_w.drain()
            await util.relay_streams(client_r, client_w, remote_r, remote_w)

    @property
    def part_name(self):
        return self._part_name

    @property
    def iface_name(self):
        return self._iface_name

    @property
    def type_name(self):
        return "TCP"

    @property
    def attached(self):
        return self._is_attached


class ProtocolError(Exception):
    pass
