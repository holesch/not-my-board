#!/usr/bin/env python3

import asyncio
import contextlib
import contextvars
import ipaddress
import itertools
import logging
import pathlib
import random
import traceback

import asgineer

import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._util as util

logger = logging.getLogger(__name__)
client_ip_var = contextvars.ContextVar("client_ip")
connection_id_var = contextvars.ContextVar("connection_id")


def run_hub():
    asgineer.run(asgi_app, "uvicorn", ":2092")


async def asgi_app(scope, receive, send):
    if scope["type"] == "lifespan":
        # asgineer doesn't expose the lifespan hooks. Handle them here
        # before handing over to asgineer
        await _handle_lifespan(scope, receive, send)
    else:
        # to_asgi() decorator adds extra arguments
        # pylint: disable-next=too-many-function-args
        await _handle_request(scope, receive, send)


async def _handle_lifespan(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            try:
                config_file = pathlib.Path("/etc/not-my-board/not-my-board-hub.toml")
                if config_file.exists():
                    config = util.toml_loads(config_file.read_text())
                else:
                    config = {}

                hub = Hub(config)
                await hub.startup()
                scope["state"]["hub"] = hub
            except Exception as err:
                await send({"type": "lifespan.startup.failed", "message": str(err)})
            else:
                await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            try:
                await hub.shutdown()
            except Exception as err:
                await send({"type": "lifespan.shutdown.failed", "message": str(err)})
            else:
                await send({"type": "lifespan.shutdown.complete"})
            return
        else:
            logger.warning("Unknown lifespan message %s", message["type"])


@asgineer.to_asgi
async def _handle_request(request):
    hub = request.scope["state"]["hub"]
    response = (404, {}, "Page not found")

    if isinstance(request, asgineer.WebsocketRequest):
        if request.path == "/ws":
            await _handle_websocket(hub, request)
        else:
            await request.close()
        response = None
    elif isinstance(request, asgineer.HttpRequest):
        if request.path == "/api/v1/places":
            response = await hub.get_places()
        elif request.path == "/api/v1/auth-info":
            response = hub.auth_info()
        elif request.path == "/oidc-callback":
            response = await hub.oidc_callback(request.querydict)
    return response


async def _handle_websocket(hub, ws):
    await ws.accept()
    client_ip = ws.scope["client"][0]
    channel = jsonrpc.Channel(ws.send, ws.receive_iter())
    await hub.communicate(client_ip, channel)


class Hub:
    _places = {}
    _exporters = {}
    _available = set()
    _wait_queue = []
    _reservations = {}
    _pending_callbacks = {}

    def __init__(self, config=None):
        if config is None:
            config = {}

        if "log_level" in config:
            log_level_str = config["log_level"]
            log_level_map = {
                "debug": logging.DEBUG,
                "info": logging.INFO,
                "warning": logging.WARNING,
                "error": logging.ERROR,
            }
            log_level = log_level_map[log_level_str]

            logging.basicConfig(
                format="%(levelname)s: %(name)s: %(message)s", level=log_level
            )

        auth_config = config.get("auth")
        if auth_config:
            required_keys = {"issuer", "client_id"}
            optional_keys = {"show_claims"}
            keys = required_keys | (optional_keys & auth_config.keys())
            self._auth_info = {k: auth_config[k] for k in keys}
        else:
            self._auth_info = {}

        self._id_generator = itertools.count(start=1)

    @jsonrpc.hidden
    async def startup(self):
        pass

    @jsonrpc.hidden
    async def shutdown(self):
        pass

    @jsonrpc.hidden
    async def get_places(self):
        return {"places": [p.dict() for p in self._places.values()]}

    @jsonrpc.hidden
    async def communicate(self, client_ip, channel):
        client_ip_var.set(client_ip)
        async with self._connection_context():
            channel.set_api_object(self)
            await channel.communicate_forever()

    @contextlib.asynccontextmanager
    async def _connection_context(self):
        id_ = next(self._id_generator)
        connection_id_var.set(id_)
        self._reservations[id_] = set()

        try:
            yield
        finally:
            if id_ in self._places:
                logger.info("Place disappeared: %d", id_)
                del self._places[id_]
                del self._exporters[id_]
                self._available.discard(id_)
                for candidates, _, future in self._wait_queue:
                    candidates.discard(id_)
                    if not candidates and not future.done():
                        future.set_exception(Exception("All candidate places are gone"))

            coros = [self.return_reservation(id_) for id_ in self._reservations[id_]]
            results = await asyncio.gather(*coros, return_exceptions=True)
            del self._reservations[id_]
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Error while deregistering agent: %s", result)

    async def register_place(self, export_desc):
        id_ = connection_id_var.get()
        client_ip = client_ip_var.get()
        place = models.Place(id=id_, host=_unmap_ip(client_ip), **export_desc)

        if id_ in self._places:
            raise RuntimeError("Place already registered")

        self._places[id_] = place
        self._exporters[id_] = jsonrpc.get_current_channel()
        self._available.add(id_)
        logger.info("New place registered: %d", id_)
        return id_

    async def reserve(self, candidate_ids):
        id_ = connection_id_var.get()
        existing_candidates = {c_id for c_id in candidate_ids if c_id in self._places}
        if not existing_candidates:
            raise RuntimeError("None of the candidates exist anymore")

        available_candidates = existing_candidates & self._available
        if available_candidates:
            # TODO do something smart to get the best candidate
            reserved_id = random.choice(list(available_candidates))

            self._available.remove(reserved_id)
            self._reservations[id_].add(reserved_id)
            logger.info("Place %d reserved by %d", reserved_id, id_)
        else:
            logger.debug(
                "No places available, adding request to queue: %s",
                str(existing_candidates),
            )
            future = asyncio.get_running_loop().create_future()
            entry = (existing_candidates, id_, future)
            self._wait_queue.append(entry)
            try:
                reserved_id = await future
            finally:
                self._wait_queue.remove(entry)

        client_ip = client_ip_var.get()
        async with util.on_error(self.return_reservation, reserved_id):
            exporter = self._exporters[reserved_id]
            await exporter.set_allowed_ips([_unmap_ip(client_ip)])

        return reserved_id

    async def return_reservation(self, place_id):
        id_ = connection_id_var.get()
        self._reservations[id_].remove(place_id)
        if place_id in self._places:
            for candidates, agent_id, future in self._wait_queue:
                if place_id in candidates and not future.done():
                    self._reservations[agent_id].add(place_id)
                    logger.info("Place %d returned by %d was reserved by %d", place_id, id_, agent_id)
                    future.set_result(place_id)
                    break
            else:
                logger.info("Place %d returned by %d", place_id, id_)
                self._available.add(place_id)
                rpc = self._exporters[place_id]
                await rpc.set_allowed_ips([])
        else:
            logger.info("Place %d returned, but it doesn't exist", place_id)

    async def get_authentication_response(self, state):
        future = asyncio.get_running_loop().create_future()
        self._pending_callbacks[state] = future
        try:
            channel = jsonrpc.get_current_channel()
            await channel.oidc_callback_registered(_notification=True)
            return await future
        finally:
            del self._pending_callbacks[state]

    @jsonrpc.hidden
    async def oidc_callback(self, query):
        future = self._pending_callbacks[query["state"]]
        if not future.done():
            future.set_result(query)

        return "Continue in not-my-board CLI"

    @jsonrpc.hidden
    def auth_info(self):
        return self._auth_info


def _unmap_ip(ip_str):
    """Resolve IPv4-mapped-on-IPv6 to an IPv4 address"""
    ip = ipaddress.ip_address(ip_str)
    unmapped = getattr(ip, "ipv4_mapped", None) or ip
    return str(unmapped)


class ProtocolError(Exception):
    pass
