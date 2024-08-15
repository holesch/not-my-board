import asyncio
import base64
import contextlib
import datetime
import hashlib
import json
import pathlib
import secrets
import urllib.parse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import not_my_board._auth as auth
import not_my_board._hub as hubmodule
import not_my_board._jsonrpc as jsonrpc
import not_my_board._util as util

from .util import fake_rpc_pair

HUB_URL = "http://not-my-board.example.com"
ISSUER_URL = "http://oidc.example.com"
CLIENT_ID = "not-my-board"
EXPECTED_TOKEN_LEN = len(secrets.token_urlsafe())
USER_NAME = "test-user"
real_sleep = asyncio.sleep


class FakeHttpClient:
    def __init__(self):
        self._private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self._key_id = secrets.token_urlsafe()
        self._refresh_token = None
        self._nonce = None
        self._sub = USER_NAME

    def set_hub(self, hub_):
        self._hub = hub_

    def set_sub(self, sub):
        self._sub = sub

    async def get_json(self, url, cache=None):  # noqa: ARG002
        if url == f"{HUB_URL}/api/v1/auth-info":
            response = self._hub.auth_info()
        elif url == f"{ISSUER_URL}/.well-known/openid-configuration":
            response = {
                "issuer": ISSUER_URL,
                "authorization_endpoint": f"{ISSUER_URL}/authorize",
                "token_endpoint": f"{ISSUER_URL}/token",
                "jwks_uri": f"{ISSUER_URL}/jwks",
            }
        elif url == f"{ISSUER_URL}/jwks":
            alg = jwt.get_algorithm_by_name("RS256")
            jwk = alg.to_jwk(self._private_key.public_key(), as_dict=True)
            jwk["use"] = "sig"
            jwk["kid"] = self._key_id
            response = {"keys": [jwk]}
        else:
            raise RuntimeError(f"URL not found: {url}")

        return response

    async def oidc_login(self, url):
        url_parts = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(url_parts[4]))

        expected_url_parts = urllib.parse.urlparse(f"{ISSUER_URL}/authorize")
        assert url_parts.scheme == expected_url_parts.scheme
        assert url_parts.netloc == expected_url_parts.netloc
        assert url_parts.path == expected_url_parts.path

        assert set(query["scope"].split()) == {
            "openid",
            "profile",
            "email",
            "offline_access",
        }
        assert query["response_type"] == "code"
        assert query["client_id"] == CLIENT_ID
        assert query["redirect_uri"] == f"{HUB_URL}/oidc-callback"
        assert query["code_challenge_method"] == "S256"
        for key in ("state", "nonce"):
            assert len(query[key]) == EXPECTED_TOKEN_LEN
        self._code_challenge = query["code_challenge"]
        self._nonce = query["nonce"]

        self._auth_code = secrets.token_urlsafe()
        callback_query = {"state": query["state"], "code": self._auth_code}
        await self._hub.oidc_callback(callback_query)

    async def post_form(self, url, params):
        if url == f"{ISSUER_URL}/token":
            if params["grant_type"] == "authorization_code":
                assert params["code"] == self._auth_code
                assert params["redirect_uri"] == f"{HUB_URL}/oidc-callback"
                assert params["client_id"] == CLIENT_ID
                hashed = hashlib.sha256(params["code_verifier"].encode()).digest()
                code_challenge = (
                    base64.urlsafe_b64encode(hashed).rstrip(b"=").decode("ascii")
                )
                assert code_challenge == self._code_challenge
            elif params["grant_type"] == "refresh_token":
                assert self._refresh_token
                assert params["refresh_token"] == self._refresh_token
                assert params["client_id"] == CLIENT_ID
            else:
                raise RuntimeError(f'Unknown grant_type "{params["grant_type"]}"')

            self._refresh_token = secrets.token_urlsafe()
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            claims = {
                "iss": ISSUER_URL,
                "sub": self._sub,
                "aud": CLIENT_ID,
                "exp": now + datetime.timedelta(seconds=30),
                "iat": now,
            }
            if self._nonce is not None:
                claims["nonce"] = self._nonce

            headers = {"kid": self._key_id}
            id_token = jwt.encode(
                claims, self._private_key, algorithm="RS256", headers=headers
            )

            return {
                "token_type": "Bearer",
                "refresh_token": self._refresh_token,
                "id_token": id_token,
            }

    @contextlib.asynccontextmanager
    async def websocket(self, url):
        if url == f"{HUB_URL}/ws":
            client_to_hub = asyncio.Queue()
            hub_to_client = asyncio.Queue()

            async def receive_iter(queue):
                while True:
                    data = await queue.get()
                    yield data
                    queue.task_done()

            channel = jsonrpc.Channel(hub_to_client.put, receive_iter(client_to_hub))
            coro = self._hub.communicate("", channel)
            async with util.background_task(coro):
                yield FakeWebsocket(client_to_hub.put, receive_iter(hub_to_client))
        else:
            raise RuntimeError(f"URL not found: {url}")

    def issue_tokens(self, claims):
        if not self._refresh_token:
            self._refresh_token = secrets.token_urlsafe()
        headers = {"kid": self._key_id}
        id_token = jwt.encode(
            claims, self._private_key, algorithm="RS256", headers=headers
        )

        return {
            "id": id_token,
            "refresh": self._refresh_token,
        }


