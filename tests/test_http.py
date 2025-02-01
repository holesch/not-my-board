import asyncio
import contextlib
import datetime
import email
import json
import pathlib

import h11
import pytest

import not_my_board._http as http
import not_my_board._util as util

from .util import sh, sh_task, wait_for_ports

project_dir = pathlib.Path(__file__).parents[1]


@pytest.fixture(scope="session")
async def tinyproxy():
    conf = project_dir / "tests/tinyproxy.conf"
    async with sh_task(f"tinyproxy -d -c {conf}", "tinyproxy"):
        await wait_for_ports(8888)
        yield "http://127.0.0.1:8888"


async def test_proxy_connect(tinyproxy):
    async with sh_task("not-my-board hub", "hub", wait_ready=True):
        client = http.Client(proxies={"http": tinyproxy})
        response = await client.get_json("http://127.0.0.1:2092/api/v1/places")
        assert response == {"places": []}


async def test_proxy_ignore():
    async with sh_task("not-my-board hub", "hub", wait_ready=True):
        client = http.Client(
            proxies={"http": "http://non-existing.localhost", "no": "127.0.0.1"}
        )
        response = await client.get_json("http://127.0.0.1:2092/api/v1/places")
        assert response == {"places": []}


async def test_proxy_connect_https(tinyproxy):
    root_key = project_dir / "tests/.cache/not-my-board-root-ca.key"
    root_cert = project_dir / "tests/.cache/not-my-board-root-ca.crt"
    key_file = project_dir / "tests/.cache/not-my-board.key"
    cert_file = project_dir / "tests/.cache/not-my-board.crt"
    if not key_file.exists():
        key_file.parent.mkdir(exist_ok=True)
        await sh(
            "openssl req "
            "-x509 "
            "-newkey ec "
            "-pkeyopt ec_paramgen_curve:secp384r1 "
            "-days 365000 "
            "-nodes "
            f"-keyout {root_key} "
            f"-out {root_cert} "
            "-subj '/CN=not-my-board-root-ca' "
            "-addext 'basicConstraints=critical,CA:TRUE' "
            "-addext 'keyUsage=critical,keyCertSign'"
        )
        hostname = "hub.local"
        await sh(
            "openssl req "
            "-x509 "
            f"-CA {root_cert} "
            f"-CAkey {root_key} "
            "-newkey ec "
            "-pkeyopt ec_paramgen_curve:prime256v1 "
            "-days 365000 "
            "-nodes "
            f"-keyout {key_file} "
            f"-out {cert_file} "
            f"-subj '/CN={hostname}' "
            f"-addext 'subjectAltName=DNS:{hostname},DNS:*.{hostname},IP:127.0.0.1' "
            "-addext 'basicConstraints=CA:FALSE' "
            "-addext 'keyUsage=digitalSignature,keyEncipherment' "
            "-addext 'extendedKeyUsage=serverAuth'"
        )

    async with sh_task(
        (
            "uvicorn --port 2092 "
            f"--ssl-keyfile {key_file} "
            f"--ssl-certfile {cert_file} "
            "not_my_board:asgi_app"
        ),
        "hub",
    ):
        await wait_for_ports(2092)

        client = http.Client(ca_files=[root_cert], proxies={"https": tinyproxy})
        response = await client.get_json("https://127.0.0.1:2092/api/v1/places")
        assert response == {"places": []}


