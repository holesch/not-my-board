#!/usr/bin/env python3

import functools
import contextlib
import asgineer
import json
import traceback
import not_my_board._jsonrpc as jsonrpc


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
    # import pdb; pdb.set_trace()

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
    receive_iter = ws.receive_iter()
    websocket_server = jsonrpc.Server(
            ws.send,
            receive_iter,
            WebsocketApi(ws.send, receive_iter))
    await websocket_server.serve_forever()


class WebsocketApi:
    def __init__(self, send, receive_iter):
        self._send = send
        self._receive_iter = receive_iter

    async def register_exporter(self, places):
        await Exporter(self._send, self._receive_iter, places).communicate()


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

    @classmethod
    def all(cls):
        return cls._all_places.values()

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
            cls._all_places[self._id] = self
            yield self
        finally:
            del cls._all_places[self._id]

    @property
    def desc(self):
        return self._desc
