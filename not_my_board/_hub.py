#!/usr/bin/env python3

import asyncio
import contextlib
import contextvars
import ipaddress
import itertools
import logging
import random
import traceback

import asgineer

import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._util as util

logger = logging.getLogger(__name__)
client_ip_var = contextvars.ContextVar("client_ip")
reservation_context_var = contextvars.ContextVar("reservation_context")
valid_tokens = ("dummy-token-1", "dummy-token-2")


def hub():
    asgineer.run(asgi_app, "uvicorn", ":2092")


@asgineer.to_asgi
async def asgi_app(request):
    if isinstance(request, asgineer.WebsocketRequest):
        if request.path == "/ws-agent":
            return await _handle_agent(request)
        elif request.path == "/ws-exporter":
            return await _handle_exporter(request)
        await request.close()
        return
    elif isinstance(request, asgineer.HttpRequest):
        if request.path == "/api/v1/places":
            return await _hub.get_places()
    return 404, {}, "Page not found"


async def _handle_agent(ws):
    await _authorize_ws(ws)
    client_ip = ws.scope["client"][0]
    server = jsonrpc.Channel(ws.send, ws.receive_iter())
    await _hub.agent_communicate(client_ip, server)


async def _handle_exporter(ws):
    await _authorize_ws(ws)
    client_ip = ws.scope["client"][0]
    exporter = jsonrpc.Channel(ws.send, ws.receive_iter())
    await _hub.exporter_communicate(client_ip, exporter)


async def _authorize_ws(ws):
    try:
        auth = ws.headers["authorization"]
        scheme, token = auth.split(" ", 1)
        if scheme != "Bearer":
            raise ProtocolError(f"Invalid Authorization Scheme: {scheme}")
        if token not in valid_tokens:
            raise ProtocolError("Invalid token")
    except Exception:
        traceback.print_exc()
        await ws.close()
        return

    await ws.accept()


class Hub:
    _places = {}
    _exporters = {}
    _available = set()
    _wait_queue = []
    _reservations = {}

    def __init__(self):
        self._id_generator = itertools.count(start=1)

    async def get_places(self):
        return {"places": [p.dict() for p in self._places.values()]}

    async def agent_communicate(self, client_ip, rpc):
        client_ip_var.set(client_ip)
        async with self._register_agent():
            rpc.set_api_object(self)
            await rpc.communicate_forever()

    async def exporter_communicate(self, client_ip, rpc):
        client_ip_var.set(client_ip)
        async with util.background_task(rpc.communicate_forever()) as com_task:
            export_desc = await rpc.get_place()
            with self._register_place(export_desc, rpc, client_ip):
                await com_task

    @contextlib.contextmanager
    def _register_place(self, export_desc, rpc, client_ip):
        id_ = next(self._id_generator)
        place = models.Place(id=id_, host=_unmap_ip(client_ip), **export_desc)

        try:
            logger.info("New place registered: %d", id_)
            self._places[id_] = place
            self._exporters[id_] = rpc
            self._available.add(id_)
            yield self
        finally:
            logger.info("Place disappeared: %d", id_)
            del self._places[id_]
            del self._exporters[id_]
            self._available.discard(id_)
            for candidates, _, future in self._wait_queue:
                candidates.discard(id_)
                if not candidates and not future.done():
                    future.set_exception(Exception("All candidate places are gone"))

    @contextlib.asynccontextmanager
    async def _register_agent(self):
        ctx = object()
        reservation_context_var.set(ctx)

        try:
            self._reservations[ctx] = set()
            yield
        finally:
            coros = [self.return_reservation(id_) for id_ in self._reservations[ctx]]
            results = await asyncio.gather(*coros, return_exceptions=True)
            del self._reservations[ctx]
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Error while deregistering agent: %s", result)

    async def reserve(self, candidate_ids):
        ctx = reservation_context_var.get()
        existing_candidates = {id_ for id_ in candidate_ids if id_ in self._places}
        if not existing_candidates:
            raise RuntimeError("None of the candidates exist anymore")

        available_candidates = existing_candidates & self._available
        if available_candidates:
            # TODO do something smart to get the best candidate
            reserved_id = random.choice(list(available_candidates))

            self._available.remove(reserved_id)
            self._reservations[ctx].add(reserved_id)
            logger.info("Place reserved: %d", reserved_id)
        else:
            logger.debug(
                "No places available, adding request to queue: %s",
                str(existing_candidates),
            )
            future = asyncio.get_running_loop().create_future()
            entry = (existing_candidates, ctx, future)
            self._wait_queue.append(entry)
            try:
                reserved_id = await future
            finally:
                self._wait_queue.remove(entry)

        client_ip = client_ip_var.get()
        async with util.on_error(self.return_reservation, reserved_id):
            rpc = self._exporters[reserved_id]
            await rpc.set_allowed_ips([_unmap_ip(client_ip)])

        return reserved_id

    async def return_reservation(self, place_id):
        ctx = reservation_context_var.get()
        self._reservations[ctx].remove(place_id)
        if place_id in self._places:
            for candidates, new_ctx, future in self._wait_queue:
                if place_id in candidates and not future.done():
                    self._reservations[new_ctx].add(place_id)
                    logger.info("Place returned and reserved again: %d", place_id)
                    future.set_result(place_id)
                    break
            else:
                logger.info("Place returned: %d", place_id)
                self._available.add(place_id)
                rpc = self._exporters[place_id]
                await rpc.set_allowed_ips([])
        else:
            logger.info("Place returned, but it doesn't exist: %d", place_id)


_hub = Hub()


def _unmap_ip(ip_str):
    """Resolve IPv4-mapped-on-IPv6 to an IPv4 address"""
    ip = ipaddress.ip_address(ip_str)
    unmapped = getattr(ip, "ipv4_mapped", None) or ip
    return str(unmapped)


class ProtocolError(Exception):
    pass
