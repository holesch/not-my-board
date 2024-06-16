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
import weakref
from dataclasses import dataclass, field
from typing import List, Tuple

import not_my_board._auth as auth
import not_my_board._http as http
import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._usbip as usbip
import not_my_board._util as util

logger = logging.getLogger(__name__)
USBIP_REMOTE = ("usb.not-my-board.localhost", 3240)
Address = Tuple[str, int]


async def agent(hub_url, ca_files):
    io = _AgentIO(hub_url, http.Client(ca_files))
    async with Agent(hub_url, io) as agent_:
        await agent_.serve_forever()


class _AgentIO:
    def __init__(self, hub_url, http_client):
        self._hub_url = hub_url
        self._http = http_client

    @contextlib.asynccontextmanager
    async def hub_rpc(self):
        url = f"{self._hub_url}/ws"
        async with jsonrpc.WebsocketChannel(url, self._http) as rpc:
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
        response = await self._http.get_json(f"{self._hub_url}/api/v1/places")
        return [models.Place(**p) for p in response["places"]]

    @staticmethod
    async def usbip_refresh_status():
        await usbip.refresh_vhci_status()

    @staticmethod
    def usbip_is_attached(vhci_port):
        return usbip.is_attached(vhci_port)

    async def usbip_attach(self, proxy, target, port_num, usbid):
        tunnel = self._http.open_tunnel(*proxy, *target)
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

    async def _handle_port_forward_client(self, proxy, target, client_r, client_w):
        async with self._http.open_tunnel(*proxy, *target) as (
            remote_r,
            remote_w,
            trailing_data,
        ):
            client_w.write(trailing_data)
            await client_w.drain()
            await util.relay_streams(client_r, client_w, remote_r, remote_w)

    async def get_id_token(self):
        return await auth.get_id_token(self._hub_url, self._http)


class Agent(util.ContextStack):
    def __init__(self, hub_url, io):
        url = urllib.parse.urlsplit(hub_url)
        self._hub_host = url.netloc.split(":")[0]
        self._io = io
        self._locks = weakref.WeakValueDictionary()
        self._reservations = {}

    async def _context_stack(self, stack):
        self._hub = await stack.enter_async_context(self._io.hub_rpc())
        stack.push_async_callback(self._cleanup)
        self._unix_server = await stack.enter_async_context(self._io.unix_server(self))

    async def serve_forever(self):
        await self._unix_server.serve_forever()

    async def _cleanup(self):
        coros = [t.close() for r in self._reservations.values() for t in r.tunnels]

        await util.run_concurrently(*coros)

    async def reserve(self, import_description):
        import_description = models.ImportDesc(**import_description)
        name = import_description.name

        async with self._name_lock(name):
            if name in self._reservations:
                raise RuntimeError(f'A place named "{name}" is already reserved')

            places = await self._io.get_places()

            candidates = _filter_places(import_description, places)
            if not candidates:
                raise RuntimeError("No matching place found")

            candidate_ids = list(candidates)
            place_id = await self._hub.reserve(candidate_ids)

            tunnels = [
                desc.tunnel_cls(desc, self._hub_host, self._io)
                for desc in candidates[place_id]
            ]
            self._reservations[name] = _Reservation(place_id, tunnels)

    async def return_reservation(self, name, force=False):
        async with self._reservation(name) as reservation:
            if reservation.is_attached:
                if force:
                    await self._detach_reservation(reservation)
                else:
                    raise RuntimeError(f'Place "{name}" is still attached')
            await self._hub.return_reservation(reservation.place_id)
            del self._reservations[name]

    async def attach(self, name):
        async with self._reservation(name) as reservation:
            if reservation.is_attached:
                raise RuntimeError(f'Place "{name}" is already attached')

            coros = [t.open() for t in reservation.tunnels]

            async with util.on_error(self._detach_reservation, reservation):
                await util.run_concurrently(*coros)
                reservation.is_attached = True

    async def detach(self, name):
        async with self._reservation(name) as reservation:
            if not reservation.is_attached:
                raise RuntimeError(f'Place "{name}" is not attached')

            await self._detach_reservation(reservation)

    async def _detach_reservation(self, reservation):
        coros = [t.close() for t in reservation.tunnels]
        await util.run_concurrently(*coros)
        reservation.is_attached = False

    @contextlib.asynccontextmanager
    async def _name_lock(self, name):
        if name not in self._locks:
            lock = asyncio.Lock()
            self._locks[name] = lock
        else:
            lock = self._locks[name]

        async with lock:
            yield

    @contextlib.asynccontextmanager
    async def _reservation(self, name):
        async with self._name_lock(name):
            if name not in self._reservations:
                raise RuntimeError(f'A place named "{name}" is not reserved')
            yield self._reservations[name]

    async def list(self):
        return [
            {"place": name, "attached": reservation.is_attached}
            for name, reservation in self._reservations.items()
        ]

    async def status(self):
        await self._io.usbip_refresh_status()

        return [
            {
                "place": tunnel.place_name,
                "part": tunnel.part_name,
                "interface": tunnel.iface_name,
                "type": tunnel.type_name,
                "attached": tunnel.is_attached(),
            }
            for name, reservation in self._reservations.items()
            for tunnel in reservation.tunnels
        ]

    async def get_id_token(self):
        return await self._io.get_id_token()


