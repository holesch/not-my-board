#!/usr/bin/env python3

import asyncio
import contextlib
import contextvars
import datetime
import functools
import ipaddress
import itertools
import logging
import os
import pathlib
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import asgineer

import not_my_board._auth as auth
import not_my_board._http as http
import not_my_board._jsonrpc as jsonrpc
import not_my_board._models as models
import not_my_board._util as util

logger = logging.getLogger(__name__)
client_ip_var = contextvars.ContextVar("client_ip")
connection_id_var = contextvars.ContextVar("connection_id")
authenticator_var = contextvars.ContextVar("authenticator")


def run_hub():
    import socket

    import uvicorn

    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, False)

        host = "::"
        port = 2092
        s.bind((host, port))

        s.listen()
        print("ready", flush=True)  # noqa: T201

        fd = s.fileno()
        uvicorn.main([f"--fd={fd}", __name__ + ":asgi_app"])


async def asgi_app(scope, receive, send):
    if scope["type"] == "lifespan":
        # asgineer doesn't expose the lifespan hooks. Handle them here
        # before handing over to asgineer
        await _handle_lifespan(scope, receive, send)
    else:
        await _handle_request(scope, receive, send)


async def _handle_lifespan(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            try:
                hub = await _on_startup()
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


async def _on_startup():
    config_file = os.environ.get("NOT_MY_BOARD_HUB_CONFIG")
    if not config_file:
        config_file = "/etc/not-my-board/hub.toml"
    config_file = pathlib.Path(config_file)

    if config_file.exists():
        config = util.toml_loads(config_file.read_text())
    else:
        logger.warning('Config file "%s" not found', config_file)
        config = {}

    hub = Hub(config, http.Client())
    await hub.startup()

    return hub


@asgineer.to_asgi
async def _handle_request(request):
    util.generate_log_request_id()
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


def require_role(role):
    def decorator(func):
        @functools.wraps(func)
        async def require_role_wrapper(*args, **kwargs):
            authenticator = authenticator_var.get()
            await authenticator.require_role(role)
            return await func(*args, **kwargs)

        return require_role_wrapper

    return decorator


class Hub:
    def __init__(self, config=None, http_client=None):
        self._places = {}
        self._exporters = {}
        self._available = set()
        self._wait_queue = []
        self._reservations = {}
        self._pending_callbacks = {}

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
            util.configure_logging(log_level)

        auth_config = config.get("auth")
        if auth_config:
            trusted_issuers = {auth_config["issuer"]}
            for permission in auth_config["permissions"]:
                issuer = permission["claims"].get("iss")
                if issuer:
                    trusted_issuers.add(issuer)

            def make_issuer_config(issuer):
                issuer_config = auth_config.get("issuers", {}).get(issuer, {})
                return IssuerConfig(**issuer_config)

            self._issuer_configs = {
                issuer: make_issuer_config(issuer) for issuer in trusted_issuers
            }

            keys = {"issuer", "client_id"}
            self._auth_info = {k: auth_config[k] for k in keys}
            issuer_config = self._issuer_configs[auth_config["issuer"]]
            if issuer_config.show_claims is not None:
                self._auth_info["show_claims"] = issuer_config.show_claims

            def make_permission(d):
                d["claims"].setdefault("iss", auth_config["issuer"])
                return Permission.from_dict(d)

            self._permissions = [make_permission(d) for d in auth_config["permissions"]]
            self._validator = auth.Validator(
                auth_config["client_id"], http_client, trusted_issuers
            )
        else:
            logger.warning("Authentication is disabled")
            self._auth_info = {}
            self._permissions = []
            self._validator = None
            self._issuer_configs = {}

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
        async with self._connection_context(channel):
            channel.set_api_object(self)
            await channel.communicate_forever()

    @contextlib.asynccontextmanager
    async def _connection_context(self, channel):
        id_ = next(self._id_generator)
        connection_id_var.set(id_)
        self._reservations[id_] = set()
        authenticator = Authenticator(
            self._permissions, self._validator, channel, self._issuer_configs
        )

        authenticator_var.set(authenticator)

        try:
            async with authenticator:
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
                        future.set_exception(
                            RuntimeError("All candidate places are gone")
                        )

            coros = [self.return_reservation(id_) for id_ in self._reservations[id_]]
            results = await asyncio.gather(*coros, return_exceptions=True)
            del self._reservations[id_]
            for result in results:
                if isinstance(result, Exception):
                    logger.warning("Error while deregistering agent: %s", result)

    @require_role("exporter")
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

    @require_role("importer")
    async def reserve(self, candidate_ids):
        id_ = connection_id_var.get()
        existing_candidates = {c_id for c_id in candidate_ids if c_id in self._places}
        if not existing_candidates:
            raise RuntimeError("None of the candidates exist anymore")

        available_candidates = existing_candidates & self._available
        if available_candidates:
            # TODO do something smart to get the best candidate
            reserved_id = random.choice(list(available_candidates))  # noqa: S311

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

    @require_role("importer")
    async def return_reservation(self, place_id):
        id_ = connection_id_var.get()
        self._reservations[id_].remove(place_id)
        if place_id in self._places:
            for candidates, agent_id, future in self._wait_queue:
                if place_id in candidates and not future.done():
                    self._reservations[agent_id].add(place_id)
                    logger.info(
                        "Place %d returned by %d was reserved by %d",
                        place_id,
                        id_,
                        agent_id,
                    )
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


class Authenticator(util.ContextStack):
    _leeway = datetime.timedelta(seconds=30)
    _timeout = datetime.timedelta(seconds=30)

    def __init__(self, permissions, validator, channel, issuer_configs):
        self._permissions = permissions
        self._validator = validator
        self._channel = channel
        self._issuer_configs = issuer_configs
        self._required_roles = set()
        self._refresh_start_event = asyncio.Event()
        self._roles = None
        self._expires = None
        self._roles_lock = asyncio.Lock()
        self._previous_claims = None

    async def _context_stack(self, stack):
        if self._validator:
            coro = self._refresh_token()
            await stack.enter_async_context(util.background_task(coro))

    async def require_role(self, role):
        if not self._validator:
            # authentication is disabled
            return

        def check(roles):
            if role not in roles:
                raise RuntimeError(f'Permission denied: requires role "{role}"')

        async with self._roles_lock:
            if self._roles is None:
                roles, expires = await self._request_roles()
                check(roles)
                self._roles = roles
                self._expires = expires
                self._refresh_start_event.set()
            else:
                check(self._roles)

            self._required_roles.add(role)

    async def _request_roles(self):
        id_token = await self._channel.get_id_token()
        token_claims = await self._validator.extract_claims(
            id_token, leeway=self._leeway.total_seconds()
        )
        roles = set()

        if logger.isEnabledFor(logging.INFO):
            show_claims = self._issuer_configs[token_claims["iss"]].show_claims
            if show_claims is not None:
                filtered_claims = [
                    (c, token_claims[c]) for c in show_claims if c in token_claims
                ]
            else:
                filtered_claims = list(token_claims.items())

            if filtered_claims and filtered_claims != self._previous_claims:
                claims_str = ", ".join([f"{k!r}: {v!r}" for k, v in filtered_claims])
                logger.info("Token claims: %s", claims_str)
                self._previous_claims = filtered_claims

        for permission in self._permissions:
            if permission.roles <= roles:
                # permission rule has no new roles, skip
                continue

            for key, required_claim in permission.claims.items():
                token_claim = token_claims.get(key)
                if isinstance(required_claim, set):
                    if (
                        not isinstance(token_claim, list)
                        or set(token_claim) < required_claim
                    ):
                        break
                elif token_claim != required_claim:
                    break
            else:
                # Token has all needed claims. Add roles.
                roles.update(permission.roles)

        expires = datetime.datetime.fromtimestamp(
            int(token_claims["exp"]), tz=datetime.timezone.utc
        )
        return roles, expires

    async def _refresh_token(self):
        await self._refresh_start_event.wait()

        # This is run as a background task. Every exception closes the
        # connection.
        while True:
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            delay = (self._expires - now) + self._leeway
            logger.debug(
                "Token expires at %s, refreshing in %d seconds",
                str(self._expires),
                delay.total_seconds(),
            )
            await asyncio.sleep(delay.total_seconds())

            async with util.timeout(self._timeout.total_seconds()):
                roles, expires = await self._request_roles()

            self._roles = roles
            self._expires = expires

            if self._required_roles > self._roles:
                raise RuntimeError("Permission lost")


@dataclass
class Permission:
    claims: Dict[str, Union[dict, str, int, float, set, bool]]
    roles: set

    @classmethod
    def from_dict(cls, d):
        claims = {}
        for key, value in d["claims"].items():
            if isinstance(value, list):
                claims[key] = set(value)
            else:
                claims[key] = value

        return cls(claims, set(d["roles"]))


@dataclass
class IssuerConfig:
    show_claims: Optional[List[str]] = None


def _unmap_ip(ip_str):
    """Resolve IPv4-mapped-on-IPv6 to an IPv4 address"""
    ip = ipaddress.ip_address(ip_str)
    unmapped = getattr(ip, "ipv4_mapped", None) or ip
    return str(unmapped)


class ProtocolError(Exception):
    pass
