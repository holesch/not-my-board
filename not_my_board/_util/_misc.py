import functools
import traceback
import urllib.parse

import websockets


def ws_connect(url, auth=None):
    url = urllib.parse.urlsplit(url)

    if url.scheme == "http":
        ws_scheme = "ws"
    elif url.scheme == "https":
        ws_scheme = "wss"
    else:
        ws_scheme = url.scheme

    uri = f"{ws_scheme}://{url.netloc}{url.path}"
    headers = {"Authorization": auth} if auth else {}

    return websockets.connect(uri, extra_headers=headers)


def log_exception(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            await func(*args, **kwargs)
        except Exception:
            traceback.print_exc()

    return wrapper
