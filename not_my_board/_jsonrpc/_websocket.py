import not_my_board._http as http
import not_my_board._util as util

from ._protocol import Channel


class WebsocketChannel(Channel, util.ContextStack):
    def __init__(self, url, start=True, auth=None, api_obj=None):
        self._url = url
        self._ws = None
        self._start = start
        self._auth = auth

        super().__init__(self._ws_send, self._ws_receive_iter(), api_obj)

    async def _context_stack(self, stack):
        client = http.Client()
        ws = client.websocket(self._url, self._auth)
        self._ws = await stack.enter_async_context(ws)

        if self._start:
            await super()._context_stack(stack)

    async def _ws_receive_iter(self):
        async for message in self._ws.receive_iter():
            yield message

    async def _ws_send(self, data):
        await self._ws.send(data)
