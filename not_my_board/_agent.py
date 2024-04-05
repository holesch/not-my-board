#!/usr/bin/env python3

import asyncio
import contextlib
import functools
import ipaddress
import logging
import os
import pathlib
import shutil
import traceback
import urllib.parse

import not_my_board._http as http
import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._usbip as usbip
import not_my_board._util as util

logger = logging.getLogger(__name__)


async def agent(hub_url):
    io = _AgentIO(hub_url)
    async with Agent(hub_url, io) as agent_:
        await agent_.serve_forever()


class _AgentIO:
    def __init__(self, hub_url):
        self._hub_url = hub_url

    @contextlib.asynccontextmanager
    async def hub_rpc(self):
        auth = "Bearer dummy-token-1"
        url = f"{self._hub_url}/ws-agent"
        async with jsonrpc.WebsocketChannel(url, auth=auth) as rpc:
            yield rpc

    @contextlib.asynccontextmanager
    async def unix_server(self, api_obj):
        socket_path = pathlib.Path("/run") / "not-my-board-agent.sock"

        connection_handler = functools.partial(self._handle_unix_client, api_obj)
        async with util.UnixServer(connection_handler, socket_path) as unix_server:
            os.chmod(socket_path, 0o660)
            try:
                shutil.chown(socket_path, group="not-my-board")
            except Exception as e:
                logger.warning(
                    'Failed to change group on agent socket "%s": %s', socket_path, e
                )

            yield unix_server

    @staticmethod
    async def _handle_unix_client(api_obj, reader, writer):
        async def send(data):
            writer.write(data + b"\n")
            await writer.drain()

        channel = jsonrpc.Channel(send, reader, api_obj)
        await channel.communicate_forever()

    async def get_places(self):
        response = await http.get_json(f"{self._hub_url}/api/v1/places")
        return [models.Place(**p) for p in response["places"]]

    @staticmethod
    async def usbip_refresh_status():
        await usbip.refresh_vhci_status()

    @staticmethod
    def usbip_is_attached(vhci_port):
        return usbip.is_attached(vhci_port)

    @staticmethod
    async def usbip_attach(proxy, target, port_num, usbid):
        tunnel = http.open_tunnel(*proxy, *target)
        async with tunnel as (reader, writer, trailing_data):
            if trailing_data:
                raise ProtocolError("USB/IP implementation cannot handle trailing data")
            return await usbip.attach(reader, writer, usbid, port_num)

    @staticmethod
    def usbip_detach(vhci_port):
        usbip.detach(vhci_port)

    async def port_forward(self, ready_event, proxy, target, local_port):
        localhost = "127.0.0.1"
        connection_handler = functools.partial(
            self._handle_port_forward_client, proxy, target
        )
        async with util.Server(connection_handler, localhost, local_port) as server:
            ready_event.set()
            await server.serve_forever()

    @staticmethod
    async def _handle_port_forward_client(proxy, target, client_r, client_w):
        async with http.open_tunnel(*proxy, *target) as (
            remote_r,
            remote_w,
            trailing_data,
        ):
            client_w.write(trailing_data)
            await client_w.drain()
            await util.relay_streams(client_r, client_w, remote_r, remote_w)


class Agent(util.ContextStack):
    def __init__(self, hub_url, io):
        url = urllib.parse.urlsplit(hub_url)
        self._hub_host = url.netloc.split(":")[0]
        self._io = io
        self._reserved_places = {}
        self._pending = set()

    async def _context_stack(self, stack):
        self._hub = await stack.enter_async_context(self._io.hub_rpc())
        stack.push_async_callback(self._cleanup)
        self._unix_server = await stack.enter_async_context(self._io.unix_server(self))

    async def serve_forever(self):
        await self._unix_server.serve_forever()

    async def _cleanup(self):
        for _, place in self._reserved_places.items():
            if place.is_attached:
                await place.detach()

    async def reserve(self, import_description):
        import_description = models.ImportDesc(**import_description)
        name = import_description.name

        if name in self._reserved_places:
            raise RuntimeError(f'A place named "{name}" is already reserved')

        if name in self._pending:
            raise RuntimeError(f'A place named "{name}" is currently being reserved')

        self._pending.add(name)
        try:
            places = await self._io.get_places()

            candidates = self._filter_places(import_description, places)
            candidate_ids = list(candidates)
            if not candidate_ids:
                raise RuntimeError("No matching place found")

            place_id = await self._hub.reserve(candidate_ids)

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
            await self._hub.return_reservation(reserved_place.id)
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
        await self._io.usbip_refresh_status()
        return [
            {"place": name, **status}
            for name, place in self._reserved_places.items()
            for status in place.status
        ]

    def _filter_places(self, import_description, places):
        reserved_places = {}

        imported_part_sets = [
            (name, _part_to_set(imported_part))
            for name, imported_part in import_description.parts.items()
        ]

        for place in places:
            matching = _find_matching(imported_part_sets, place)
            if matching:
                real_host = self._real_host(place.host)
                reserved_places[place.id] = ReservedPlace(
                    import_description, place, real_host, matching, self._io
                )

        return reserved_places

    def _real_host(self, host):
        if ipaddress.ip_address(host).is_loopback:
            logger.info("Replacing %s with %s", host, self._hub_host)
            return self._hub_host
        return host


