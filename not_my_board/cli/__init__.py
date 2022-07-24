import argparse
import aiohttp.web

from ..__about__ import __version__


ROUTES = aiohttp.web.RouteTableDef()


def main():
    parser = argparse.ArgumentParser(description='Setup, manage and use a board farm')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    subparsers = parser.add_subparsers(
            dest="command", required=True, metavar="command",
            help="for more info use: %(prog)s <command> -h")

    subparser = subparsers.add_parser("export", help="make connected boards available in the board farm")
    subparser.set_defaults(func=export_command)

    args = parser.parse_args()

    try:
        args.func(args)
    except KeyboardInterrupt:
        pass


def export_command(args):
    app = aiohttp.web.Application()
    app.add_routes(ROUTES)
    aiohttp.web.run_app(app, host='localhost', port=2092)


@ROUTES.get('/api/v1/places')
async def handle_places(request):
    text = "Hello, World!"
    return aiohttp.web.Response(text=text)

# /api/v1/places
# /api/v1/places/{id}
# /api/v1/places/{id}/shared/wifi
# /api/v1/places/{id}/boards/{board}
# /api/v1/places/{id}/boards/{board}/usb0
# /api/v1/places/{id}/boards/{board}/usb-serial
# /api/v1/places/{id}/boards/{board}/control
