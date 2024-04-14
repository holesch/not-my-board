import pathlib

import pytest

import not_my_board._http as http

from .util import sh, sh_task, wait_for_ports

project_dir = pathlib.Path(__file__).parents[1]


@pytest.fixture(scope="session")
async def tinyproxy():
    conf = project_dir / "tests/tinyproxy.conf"
    async with sh_task(f"tinyproxy -d -c {conf}", "tinyproxy"):
        await wait_for_ports(8888)
        yield "http://127.0.0.1:8888"


async def test_proxy_connect(tinyproxy):
    async with sh_task("not-my-board hub", "hub"):
        await wait_for_ports(2092)

        client = http.Client(proxies={"http": tinyproxy})
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
            "-subj '/CN=not-my-board-root-ca'"
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
    "host,no_proxy_env,expected",
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