class FakeWebsocket:
    def __init__(self, send, receive_iter):
        self._send = send
        self._receive_iter = receive_iter

    async def receive_iter(self):
        async for msg in self._receive_iter:
            yield msg

    async def send(self, data):
        await self._send(data)


@pytest.fixture
def http_client():
    return FakeHttpClient()


@pytest.fixture
def hub(http_client):
    config = {
        "auth": {
            "issuer": ISSUER_URL,
            "client_id": CLIENT_ID,
            "permissions": [
                {
                    "claims": {"sub": USER_NAME},
                    "roles": ["exporter", "importer"],
                }
            ],
        },
    }
    return hubmodule.Hub(config, http_client)


@pytest.fixture
def token_store_path():
    path = pathlib.Path(__file__).parent / "auth_tokens.json"
    path.unlink(missing_ok=True)
    yield path
    path.unlink(missing_ok=True)


class FakeExporter:
    def __init__(self, rpc, token_src, http):
        rpc.set_api_object(self)
        self._rpc = rpc
        self._http = http
        self._token_src = token_src

    async def communicate_forever(self):
        await self._rpc.communicate_forever()

    async def register_place(self):
        place = {
            "port": 1234,
            "parts": [],
        }
        await self._rpc.register_place(place)

    async def get_id_token(self):
        return await self._token_src.get_id_token()


class FakeTokenSource:
    def __init__(self, id_token):
        self._id_token = id_token

    async def get_id_token(self):
        return self._id_token


async def test_login_success(hub, http_client, token_store_path):
    http_client.set_hub(hub)
    async with auth.LoginFlow(HUB_URL, http_client, token_store_path) as login:
        url = login.login_url
        await http_client.oidc_login(url)

        async with util.timeout(2):
            claims = await login.finish()

        assert claims["iss"] == ISSUER_URL
        assert claims["sub"] == USER_NAME
        assert claims["aud"] == CLIENT_ID

    rpc1, rpc2 = fake_rpc_pair()
    token_src = auth.IdTokenFromFile(HUB_URL, http_client, token_store_path)
    fake_exporter = FakeExporter(rpc1, token_src, http_client)
    hub_coro = hub.communicate("3.1.1.1", rpc2)
    exporter_coro = fake_exporter.communicate_forever()
    async with util.background_task(hub_coro):
        async with util.background_task(exporter_coro):
            await fake_exporter.register_place()


