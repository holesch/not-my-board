#!/usr/bin/env python3

import asyncio
import contextlib
import functools
import ipaddress
import logging
import pathlib
import shutil
import socket
import traceback
import urllib.parse
import weakref
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._usbip as usbip
import not_my_board._util as util

logger = logging.getLogger(__name__)
USBIP_REMOTE = ("usb.not-my-board.localhost", 3240)
Address = Tuple[str, int]


class AgentIO:
    def __init__(self, hub_url, http_client, unix_server_fd=None):
        self._hub_url = hub_url
        self._http = http_client
        self._unix_server_fd = unix_server_fd

    @contextlib.asynccontextmanager
    async def hub_rpc(self):
        url = f"{self._hub_url}/ws"
        async with jsonrpc.WebsocketChannel(url, self._http) as rpc:
            yield rpc

    @contextlib.asynccontextmanager
    async def unix_server(self, api_obj):
        if self._unix_server_fd is not None:
            s = socket.socket(fileno=self._unix_server_fd)
        else:
            socket_path = pathlib.Path("/run") / "not-my-board-agent.sock"
            if socket_path.is_socket():
                socket_path.unlink(missing_ok=True)

            s = socket.socket(family=socket.AF_UNIX)
            s.bind(socket_path.as_posix())
            socket_path.chmod(0o660)
            try:
                shutil.chown(socket_path, group="not-my-board")
            except Exception as e:
                logger.warning(
                    'Failed to change group on agent socket "%s": %s', socket_path, e
                )

        connection_handler = functools.partial(self._handle_unix_client, api_obj)
        async with util.UnixServer(connection_handler, sock=s) as unix_server:
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
    def usbip_refresh_status():
        usbip.refresh_vhci_status()

    @staticmethod
    def usbip_is_attached(vhci_port):
        return usbip.is_attached(vhci_port)

    @staticmethod
    def usbip_port_num_to_busid(port_num):
        return usbip.port_num_to_busid(port_num)

    @staticmethod
    def usbip_vhci_port_to_busid(vhci_port):
        return usbip.vhci_port_to_busid(vhci_port)

    async def usbip_attach(self, proxy, target, port_num, usbid):
        tunnel = self._http.open_tunnel(*proxy, *target)
        async with tunnel as (reader, writer, trailing_data):
            if trailing_data:
                raise ProtocolError("USB/IP implementation cannot handle trailing data")
            return await usbip.attach(reader, writer, usbid, port_num)

    @staticmethod
    async def usbip_detach(vhci_port):
        usbip.detach(vhci_port)

    async def port_forward(self, ready_event, proxy, target, local_port):
        connection_handler = functools.partial(
            self._handle_port_forward_client, proxy, target
        )
        async with util.Server(connection_handler, "localhost", local_port) as server:
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


