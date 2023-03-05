#!/usr/bin/env python3

import functools
import contextlib
import asgineer
import json
import traceback
import not_my_board._jsonrpc as jsonrpc
import random
import logging

logger = logging.getLogger(__name__)
valid_tokens = ("dummy-token-1", "dummy-token-2")


def serve():
    asgineer.run(asgi_app, 'uvicorn', 'localhost:2092')


@asgineer.to_asgi
async def asgi_app(request):
    if request.path == "/ws" and isinstance(request, asgineer.WebsocketRequest):
        return await websocket_handler(request)
    elif request.path == "/api/v1/places":
        return { "places": [p.desc for p in Place.all()] }
    return 404, {}, 'Page not found'


async def websocket_handler(ws):
    try:
        auth = ws.headers["authorization"]
        scheme, token = auth.split(" ", 1)
        if scheme != "Bearer":
            raise Exception(f"Invalid Authorization Scheme: {scheme}")
        if token not in valid_tokens:
            raise Exception("Invalid token")
    except Exception:
        traceback.print_exc()
        await ws.close()
        return

    await ws.accept()

    with Place.reservation_context() as ctx:
        receive_iter = ws.receive_iter()
        ws_api = WebsocketApi(ws.send, receive_iter, ctx)
        websocket_server = jsonrpc.Server(ws.send, receive_iter, ws_api)
        await websocket_server.serve_forever()


class WebsocketApi:
    def __init__(self, send, receive_iter, reservation_context):
        self._send = send
        self._receive_iter = receive_iter
        self._reservation_context = reservation_context

    async def register_exporter(self, places):
        await Exporter(self._send, self._receive_iter, places).communicate()

    async def reserve(self, candidate_ids):
        place = await Place.reserve(candidate_ids, self._reservation_context)
        return place.desc

    async def return_reservation(self, place_id):
        Place.return_by_id(place_id, self._reservation_context)


class Exporter:
    def __init__(self, send, receive_iter, places):
        self._send = send
        self._receive_iter = receive_iter
        self._places_desc = places

    async def communicate(self):
        with contextlib.ExitStack() as stack:
            self._places = [stack.enter_context(Place.register(desc, self))
                for desc in self._places_desc]

            async for json_msg in self._receive_iter:
                msg = json.loads(json_msg)
                print(msg)


class Place:
    _all_places = dict()
    _next_id = 1
    _available = set()
    _wait_queue = list()
    _reservations = dict()

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
    def register(cls, desc, exporter):
        self = cls()
        self._desc = desc
        self._exporter = exporter

        self._id = cls._new_id()
        self._desc["id"] = self._id
        try:
            logger.info(f"New place registered: {self._id}")
            cls._all_places[self._id] = self
            cls._available.add(self._id)
            yield self
        finally:
            logger.info(f"Place disappeared: {self._id}")
            del cls._all_places[self._id]
            cls._available.discard(self._id)
            for candidates, future in cls._wait_queue:
                candidates.discard(self._id)
                if not candidates and not future.done():
                    future.set_exception(Exception("All candidate places are gone"))

    @property
    def desc(self):
        return self._desc

    @classmethod
    @contextlib.contextmanager
    def reservation_context(cls):
        ctx = object()
        try:
            cls._reservations[ctx] = set()
            yield ctx
        finally:
            for place in cls._reservations[ctx]:
                cls.return_by_id(place, ctx)
            del cls._reservations[ctx]

    @classmethod
    async def reserve(cls, candidate_ids, ctx):
        existing_candidates = {id_ for id_ in candidate_ids
                if id_ in cls._all_places}
        if not existing_candidates:
            raise RuntimeError("None of the candidates exist anymore")

        available_candidates = existing_candidates & cls._available
        if available_candidates:
            # TODO do something smart to get the best candidate
            reserved_id = random.choice(list(available_candidates))

            cls._available.remove(reserved_id)
            cls._reservations[ctx].add(reserved_id)
            logger.info(f"Place reserved: {reserved_id}")
            return cls._all_places[reserved_id]
        else:
            logger.debug(f"No places available, adding request to queue: {existing_candidates}")
            future = asyncio.get_running_loop().create_future()
            entry = (existing_candidates, ctx, future)
            cls._wait_queue.append(entry)
            try:
                return await future
            finally:
                cls._wait_queue.remove(entry)

    @classmethod
    def return_by_id(cls, place_id, ctx):
        cls._reservations[ctx].remove(place_id)
        if place_id in cls._all_places:
            for candidates, new_ctx, future in cls._wait_queue:
                if place_id in candidates and not future.done():
                    cls._reservations[new_ctx].add(place_id)
                    logger.info(f"Place returned and reserved again: {place_id}")
                    future.set_result(cls._all_places[place_id])
                    break
            else:
                logger.info(f"Place returned: {place_id}")
                cls._available.add(place_id)
