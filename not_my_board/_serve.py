#!/usr/bin/env python3

import asyncio
import contextlib
import logging
import random
import traceback

import asgineer

import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

logger = logging.getLogger(__name__)
valid_tokens = ("dummy-token-1", "dummy-token-2")


def serve():
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
            return {"places": [p.desc for p in Place.all()]}
    return 404, {}, "Page not found"


async def _handle_agent(ws):
    await _authorize_ws(ws)
    client_ip = ws.scope["client"][0]
    async with Place.reservation_context(client_ip) as ctx:
        api = AgentAPI(ctx)
        server = jsonrpc.Server(ws.send, ws.receive_iter(), api)
        await server.serve_forever()


class AgentAPI:
    def __init__(self, reservation_context):
        self._reservation_context = reservation_context

    async def reserve(self, candidate_ids):
        place = await Place.reserve(candidate_ids, self._reservation_context)
        return place.desc["id"]

    async def return_reservation(self, place_id):
        await Place.return_by_id(place_id, self._reservation_context)


async def _handle_exporter(ws):
    await _authorize_ws(ws)
    client_ip = ws.scope["client"][0]
    exporter = jsonrpc.Proxy(ws.send, ws.receive_iter())
    async with util.background_task(exporter.io_loop()) as io_loop:
        place = await exporter.get_place()
        with Place.register(place, exporter, client_ip):
            await io_loop


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


class Place:
    _all_places = {}
    _next_id = 1
    _available = set()
    _wait_queue = []
    _reservations = {}

    @classmethod
    def all(cls):
        return cls._all_places.values()

    @classmethod
    def get_by_id(cls, id_, default=None):
        return cls._all_places.get(id_, default)

    @classmethod
    def _new_id(cls):
        id_ = cls._next_id
        cls._next_id += 1
        return id_

    @classmethod
    @contextlib.contextmanager
    def register(cls, desc, exporter, client_ip):
        self = cls()
        self._desc = desc
        self._exporter = exporter

        self._id = cls._new_id()
        self._desc["id"] = self._id
        self._desc["host"] = client_ip
        try:
            logger.info("New place registered: %d", self._id)
            cls._all_places[self._id] = self
            cls._available.add(self._id)
            yield self
        finally:
            logger.info("Place disappeared: %d", self._id)
            del cls._all_places[self._id]
            cls._available.discard(self._id)
            for candidates, _, future in cls._wait_queue:
                candidates.discard(self._id)
                if not candidates and not future.done():
                    future.set_exception(Exception("All candidate places are gone"))

    @property
    def desc(self):
        return self._desc

    @classmethod
    @contextlib.asynccontextmanager
    async def reservation_context(cls, client_ip):
        ctx = _ReservationContext(client_ip)
        try:
            cls._reservations[ctx] = set()
            yield ctx
        finally:
            for place in cls._reservations[ctx].copy():
                await cls.return_by_id(place, ctx)
            del cls._reservations[ctx]

    @classmethod
    async def reserve(cls, candidate_ids, ctx):
        existing_candidates = {id_ for id_ in candidate_ids if id_ in cls._all_places}
        if not existing_candidates:
            raise RuntimeError("None of the candidates exist anymore")

        available_candidates = existing_candidates & cls._available
        if available_candidates:
            # TODO do something smart to get the best candidate
            reserved_id = random.choice(list(available_candidates))

            cls._available.remove(reserved_id)
            cls._reservations[ctx].add(reserved_id)
            logger.info("Place reserved: %d", reserved_id)
            place = cls._all_places[reserved_id]
        else:
            logger.debug(
                "No places available, adding request to queue: %s",
                str(existing_candidates),
            )
            future = asyncio.get_running_loop().create_future()
            entry = (existing_candidates, ctx, future)
            cls._wait_queue.append(entry)
            try:
                place = await future
            finally:
                cls._wait_queue.remove(entry)

        # TODO refactor Place class
        # pylint: disable=protected-access
        try:
            await place._exporter.set_allowed_ips([ctx.client_ip])
        except Exception:
            await cls.return_by_id(place._id, ctx)
            raise

        return place

    @classmethod
    async def return_by_id(cls, place_id, ctx):
        cls._reservations[ctx].remove(place_id)
        if place_id in cls._all_places:
            for candidates, new_ctx, future in cls._wait_queue:
                if place_id in candidates and not future.done():
                    cls._reservations[new_ctx].add(place_id)
                    logger.info("Place returned and reserved again: %d", place_id)
                    future.set_result(cls._all_places[place_id])
                    break
            else:
                logger.info("Place returned: %d", place_id)
                cls._available.add(place_id)
                # pylint: disable=protected-access
                await cls._all_places[place_id]._exporter.set_allowed_ips([])
        else:
            logger.info("Place returned, but it doesn't exist: %d", place_id)


class _ReservationContext:
    def __init__(self, client_ip):
        self._client_ip = client_ip

    @property
    def client_ip(self):
        return self._client_ip


class ProtocolError(Exception):
    pass