@pytest.mark.parametrize(
    ("claims", "is_allowed"),
    [
        ({"sub": USER_NAME}, True),
        ({"sub": "unauthorized-user"}, False),
        ({"custom_claim": "x"}, True),
        ({"list": [1, 2, 3]}, True),
        ({"list": [1, 2]}, False),  # less than required
        ({"list": [1, 2, 3, 4]}, True),  # more than required
        ({"sub": USER_NAME, "exp": 42}, False),  # expired
        ({"iss": "http://evil.example.com"}, False),  # not a trusted issuer
    ],
)
async def test_permissions(claims, is_allowed):
    allowed_claims = [
        {"sub": USER_NAME},
        {"custom_claim": "x"},
        {"list": [1, 2, 3]},
    ]
    config = {
        "auth": {
            "issuer": ISSUER_URL,
            "client_id": CLIENT_ID,
            "permissions": [
                {"claims": c, "roles": ["exporter"]} for c in allowed_claims
            ],
        },
    }

    http_client_ = FakeHttpClient()
    hub_ = hubmodule.Hub(config, http_client_)
    http_client_.set_hub(hub_)

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    full_claims = claims.copy()
    full_claims.setdefault("iss", ISSUER_URL)
    full_claims.setdefault("sub", secrets.token_urlsafe())
    full_claims.setdefault("aud", CLIENT_ID)
    full_claims.setdefault("exp", now + datetime.timedelta(seconds=30))
    full_claims.setdefault("iat", now)

    tokens = http_client_.issue_tokens(full_claims)

    rpc1, rpc2 = fake_rpc_pair()
    token_src = FakeTokenSource(tokens["id"])
    fake_exporter = FakeExporter(rpc1, token_src, http_client_)
    hub_coro = hub_.communicate("3.1.1.1", rpc2)
    exporter_coro = fake_exporter.communicate_forever()
    async with util.background_task(hub_coro):
        async with util.background_task(exporter_coro):
            try:
                await fake_exporter.register_place()
            except Exception:
                assert not is_allowed
            else:
                assert is_allowed


async def test_permission_lost(hub, http_client, token_store_path, fake_time):
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    initial_claims = {
        "iss": ISSUER_URL,
        "sub": USER_NAME,
        "aud": CLIENT_ID,
        "exp": now + datetime.timedelta(seconds=30),
        "iat": now,
    }

    tokens = http_client.issue_tokens(initial_claims)
    token_store_content = {HUB_URL: tokens}
    token_store_path.write_text(json.dumps(token_store_content))

    rpc1, rpc2 = fake_rpc_pair()
    token_src = auth.IdTokenFromFile(HUB_URL, http_client, token_store_path)
    fake_exporter = FakeExporter(rpc1, token_src, http_client)
    hub_coro = hub.communicate("3.1.1.1", rpc2)
    exporter_coro = fake_exporter.communicate_forever()

    with pytest.raises(RuntimeError) as execinfo:
        async with util.background_task(hub_coro):
            async with util.background_task(exporter_coro):
                await fake_exporter.register_place()
                http_client.set_sub("invalid-user")
                fake_time.add_time(60)
                # Token expires and hub requests refresh. New token contains
                # a different "sub" claim, which has no permissions.
                await real_sleep(1)

    assert "Permission lost" in str(execinfo.value)


@pytest.fixture
def fake_time(monkeypatch):
    fake_time_ = FakeTime()
    fake_datetime = fake_time_.fake_datetime()
    monkeypatch.setattr(datetime, "datetime", fake_datetime)
    monkeypatch.setattr(jwt.api_jwt, "datetime", fake_datetime)
    monkeypatch.setattr(asyncio, "sleep", fake_time_.fake_sleep)
    return fake_time_


class FakeTime:
    def __init__(self):
        self._now = 0
        self._waiters = set()

    def fake_datetime(self):
        class FakeDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(self.now, tz)

        return FakeDateTime

    @property
    def now(self):
        return self._now

    async def fake_sleep(self, delay):
        if delay <= 0:
            return

        end_time = self._now + delay
        event = asyncio.Event()
        entry = (end_time, event)
        self._waiters.add(entry)
        try:
            await event.wait()
        finally:
            self._waiters.remove(entry)

    def add_time(self, time):
        self._now += time
        for end_time, event in self._waiters:
            if end_time >= self._now:
                event.set()