def _filter_places(import_description, places):
    candidates = {}

    imported_part_sets = [
        (name, _part_to_set(imported_part))
        for name, imported_part in import_description.parts.items()
    ]

    for place in places:
        matching = _find_matching(imported_part_sets, place)
        if matching:
            candidates[place.id] = _create_tunnel_descriptions(
                import_description, place, matching
            )

    return candidates


# pylint: disable=too-many-locals
def _create_tunnel_descriptions(import_description, place, matching):
    place_name = import_description.name
    proxy = (place.host, place.port)
    tunnel_descs = set()

    for part_name, place_part_idx in matching:
        imported_part = import_description.parts[part_name]
        place_part = place.parts[place_part_idx]

        for iface_name, usb_import_description in imported_part.usb.items():
            usbid = place_part.usb[iface_name].usbid
            port_num = usb_import_description.port_num
            tunnel_desc = _UsbTunnelDesc(
                place_name, part_name, iface_name, proxy, usbid, port_num
            )
            tunnel_descs.add(tunnel_desc)

        for iface_name, tcp_import_description in imported_part.tcp.items():
            host = place_part.tcp[iface_name].host
            port = place_part.tcp[iface_name].port
            remote = (host, port)
            local_port = tcp_import_description.local_port
            tunnel_desc = _TcpTunnelDesc(
                place_name, part_name, iface_name, proxy, remote, local_port
            )
            tunnel_descs.add(tunnel_desc)

    return tunnel_descs


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


class _Tunnel:
    _ready_timeout = 5

    def __init__(self, desc, hub_host, io):
        self._desc = desc
        self._io = io
        self._task = None
        self._ready_event = asyncio.Event()

        host = self.proxy[0]
        if ipaddress.ip_address(host).is_loopback:
            host = hub_host
        self._proxy = (host, self.proxy[1])

    def __getattr__(self, attr):
        return getattr(self._desc, attr)

    async def open(self):
        if not self._task:
            logger.debug("%s: Opening %s tunnel", self.name, self.type_name)
            self._ready_event.clear()
            self._task = asyncio.create_task(self._task_func())
            self._task.add_done_callback(self._task_done_callback)

        try:
            await asyncio.wait_for(self._ready_event.wait(), self._ready_timeout)
        except asyncio.TimeoutError:
            logger.warning("%s: Opening %s tunnel timed out", self.name, self.type_name)

    def _task_done_callback(self, _):
        self._task = None

    async def close(self):
        if self._task:
            await util.cancel_tasks([self._task])

    def is_attached(self):
        return bool(self._task and self._ready_event.is_set())


class _UsbTunnel(_Tunnel):
    _vhci_port = None

    async def close(self):
        await super().close()
        if self._vhci_port is not None:
            self._io.usbip_detach(self._vhci_port)

    async def _task_func(self):
        retry_timeout = 1
        while True:
            try:
                self._vhci_port = await self._io.usbip_attach(
                    self._proxy, USBIP_REMOTE, self.port_num, self.usbid
                )
                logger.debug("%s: USB device attached", self.name)
                self._ready_event.set()
                retry_timeout = 1
            except Exception:
                traceback.print_exc()
                await asyncio.sleep(retry_timeout)
                retry_timeout = min(2 * retry_timeout, 30)

    def is_attached(self):
        if self._vhci_port is None:
            return False
        return self._io.usbip_is_attached(self._vhci_port)


class _TcpTunnel(_Tunnel):
    async def _task_func(self):
        await self._io.port_forward(
            self._ready_event, self._proxy, self.remote, self.local_port
        )


@dataclass(frozen=True)
class _TunnelDesc:
    place_name: str
    part_name: str
    iface_name: str
    proxy: Address

    @property
    def name(self):
        return f"{self.place_name}.{self.part_name}.{self.iface_name}"


@dataclass(frozen=True)
class _UsbTunnelDesc(_TunnelDesc):
    usbid: str
    port_num: int
    type_name: str = field(default="USB", init=False)
    tunnel_cls: type = field(default=_UsbTunnel, init=False)


@dataclass(frozen=True)
class _TcpTunnelDesc(_TunnelDesc):
    remote: Address
    local_port: int
    type_name: str = field(default="TCP", init=False)
    tunnel_cls: type = field(default=_TcpTunnel, init=False)


@dataclass
class _Reservation:
    place_id: int
    is_attached: bool = field(default=False, init=False)
    tunnels: List[_Tunnel]


class ProtocolError(Exception):
    pass
