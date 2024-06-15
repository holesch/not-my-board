import not_my_board._util as util

from ._protocol import Channel


class WebsocketChannel(Channel, util.ContextStack):
    def __init__(self, url, http_client, start=True, api_obj=None):
        self._url = url
        self._http = http_client
        self._ws = None
        self._start = start

        super().__init__(self._ws_send, self._ws_receive_iter(), api_obj)

    async def _context_stack(self, stack):
        ws = self._http.websocket(self._url)
        self._ws = await stack.enter_async_context(ws)

        if self._start:
            await super()._context_stack(stack)

    async def _ws_receive_iter(self):
        async for message in self._ws.receive_iter():
            yield message

    async def _ws_send(self, data):
        await self._ws.send(data)
