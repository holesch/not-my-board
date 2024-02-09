import asyncio
import json
import os
import pathlib

import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

from ._openid import Client, IdentityProvider, ensure_fresh

state_home = pathlib.Path(
    os.environ.get("XDG_STATE_HOME", pathlib.Path.home() / ".local/state")
)
token_store = state_home / "not-my-board/auth_tokens.json"


class LoginFlow(util.ContextStack):
    def __init__(self, hub_url):
        self._hub_url = hub_url

    async def _context_stack(self, stack):
        # todo from settings
        self._client_id = "6e2750e5-4f1e-42d8-bdcf-7c794c154e01"
        self._issuer = "https://login.microsoftonline.com/common/v2.0"
        redirect_uri = f"{self._hub_url}/oidc-callback"

        identity_provider = await IdentityProvider.from_url(self._issuer)
        self._client = Client(self._client_id, identity_provider, redirect_uri)

        ready_event = asyncio.Event()
        notification_api = _HubNotifications(ready_event)

        channel_url = f"{self._hub_url}/ws-login"
        hub = jsonrpc.WebsocketChannel(channel_url, api_obj=notification_api)
        self._hub = await stack.enter_async_context(hub)

        coro = self._hub.get_authentication_response(self._client.state)
        self._auth_response_task = await stack.enter_async_context(
            util.background_task(coro)
        )

        await ready_event.wait()

    async def finish(self):
        auth_response = await self._auth_response_task
        tokens = await self._client.request_tokens(auth_response)

        # drop unused enries, like "access_token"
        to_store = {k: tokens[k] for k in ("refresh_token", "id_token")}

        if not token_store.exists():
            token_store.parent.mkdir(parents=True, exist_ok=True)
            token_store.touch(mode=0o600)

        with token_store.open("r+") as f:
            async with util.flock(f):
                f.seek(0)
                f.truncate()
                f.write(json.dumps(to_store))

    @property
    def login_url(self):
        return self._client.login_url


class _HubNotifications:
    def __init__(self, ready_event):
        self._ready_event = ready_event

    async def oidc_callback_registered(self):
        self._ready_event.set()


async def get_id_token():
    # todo from hub
    client_id = "6e2750e5-4f1e-42d8-bdcf-7c794c154e01"
    issuer = "https://login.microsoftonline.com/common/v2.0"

    identity_provider = await IdentityProvider.from_url(issuer)

    with token_store.open("r+") as f:
        async with util.flock(f):
            tokens = json.loads(f.read())
            fresh_tokens = ensure_fresh(tokens, identity_provider, client_id)
            if fresh_tokens != tokens:
                to_store = {k: fresh_tokens[k] for k in ("refresh_token", "id_token")}
                f.seek(0)
                f.truncate()
                f.write(json.dumps(to_store))

    return fresh_tokens["id_token"]
