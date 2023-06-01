#!/usr/bin/env python3

import asyncio
import contextlib
import os
import pathlib
import urllib.parse

import websockets

import not_my_board._http as http
import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._util as util

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


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
            uri = f"{ws_scheme}://{url.netloc}/ws"
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

            self._unix_server = await asyncio.start_unix_server(
                self._handle_client, runtime_dir / "not-my-board.sock"
            )
            await stack.enter_async_context(self._unix_server)

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def _cleanup(self):
        for _, place in self._reserved_places.items():
            await place.detach()

        while self._reserved_places:
            _, place = self._reserved_places.popitem()
            await self._server_proxy.return_reservation(place.id)

    # TODO: hide from JSON-RPC interface
    async def serve_forever(self):
        await util.run_concurrently(
            self._unix_server.serve_forever(), self._server_proxy.io_loop()
        )

    @util.connection_handler
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
            spec_content = tomllib.loads(pathlib.Path(spec_file).read_text())
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

    async def return_reservation(self, name):
        reserved_place = self._reserved_places[name]
        async with reserved_place.lock:
            if reserved_place.is_attached:
                raise RuntimeError(f'Place "{name}" is still attached')
            await self._server_proxy.return_reservation(reserved_place.id)
            del self._reserved_places[name]

    async def attach(self, name):
        if name not in self._reserved_places:
            raise RuntimeError(f'A place named "{name}" is not reserved')

        reserved_place = self._reserved_places[name]
        async with reserved_place.lock:
            await reserved_place.attach()

    async def detach(self, name):
        reserved_place = self._reserved_places[name]
        async with reserved_place.lock:
            await reserved_place.detach()

    async def list(self):
        return list(self._reserved_places)


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
        self._is_attached = False
        self.lock = asyncio.Lock()
        proxy_url = f"http://{place.host}:{place.port}"

        for name, place_part_idx in matching:
            spec_part = spec.parts[name]
            place_part = place.parts[place_part_idx]

            for usb_name, usb_spec in spec_part.usb.items():
                self._tunnels.append(
                    UsbTunnel(
                        name=f"{name}.{usb_name}",
                        proxy_url=proxy_url,
                        usbid=place_part.usb[usb_name].usbid,
                        vhci_port=usb_spec.vhci_port,
                    )
                )

            for tcp_name, tcp_spec in spec_part.tcp.items():
                self._tunnels.append(
                    TcpTunnel(
                        name=f"{name}.{tcp_name}",
                        proxy_url=proxy_url,
                        remote_host=place_part.tcp[tcp_name].host,
                        remote_port=place_part.tcp[tcp_name].port,
                        local_port=tcp_spec.local_port,
                    )
                )

    async def attach(self):
        await util.run_concurrently(*[tunnel.open() for tunnel in self._tunnels])
        self._is_attached = True

    async def detach(self):
        await util.run_concurrently(*[tunnel.close() for tunnel in self._tunnels])
        self._is_attached = False

    @property
    def id(self):
        return self._place.id

    @property
    def is_attached(self):
        return self._is_attached


# TODO implement tunnels
class UsbTunnel:
    def __init__(self, name, proxy_url, usbid, vhci_port):
        pass

    async def open(self):
        pass

    async def close(self):
        pass


class TcpTunnel:
    def __init__(self, name, proxy_url, remote_host, remote_port, local_port):
        pass

    async def open(self):
        pass

    async def close(self):
        pass
