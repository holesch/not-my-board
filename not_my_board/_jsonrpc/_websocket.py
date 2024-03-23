import urllib.parse

import websockets

import not_my_board._util as util

from ._protocol import Channel


class WebsocketChannel(Channel, util.ContextStack):
    def __init__(self, url, start=True, auth=None, api_obj=None):
        url = urllib.parse.urlsplit(url)

        if url.scheme == "http":
            ws_scheme = "ws"
        elif url.scheme == "https":
            ws_scheme = "wss"
        else:
            ws_scheme = url.scheme

        self._uri = f"{ws_scheme}://{url.netloc}{url.path}"
        self._headers = {"Authorization": auth} if auth else {}
        self._ws = None
        self._start = start

        super().__init__(self._ws_send, self._ws_receive_iter(), api_obj)

    async def _context_stack(self, stack):
        ws = websockets.connect(self._uri, extra_headers=self._headers)
        self._ws = await stack.enter_async_context(ws)

        if self._start:
            await super()._context_stack(stack)

    async def _ws_receive_iter(self):
        try:
            while True:
                yield await self._ws.recv()
        except websockets.ConnectionClosedOK:
            pass

    async def _ws_send(self, data):
        await self._ws.send(data)
