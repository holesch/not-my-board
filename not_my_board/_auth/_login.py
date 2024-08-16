import asyncio
import json
import os
import pathlib
import string

import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

from ._openid import AuthRequest, ensure_fresh


class LoginFlow(util.ContextStack):
    def __init__(self, hub_url, http_client, token_store_path):
        self._hub_url = hub_url
        self._http = http_client
        self._show_claims = None
        self._token_store = _TokenStore(token_store_path)

        # fail early, if user doesn't have permission
        self._token_store.check_access()

    async def _context_stack(self, stack):
        url = f"{self._hub_url}/api/v1/auth-info"
        auth_info = await self._http.get_json(url)
        redirect_uri = f"{self._hub_url}/oidc-callback"

        self._request = await AuthRequest.create(
            auth_info["issuer"], auth_info["client_id"], redirect_uri, self._http
        )

        ready_event = asyncio.Event()
        notification_api = _HubNotifications(ready_event)

        channel_url = f"{self._hub_url}/ws"
        hub = jsonrpc.WebsocketChannel(
            channel_url, self._http, api_obj=notification_api
        )
        self._hub = await stack.enter_async_context(hub)

        coro = self._hub.get_authentication_response(self._request.state)
        self._auth_response_task = await stack.enter_async_context(
            util.background_task(coro)
        )

        await ready_event.wait()

        self._show_claims = auth_info.get("show_claims")

    async def finish(self):
        auth_response = await self._auth_response_task
        id_token, refresh_token, claims = await self._request.request_tokens(
            auth_response, self._http
        )

        async with self._token_store:
            self._token_store.save_tokens(self._hub_url, id_token, refresh_token)

        if self._show_claims is not None:
            # filter claims to only show relevant ones
            return {k: v for k, v in claims.items() if k in self._show_claims}
        else:
            return claims

    @property
    def login_url(self):
        return self._request.login_url


class _HubNotifications:
    def __init__(self, ready_event):
        self._ready_event = ready_event

    async def oidc_callback_registered(self):
        self._ready_event.set()


class IdTokenFromFile:
    def __init__(self, hub_url, http_client, token_store_path):
        self._hub_url = hub_url
        self._http = http_client
        self._token_store = _TokenStore(token_store_path)

    async def get_id_token(self):
        async with self._token_store:
            id_token, refresh_token = self._token_store.get_tokens(self._hub_url)
            id_token, refresh_token = await ensure_fresh(
                id_token, refresh_token, self._http
            )
            self._token_store.save_tokens(self._hub_url, id_token, refresh_token)

        return id_token


class IdTokenFromCmd:
    def __init__(self, hub_url, http_client, cmd_template):
        self._hub_url = hub_url
        self._http = http_client
        self._cmd_template = string.Template(cmd_template)
        self._cmd = None

    async def get_id_token(self):
        if self._cmd is None:
            url = f"{self._hub_url}/api/v1/auth-info"
            auth_info = await self._http.get_json(url)
            context = {k: auth_info[k] for k in ("issuer", "client_id")}
            self._cmd = self._cmd_template.safe_substitute(context)

        proc = await asyncio.create_subprocess_shell(
            self._cmd, stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"{self._cmd!r} exited with {proc.returncode}")
        return stdout.decode("utf-8").rstrip()


class _TokenStore(util.ContextStack):
    def __init__(self, path_str=None):
        self._path = pathlib.Path(path_str)

    def check_access(self):
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch(mode=0o600)

        if not os.access(self._path, os.R_OK | os.W_OK):
            raise RuntimeError(f"Not allowed to access {self._path}")

    async def _context_stack(self, stack):
        self._f = stack.enter_context(self._path.open("r+"))
        await stack.enter_async_context(util.flock(self._f))
        content = self._f.read()
        self._hub_tokens_map = json.loads(content) if content else {}

    def get_tokens(self, hub_url):
        if hub_url not in self._hub_tokens_map:
            raise RuntimeError("Login required")

        tokens = self._hub_tokens_map[hub_url]
        return tokens["id"], tokens["refresh"]

    def save_tokens(self, hub_url, id_token, refresh_token):
        new_tokens = {
            "id": id_token,
            "refresh": refresh_token,
        }
        old_tokens = self._hub_tokens_map.get(hub_url)

        if old_tokens != new_tokens:
            self._hub_tokens_map[hub_url] = new_tokens
            self._f.seek(0)
            self._f.truncate()
            self._f.write(json.dumps(self._hub_tokens_map))