def _find_matching(imported_part_sets, place):
    match_graph = {}
    for name, imported_part_set in imported_part_sets:
        match_graph[name] = []
        for place_part_idx, place_part in enumerate(place.parts):
            place_part_set = _part_to_set(place_part)
            if imported_part_set.issubset(place_part_set):
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
    def __init__(self, import_description, place, real_host, matching, io):
        self._import_description = import_description
        self._place = place
        self._tunnels = []
        self._stack = None
        self.lock = asyncio.Lock()
        proxy = real_host, place.port

        for name, place_part_idx in matching:
            imported_part = import_description.parts[name]
            place_part = place.parts[place_part_idx]

            for usb_name, usb_import_description in imported_part.usb.items():
                self._tunnels.append(
                    UsbTunnel(
                        io,
                        part_name=name,
                        iface_name=usb_name,
                        proxy=proxy,
                        usbid=place_part.usb[usb_name].usbid,
                        port_num=usb_import_description.port_num,
                    )
                )

            for tcp_name, tcp_import_description in imported_part.tcp.items():
                self._tunnels.append(
                    TcpTunnel(
                        io,
                        part_name=name,
                        iface_name=tcp_name,
                        proxy=proxy,
                        remote=(
                            place_part.tcp[tcp_name].host,
                            place_part.tcp[tcp_name].port,
                        ),
                        local_port=tcp_import_description.local_port,
                    )
                )

    async def attach(self):
        if self._stack is not None:
            raise RuntimeError(
                'Place "{self._import_description.name}" is already attached'
            )

        async with contextlib.AsyncExitStack() as stack:
            for tunnel in self._tunnels:
                await stack.enter_async_context(tunnel)
            self._stack = stack.pop_all()

    async def detach(self):
        if self._stack is None:
            raise RuntimeError(
                'Place "{self._import_description.name}" is not attached'
            )

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


class UsbTunnel(util.ContextStack):
    _target = "usb.not-my-board.localhost", 3240
    _ready_timeout = 5

    def __init__(self, io, part_name, iface_name, proxy, usbid, port_num):
        self._io = io
        self._part_name = part_name
        self._iface_name = iface_name
        self._name = f"{part_name}.{iface_name}"
        self._proxy = proxy
        self._usbid = usbid
        self._port_num = port_num
        self._vhci_port = None

    async def _context_stack(self, stack):
        ready_event = asyncio.Event()
        await stack.enter_async_context(
            util.background_task(self._tunnel_task(ready_event))
        )
        logger.debug("%s: Attaching USB device", self._name)

        try:
            await asyncio.wait_for(ready_event.wait(), self._ready_timeout)
        except asyncio.TimeoutError:
            logger.warning("%s: Attaching USB device timed out", self._name)

    async def _tunnel_task(self, ready_event):
        retry_timeout = 1
        try:
            while True:
                try:
                    self._vhci_port = await self._io.usbip_attach(
                        self._proxy, self._target, self._port_num, self._usbid
                    )
                    logger.debug("%s: USB device attached", self._name)
                    ready_event.set()
                    retry_timeout = 1
                except Exception:
                    traceback.print_exc()
                    await asyncio.sleep(retry_timeout)
                    retry_timeout = min(2 * retry_timeout, 30)
        finally:
            if self._vhci_port is not None:
                self._io.usbip_detach(self._vhci_port)
            logger.debug("%s: USB device detached", self._name)

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
        return (
            self._io.usbip_is_attached(self._vhci_port)
            if self._vhci_port is not None
            else False
        )


class TcpTunnel(util.ContextStack):
    def __init__(self, io, part_name, iface_name, proxy, remote, local_port):
        self._io = io
        self._part_name = part_name
        self._iface_name = iface_name
        self._name = f"{part_name}.{iface_name}"
        self._proxy = proxy
        self._remote = remote
        self._local_port = local_port
        self._is_attached = False

    async def _context_stack(self, stack):
        ready_event = asyncio.Event()
        coro = self._io.port_forward(
            ready_event, self._proxy, self._remote, self._local_port
        )
        await stack.enter_async_context(util.background_task(coro))
        await ready_event.wait()
        self._is_attached = True

    async def __aexit__(self, exc_type, exc, tb):
        super().__aexit__(exc_type, exc, tb)
        self._is_attached = False

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