class Agent(util.ContextStack):
    def __init__(self, hub_url, io, token_src):
        url = urllib.parse.urlsplit(hub_url)
        self._hub_host = url.netloc.split(":")[0]
        self._io = io
        self._locks = weakref.WeakValueDictionary()
        self._reservations = {}
        self._token_src = token_src

    async def _context_stack(self, stack):
        self._hub = await stack.enter_async_context(self._io.hub_rpc())
        self._hub.set_api_object(self._token_src)
        stack.push_async_callback(self._cleanup)
        self._unix_server = await stack.enter_async_context(self._io.unix_server(self))

    @jsonrpc.hidden
    async def serve_forever(self):
        await self._unix_server.serve_forever()

    async def _cleanup(self):
        # The auto return tasks might currently close tunnels. Cancel auto
        # return tasks first.
        await util.cancel_tasks(
            [r.auto_return_task for r in self._reservations.values()]
        )

        coros = [
            t.close() for r in self._reservations.values() for t in r.tunnels.values()
        ]
        await util.run_concurrently(*coros)

    async def reserve(self, name, import_description_toml):
        parsed = util.toml_loads(import_description_toml)
        import_description = models.ImportDesc(name=name, **parsed)
        auto_return_time = util.parse_time(import_description.auto_return_time)

        async with self._name_lock(name):
            if name in self._reservations:
                raise RuntimeError(f'A place named "{name}" is already reserved')

            places = await self._io.get_places()

            tunnel_descs_by_id = _filter_places(import_description, places)
            if not tunnel_descs_by_id:
                raise RuntimeError("No matching place found")

            candidate_ids = list(tunnel_descs_by_id)
            place_id = await self._hub.reserve(candidate_ids)

            for p in places:
                if p.id == place_id:
                    place = p
                    break
            else:
                raise RuntimeError("Hub returned invalid Place ID")

            tunnels = {
                desc: desc.tunnel_cls(desc, self._hub_host, self._io)
                for desc in tunnel_descs_by_id[place_id]
            }

            coro = self._auto_return(name, auto_return_time)
            auto_return_task = asyncio.create_task(coro)

            self._reservations[name] = _Reservation(
                import_description_toml, place, tunnels, auto_return_task
            )

    async def return_reservation(self, name, force=False):
        async with self._reservation(name) as reservation:
            if reservation.is_attached:
                if force:
                    await self._detach_reservation(reservation)
                else:
                    raise RuntimeError(f'Place "{name}" is still attached')
            await self._hub.return_reservation(reservation.place.id)
            await util.cancel_tasks([reservation.auto_return_task])
            del self._reservations[name]

    async def attach(self, name):
        async with self._reservation(name) as reservation:
            if reservation.is_attached:
                raise RuntimeError(f'Place "{name}" is already attached')

            coros = [t.open() for t in reservation.tunnels.values()]

            async with util.on_error(self._detach_reservation, reservation):
                await util.run_concurrently(*coros)
                reservation.is_attached = True

    async def detach(self, name):
        async with self._reservation(name) as reservation:
            if not reservation.is_attached:
                raise RuntimeError(f'Place "{name}" is not attached')

            await self._detach_reservation(reservation)

    async def _detach_reservation(self, reservation):
        coros = [t.close() for t in reservation.tunnels.values()]
        await util.run_concurrently(*coros)
        reservation.is_attached = False

    async def _auto_return(self, name, timeout):
        try:
            if timeout == 0:
                # disable auto return, wait forever
                await asyncio.Event().wait()

            await asyncio.sleep(timeout)
            logger.info('Auto return timeout: Returning place "%s"', name)

            async with self._reservation(name) as reservation:
                if reservation.is_attached:
                    await self._detach_reservation(reservation)
                await self._hub.return_reservation(reservation.place.id)
                del self._reservations[name]
        except Exception as e:
            logger.warning("Auto return failed: %s", e)

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
        self._io.usbip_refresh_status()

        return [
            {
                "place": tunnel.place_name,
                "part": tunnel.part_name,
                "interface": tunnel.iface_name,
                "type": tunnel.type_name,
                "attached": tunnel.is_attached(),
                "port": tunnel.port,
            }
            for name, reservation in self._reservations.items()
            for tunnel in reservation.tunnels.values()
        ]

    async def get_import_description(self, name):
        async with self._reservation(name) as reservation:
            return reservation.import_description_toml

    async def update_import_description(self, name, import_description_toml):
        async with self._reservation(name) as reservation:
            parsed = util.toml_loads(import_description_toml)
            import_description = models.ImportDesc(name=name, **parsed)
            auto_return_time = util.parse_time(import_description.auto_return_time)

            imported_part_sets = [
                (name, _part_to_set(imported_part))
                for name, imported_part in import_description.parts.items()
            ]

            matching = _find_matching(imported_part_sets, reservation.place)
            if not matching:
                raise RuntimeError("New import description doesn't match with place")

            new_tunnel_descs = _create_tunnel_descriptions(
                import_description, reservation.place, matching
            )

            old_tunnel_descs = reservation.tunnels.keys()

            to_remove = old_tunnel_descs - new_tunnel_descs
            to_add = new_tunnel_descs - old_tunnel_descs
            to_keep = old_tunnel_descs & new_tunnel_descs

            new_tunnels = {
                desc: desc.tunnel_cls(desc, self._hub_host, self._io) for desc in to_add
            }
            for desc in to_keep:
                new_tunnels[desc] = reservation.tunnels[desc]

            if reservation.is_attached:
                # close removed tunnels
                removed_tunnels = [
                    t for desc, t in reservation.tunnels.items() if desc in to_remove
                ]
                coros = [t.close() for t in removed_tunnels]
                await util.run_concurrently(*coros)

                async def restore_removed():
                    coros = [t.open() for t in removed_tunnels]
                    await util.run_concurrently(*coros)

                async with util.on_error(restore_removed):
                    # open added tunnels
                    coros = [
                        t.open() for desc, t in new_tunnels.items() if desc in to_add
                    ]
                    await util.run_concurrently(*coros)

            # refresh auto return
            await util.cancel_tasks([reservation.auto_return_task])
            coro = self._auto_return(name, auto_return_time)
            reservation.auto_return_task = asyncio.create_task(coro)

            # everything ok: update reservation
            reservation.import_description_toml = import_description_toml
            reservation.tunnels = new_tunnels


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
            await self._io.usbip_detach(self._vhci_port)
            self._vhci_port = None

    async def _task_func(self):
        retry_timeout = 1
        while True:
            try:
                await self._attach()
                logger.debug("%s: USB device attached", self.name)
                self._ready_event.set()
                retry_timeout = 1
            except Exception:
                traceback.print_exc()
                await asyncio.sleep(retry_timeout)
                retry_timeout = min(2 * retry_timeout, 30)

    async def _attach(self):
        # Only retry attach, if the tunnel was closed. This fixes an issue,
        # when devices are detached and immediately attached again: When the
        # connection is closed, then it takes ~ 0.5 seconds until the remote
        # makes the device available again.
        # When the first attach succeeds, then we send another attach request
        # which blocks until the device is available again. This blocking is
        # expected, so the attach timeout doesn't make sense anymore.
        attach_timeout = 1 if self._vhci_port is None else None

        while True:
            try:
                async with util.timeout(attach_timeout):
                    self._vhci_port = await self._io.usbip_attach(
                        self._proxy, USBIP_REMOTE, self.port_num, self.usbid
                    )
                    break
            except TimeoutError:
                attach_timeout = min(2 * attach_timeout, 30)

    def is_attached(self):
        if self._vhci_port is None:
            return False
        return self._io.usbip_is_attached(self._vhci_port)

    @property
    def port(self):
        if self.is_attached():
            return self._io.usbip_vhci_port_to_busid(self._vhci_port)
        else:
            usbids = self._io.usbip_port_num_to_busid(self.port_num)
            return "/".join(usbids)


class _TcpTunnel(_Tunnel):
    async def _task_func(self):
        await self._io.port_forward(
            self._ready_event, self._proxy, self.remote, self.local_port
        )

    @property
    def port(self):
        return str(self.local_port)


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
    import_description_toml: str
    place: models.Place
    is_attached: bool = field(default=False, init=False)
    tunnels: Mapping[_TunnelDesc, _Tunnel]
    auto_return_task: Optional[asyncio.Task]


class ProtocolError(Exception):
    pass
