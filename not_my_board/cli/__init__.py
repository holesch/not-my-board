import argparse
import asyncio
import json
import logging
import pathlib
import sys

import not_my_board._client as client
import not_my_board._util as util
from not_my_board._agent import agent
from not_my_board._export import export
from not_my_board._serve import serve

try:
    from ..__about__ import __version__
except ModuleNotFoundError:
    __version__ = "dev"


# pylint: disable=too-many-statements
def main():
    parser = argparse.ArgumentParser(description="Setup, manage and use a board farm")
    parser.add_argument(
        "-V", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="command",
        help="for more info use: %(prog)s <command> -h",
    )

    def add_subcommand(name, *args, **kwargs):
        subparser = subparsers.add_parser(name, *args, **kwargs)
        subparser.set_defaults(func=globals()[f"_{name}_command"])
        return subparser

    def add_verbose_arg(subparser):
        subparser.add_argument(
            "-v", "--verbose", action="store_true", help="Enable debug logs"
        )

    subparser = add_subcommand("serve", help="start the board farm server")
    subparser.set_defaults(verbose=True)

    subparser = add_subcommand(
        "export", help="make connected boards available in the board farm"
    )
    subparser.set_defaults(verbose=True)
    subparser.add_argument("server_url", help="http(s) URL of the server")
    subparser.add_argument(
        "export_description", type=pathlib.Path, help="path to a export description"
    )

    subparser = add_subcommand("agent", help="start an agent")
    subparser.set_defaults(verbose=True)
    subparser.add_argument("server_url", help="http(s) URL of the server")

    subparser = add_subcommand("reserve", help="reserve a place")
    add_verbose_arg(subparser)
    subparser.add_argument("-n", "--with-name", help="reserve under a different name")
    subparser.add_argument("name", help="name or full path of a place specification")

    subparser = add_subcommand("return", help="return a place")
    add_verbose_arg(subparser)
    subparser.add_argument("name", help="name of the place to return")

    subparser = add_subcommand("attach", help="attach a reserved place")
    add_verbose_arg(subparser)
    subparser.add_argument(
        "-k",
        "--keep-others",
        action="store_true",
        help="don't return all other reservations",
    )
    subparser.add_argument("name", help="name of a reserved place")

    subparser = add_subcommand("detach", help="detach a reserved place")
    add_verbose_arg(subparser)
    subparser.add_argument(
        "-k", "--keep", action="store_true", help="don't return reservation"
    )
    subparser.add_argument("name", help="name of the place to attach")

    subparser = add_subcommand("list", help="list reserved places")
    add_verbose_arg(subparser)
    subparser.add_argument(
        "-n", "--no-header", action="store_true", help="Hide table header"
    )

    subparser = add_subcommand(
        "status", help="show status of attached places and its interfaces"
    )
    add_verbose_arg(subparser)
    subparser.add_argument(
        "-n", "--no-header", action="store_true", help="Hide table header"
    )

    subparser = add_subcommand("uevent", help="handle Kernel uevent for USB devices")
    add_verbose_arg(subparser)
    subparser.add_argument("devpath", help="devpath attribute of uevent")

    args = parser.parse_args()

    # Don't use escape sequences, if stdout is not a tty
    if not sys.stdout.isatty():
        for attr in dir(Format):
            if not attr.startswith("_"):
                setattr(Format, attr, "")

    if args.verbose:
        level = logging.DEBUG

        # reduce level of verbose loggers
        logging.getLogger("websockets.client").setLevel(logging.INFO)
        logging.getLogger("asyncio").setLevel(logging.INFO)
    else:
        level = logging.WARNING

    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(name)s: %(message)s", level=level
    )

    try:
        obj = args.func(args)
        if asyncio.iscoroutine(obj):
            util.run(obj, debug=True)
    except KeyboardInterrupt:
        pass


def _serve_command(_):
    serve()


async def _export_command(args):
    await export(args.server_url, args.export_description)


async def _agent_command(args):
    await agent(args.server_url)


async def _reserve_command(args):
    await client.reserve(args.name, args.with_name)


async def _return_command(args):
    await client.return_reservation(args.name)


async def _attach_command(args):
    await client.attach(args.name, args.keep_others)


async def _detach_command(args):
    await client.detach(args.name, args.keep)


async def _list_command(args):
    place_list = await client.list_()

    if not args.no_header and place_list:
        print(f"{Format.BOLD}{'Place':<16} Status{Format.RESET}")

    for entry in place_list:
        status = (
            f"{Format.GREEN}Attached"
            if entry["attached"]
            else f"{Format.YELLOW}Reserved"
        )
        print(f"{entry['place']:<16} {status}{Format.RESET}")


async def _status_command(args):
    status_list = await client.status()

    if not args.no_header and status_list:
        columns = ["Place", "Part", "Type", "Interface", "Status"]
        header = " ".join(f"{c:<16}" for c in columns).rstrip()
        print(f"{Format.BOLD}{header}{Format.RESET}")

    for entry in status_list:
        keys = ["place", "part", "type", "interface"]
        status = f"{Format.GREEN}Up" if entry["attached"] else f"{Format.RED}Down"
        row = " ".join(f"{entry[k]:<16}" for k in keys)
        row += f" {status}{Format.RESET}"
        print(row)


async def _uevent_command(args):
    await client.uevent(args.devpath)


class Format:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
