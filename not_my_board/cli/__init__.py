import argparse
import asyncio
import logging
from not_my_board._serve import serve
from not_my_board._export import export
from not_my_board._agent import agent
import not_my_board._client as client

from ..__about__ import __version__


def main():
    logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.DEBUG)

    parser = argparse.ArgumentParser(description='Setup, manage and use a board farm')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    subparsers = parser.add_subparsers(
            dest="command", required=True, metavar="command",
            help="for more info use: %(prog)s <command> -h")

    subparser = subparsers.add_parser("export", help="make connected boards available in the board farm")
    subparser.set_defaults(func=export_command)

    subparser = subparsers.add_parser("serve", help="start the board farm server")
    subparser.set_defaults(func=serve_command)

    subparser = subparsers.add_parser("agent", help="start an agent")
    subparser.set_defaults(func=agent_command)

    subparser = subparsers.add_parser("reserve", help="reserve a place")
    subparser.set_defaults(func=reserve_command)

    args = parser.parse_args()

    try:
        args.func(args)
    except KeyboardInterrupt:
        pass


def serve_command(args):
    serve()

def export_command(args):
    asyncio.run(export())

def agent_command(args):
    asyncio.run(agent())

def reserve_command(args):
    asyncio.run(client.reserve())