@pytest.mark.parametrize(
    ("host", "no_proxy_env", "expected"),
    [
        # Test cases taken from curl
        ("www.example.com", "localhost,.example.com,.example.de", True),
        ("www.example.com.", "localhost,.example.com,.example.de", True),
        ("example.com", "localhost,.example.com,.example.de", True),
        ("example.com.", "localhost,.example.com,.example.de", True),
        ("www.example.com", "localhost,.example.com.,.example.de", True),
        ("www.example.com", "localhost,www.example.com.,.example.de", True),
        ("example.com", "localhost,example.com,.example.de", True),
        ("example.com.", "localhost,example.com,.example.de", True),
        ("nexample.com", "localhost,example.com,.example.de", False),
        ("www.example.com", "localhost,example.com,.example.de", True),
        ("127.0.0.1", "127.0.0.1,localhost", True),
        ("127.0.0.1", "127.0.0.1,localhost,", True),
        ("127.0.0.1", "127.0.0.1/8,localhost,", True),
        ("127.0.0.1", "127.0.0.1/28,localhost,", True),
        ("127.0.0.1", "127.0.0.1/31,localhost,", True),
        ("127.0.0.1", "localhost,127.0.0.1", True),
        ("localhost", "localhost,127.0.0.1", True),
        ("localhost", "127.0.0.1,localhost", True),
        ("foobar", "barfoo", False),
        ("foobar", "foobar", True),
        ("192.168.0.1", "foobar", False),
        ("192.168.0.1", "192.168.0.0/16", True),
        ("192.168.0.1", "192.168.0.0/24", True),
        ("192.168.0.1", "192.168.0.0/32", False),
        ("192.168.0.1", "192.168.0.0", False),
        ("192.168.1.1", "192.168.0.0/24", False),
        ("192.168.1.1", "foo, bar, 192.168.0.0/24", False),
        ("192.168.1.1", "foo, bar, 192.168.0.0/16", True),
        ("[::1]", "foo, bar, 192.168.0.0/16", False),
        ("[::1]", "foo, bar, ::1/64", True),
        ("bar", "foo, bar, ::1/64", True),
        ("BAr", "foo, bar, ::1/64", True),
        ("BAr", "foo,,,,,              bar, ::1/64", True),
        ("www.example.com", "foo, .example.com", True),
        ("www.example.com", "www2.example.com, .example.net", False),
        ("example.com", ".example.com, .example.net", True),
        ("nonexample.com", ".example.com, .example.net", False),
        # Test cases taken from CPython without host:port cases
        ("anotherdomain.com", "localhost, anotherdomain.com", True),
        ("localhost", "localhost, anotherdomain.com, .d.o.t", True),
        ("LocalHost", "localhost, anotherdomain.com, .d.o.t", True),
        ("LOCALHOST", "localhost, anotherdomain.com, .d.o.t", True),
        (".localhost", "localhost, anotherdomain.com, .d.o.t", True),
        ("foo.d.o.t", "localhost, anotherdomain.com, .d.o.t", True),
        ("d.o.t", "localhost, anotherdomain.com, .d.o.t", True),
        ("prelocalhost", "localhost, anotherdomain.com, .d.o.t", False),
        ("newdomain.com", "localhost, anotherdomain.com, .d.o.t", False),
        ("newdomain.com", "*", True),
        ("anotherdomain.com", "*, anotherdomain.com", True),
        ("newdomain.com", "*, anotherdomain.com", False),
        ("localhost\n", "localhost, anotherdomain.com", False),
    ],
)
def test_is_proxy_disabled(host, no_proxy_env, expected):
    assert http.is_proxy_disabled(host, no_proxy_env) == expected


class FakeReader:
    def __init__(self):
        self._queue = asyncio.Queue()

    async def read(self, _=-1):
        return await self._queue.get()

    def feed(self, data):
        self._queue.put_nowait(data)


class FakeWriter:
    def __init__(self):
        self._queue = asyncio.Queue()

    def write(self, data):
        self._queue.put_nowait(data)

    async def drain(self):
        pass

    async def get_written(self):
        return await self._queue.get()


@pytest.fixture
def fake_server(monkeypatch):
    fake_server_ = FakeServer()
    monkeypatch.setattr(util, "connect", fake_server_.connect)
    return fake_server_


class FakeServer:
    def __init__(self):
        self.requests = []
        self.headers = []

    @contextlib.asynccontextmanager
    async def connect(self, host, port, ssl=None):
        assert host == "test.localhost"
        assert port == 80
        assert not ssl

        self._reader = FakeReader()
        self._writer = FakeWriter()

        async with util.background_task(self._serve()):
            yield self._reader, self._writer

    async def _serve(self):
        conn = h11.Connection(h11.SERVER)
        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                data = await self._writer.get_written()
                conn.receive_data(data)
            elif isinstance(event, h11.Request):
                self._handle_request(conn, event)
                break
            else:
                raise RuntimeError(f"Unexpected Event: {event}")

    def _handle_request(self, conn, request):
        if request.method == b"GET":
            self.requests.append(request.target)
            if request.target == b"/hello":
                self._send_response(conn, "Hello, World!")
            else:
                raise RuntimeError(f"Unexpected target: {request.target}")

    def _send_response(self, conn, data=None, status=http.STATUS_OK):
        now = datetime.datetime.now(datetime.timezone.utc)
        date_value = email.utils.format_datetime(now, usegmt=True)

        all_headers = [
            ("Date", date_value),
            ("Connection", "close"),
        ]

        if data is not None:
            body = json.dumps(data).encode()
            all_headers += [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(body))),
            ]

        if self.headers:
            all_headers += self.headers

        res = h11.Response(status_code=status, headers=all_headers)
        end = h11.EndOfMessage()

        if data is not None:
            self._send(conn, [res, h11.Data(data=body), end])
        else:
            self._send(conn, [res, end])

    def _send(self, conn, events):
        data = b"".join([conn.send(event) for event in events])
        self._reader.feed(data)


