#!/usr/bin/python3

import contextlib
import json
import urllib.parse

import h11

import not_my_board._util as util


class ProtocolError(Exception):
    pass


async def get_json(url):
    url = urllib.parse.urlsplit(url)
    headers = [
        ("Host", url.netloc),
        ("User-Agent", h11.PRODUCT_ID),
        ("Accept", "application/json"),
        ("Connection", "close"),
    ]

    conn = h11.Connection(our_role=h11.CLIENT)
    to_send = conn.send(
        h11.Request(method="GET", target=url.path or "/", headers=headers)
    )

    if url.scheme == "https":
        default_port = 443
        ssl = True
    elif url.scheme == "http":
        default_port = 80
        ssl = False
    else:
        raise ValueError("Unknown scheme '{url.scheme}'")

    port = url.port or default_port

    async with util.connect(url.hostname, port, ssl=ssl) as (reader, writer):
        writer.write(to_send)
        writer.write(conn.send(h11.EndOfMessage()))
        await writer.drain()

        async def receive_all():
            while True:
                event = conn.next_event()
                if event is h11.NEED_DATA:
                    conn.receive_data(await reader.read(4096))
                elif isinstance(event, h11.Response):
                    if event.status_code != 200:
                        raise ProtocolError(
                            f"Expected status code 200, got {event.status_code}"
                        )
                elif isinstance(event, h11.Data):
                    yield event.data
                elif isinstance(event, (h11.EndOfMessage, h11.PAUSED)):
                    break

        content = b"".join([data async for data in receive_all()])

    return json.loads(content)


@contextlib.asynccontextmanager
async def open_tunnel(proxy_host, proxy_port, target_host, target_port):
    headers = [
        ("Host", f"{target_host}:{target_port}"),
        ("User-Agent", h11.PRODUCT_ID),
    ]

    conn = h11.Connection(our_role=h11.CLIENT)
    to_send = conn.send(
        h11.Request(
            method="CONNECT", target=f"{target_host}:{target_port}", headers=headers
        )
    )

    async with util.connect(proxy_host, proxy_port) as (reader, writer):
        writer.write(to_send)
        writer.write(conn.send(h11.EndOfMessage()))
        await writer.drain()

        while True:
            event = conn.next_event()
            if event is h11.NEED_DATA:
                conn.receive_data(await reader.read(4096))
            elif isinstance(event, h11.Response):
                response = event
                if response.status_code != 200:
                    raise ProtocolError(
                        f"Expected status code 200, got {event.status_code}"
                    )

                yield reader, writer, conn.trailing_data[0]
                break
            else:
                raise ProtocolError(f"Unexpected event: {event}")
