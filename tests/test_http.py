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