@pytest.fixture
def fake_time(monkeypatch):
    fake_time_ = FakeTime()
    fake_datetime = fake_time_.fake_datetime()
    monkeypatch.setattr(datetime, "datetime", fake_datetime)
    return fake_time_


class FakeTime:
    def __init__(self):
        self._now = 0

    def fake_datetime(self):
        class FakeDateTime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(self.now, tz)

        return FakeDateTime

    @property
    def now(self):
        return self._now

    def add_time(self, time):
        self._now += time


@pytest.fixture
def http_client():
    return http.Client(proxies={})


@pytest.mark.usefixtures("fake_server")
async def test_fake_server_hello(http_client):
    res = await http_client.get_json("http://test.localhost/hello")
    assert res == "Hello, World!"


async def check_expires_after(max_age, http_client, fake_server, fake_time):
    cache = http.CacheEntry()
    await http_client.get_json("http://test.localhost/hello", cache=cache)
    assert len(fake_server.requests) == 1

    fake_time.add_time(max_age)
    res = await http_client.get_json("http://test.localhost/hello", cache=cache)
    assert res == "Hello, World!"
    assert len(fake_server.requests) == 1

    fake_time.add_time(1)
    res = await http_client.get_json("http://test.localhost/hello", cache=cache)
    assert res == "Hello, World!"
    assert len(fake_server.requests) == 2


async def test_default_cache_time(http_client, fake_server, fake_time):
    # http_client caches responses for 5 seconds, if the server doesn't specify
    # anything
    await check_expires_after(5, http_client, fake_server, fake_time)


async def test_max_age(http_client, fake_server, fake_time):
    fake_server.headers = [
        ("Cache-Control", "max-age=60"),
    ]
    await check_expires_after(60, http_client, fake_server, fake_time)


async def test_expires(http_client, fake_server, fake_time):
    now = datetime.datetime.now(datetime.timezone.utc)
    max_age = datetime.timedelta(seconds=60)
    expires = email.utils.format_datetime(now + max_age, usegmt=True)
    fake_server.headers = [
        ("Expires", expires),
    ]
    await check_expires_after(60, http_client, fake_server, fake_time)


async def test_no_cache(http_client, fake_server):
    fake_server.headers = [
        ("Cache-Control", "no-cache"),
    ]
    cache = http.CacheEntry()
    await http_client.get_json("http://test.localhost/hello", cache=cache)
    await http_client.get_json("http://test.localhost/hello", cache=cache)
    assert len(fake_server.requests) == 2


async def test_no_store(http_client, fake_server):
    fake_server.headers = [
        ("Cache-Control", "no-store"),
    ]
    cache = http.CacheEntry()
    await http_client.get_json("http://test.localhost/hello", cache=cache)
    await http_client.get_json("http://test.localhost/hello", cache=cache)
    assert len(fake_server.requests) == 2


async def test_age(http_client, fake_server, fake_time):
    fake_server.headers = [
        ("Cache-Control", "max-age=60"),
        ("Age", "40"),
    ]
    await check_expires_after(20, http_client, fake_server, fake_time)


async def test_invalid_age(http_client, fake_server, fake_time):
    fake_server.headers = [
        ("Cache-Control", "max-age=60"),
        ("Age", "old"),
    ]
    await check_expires_after(60, http_client, fake_server, fake_time)


async def test_cache_control_quote_parsing(http_client, fake_server, fake_time):
    fake_server.headers = [
        ("Cache-Control", 'ignore-this="val1, max-age=60, val2"'),
    ]
    await check_expires_after(5, http_client, fake_server, fake_time)
