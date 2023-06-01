#!/usr/bin/python3

import asyncio
import json
import urllib.parse

import h11


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
        reader, writer = await asyncio.open_connection(
            url.hostname, url.port or 443, ssl=True
        )
    elif url.scheme == "http":
        reader, writer = await asyncio.open_connection(url.hostname, url.port or 80)
    else:
        raise ValueError("Unknown scheme '{url.scheme}'")

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
    writer.close()
    await writer.wait_closed()

    return json.loads(content)
