# ruff: noqa: T201
import argparse
import asyncio
import json
import logging
import pathlib
import sys

import tabulate

import not_my_board._agent as agent
import not_my_board._auth as auth
import not_my_board._client as client
import not_my_board._export as export
import not_my_board._http as http
import not_my_board._util as util
from not_my_board._hub import run_hub

try:
    from ..__about__ import __version__
except ModuleNotFoundError:
    __version__ = "dev"


TOKEN_STORE_PATH = "/var/lib/not-my-board/auth_tokens.json"  # noqa: S105
logger = logging.getLogger(__name__)


# ruff: noqa: PLR0915
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

    def add_cacert_arg(subparser):
        subparser.add_argument(
            "--cacert",
            type=pathlib.Path,
            action="append",
            help="path to CA certificates, which should be trusted",
        )

    subparser = add_subcommand("hub", help="start the board farm hub")
    subparser.set_defaults(verbose=True)

    subparser = add_subcommand(
        "export", help="make connected boards available in the board farm"
    )
    subparser.set_defaults(verbose=True)
    add_cacert_arg(subparser)
    subparser.add_argument("--token-cmd", help="generate ID tokens with shell command")
    subparser.add_argument("hub_url", help="http(s) URL of the hub")
    subparser.add_argument(
        "export_description", type=pathlib.Path, help="path to a export description"
    )

    subparser = add_subcommand("agent", help="start an agent")
    subparser.set_defaults(verbose=True)
    add_cacert_arg(subparser)
    subparser.add_argument("--token-cmd", help="generate ID tokens with shell command")
    subparser.add_argument(
        "--fd", type=int, help="listen on socket from this file descriptor"
    )
    subparser.add_argument("hub_url", help="http(s) URL of the hub")

    subparser = add_subcommand("reserve", help="reserve a place")
    add_verbose_arg(subparser)
    subparser.add_argument("-n", "--with-name", help="reserve under a different name")
    subparser.add_argument(
        "import_description", help="name or full path of an import description"
    )

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
    subparser.add_argument(
        "name", help="name of a reserved place or an import description"
    )

    subparser = add_subcommand("detach", help="detach a reserved place")
    add_verbose_arg(subparser)
    subparser.add_argument(
        "-k", "--keep", action="store_true", help="don't return reservation"
    )
    subparser.add_argument("name", help="name of the place to detach")

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

    subparser = add_subcommand("login", help="log in to a hub")
    add_verbose_arg(subparser)
    add_cacert_arg(subparser)
    subparser.add_argument("hub_url", help="http(s) URL of the hub")

    subparser = add_subcommand("edit", help="edit a reserved place")
    add_verbose_arg(subparser)
    subparser.add_argument("name", help="name of the place to edit")

    args = parser.parse_args()

    # Don't use escape sequences, if stdout is not a tty
    if not sys.stdout.isatty():
        for attr in dir(Format):
            if not attr.startswith("_"):
                setattr(Format, attr, "")

    level = logging.DEBUG if args.verbose else logging.WARNING
    util.configure_logging(level)

    try:
        obj = args.func(args)
        if asyncio.iscoroutine(obj):
            util.run(obj, debug=True)
    except KeyboardInterrupt:
        pass


def _hub_command(_):
    run_hub()


async def _export_command(args):
    http_client = http.Client(args.cacert)
    token_src = _token_src(args, http_client)

    async with export.Exporter(
        args.hub_url, args.export_description, http_client, token_src
    ) as exporter:
        await exporter.register_place()
        print("ready", flush=True)
        await exporter.serve_forever()


async def _agent_command(args):
    http_client = http.Client(args.cacert)
    io = agent.AgentIO(args.hub_url, http_client, args.fd)
    token_src = _token_src(args, http_client)

    async with agent.Agent(args.hub_url, io, token_src) as agent_:
        if args.fd is None:
            print("ready", flush=True)
        await agent_.serve_forever()


def _token_src(args, http_client):
    if args.token_cmd:
        return auth.IdTokenFromCmd(args.hub_url, http_client, args.token_cmd)
    return auth.IdTokenFromFile(args.hub_url, http_client, TOKEN_STORE_PATH)


async def _reserve_command(args):
    await client.reserve(args.import_description, args.with_name)


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

    if status_list:
        if args.no_header:
            headers = []
        else:
            headers = ["Place", "Part", "Type", "Interface", "Status", "Port"]
            headers[0] = f"{Format.BOLD}{headers[0]}"
            headers[-1] = f"{headers[-1]}{Format.RESET}"

        def row(entry):
            keys = ["place", "part", "type", "interface", "port"]
            row = [entry[k] for k in keys]
            status = f"{Format.GREEN}Up" if entry["attached"] else f"{Format.RED}Down"
            status += Format.RESET
            row.insert(-1, status)
            return row

        table = [row(entry) for entry in status_list]
        print(tabulate.tabulate(table, headers=headers, tablefmt="plain"))


async def _uevent_command(args):
    await client.uevent(args.devpath)


async def _login_command(args):
    http_client = http.Client(args.cacert)
    token_store_path = "/var/lib/not-my-board/auth_tokens.json"  # noqa: S105
    async with auth.LoginFlow(args.hub_url, http_client, token_store_path) as login:
        print(
            f"{Format.BOLD}"
            "Open the following link in your browser and log in:"
            f"{Format.RESET}"
        )
        print(login.login_url)

        if args.cacert:
            print(
                f"{Format.YELLOW}"
                "You might need to accept an unknown certificate in the browser."
                f"{Format.RESET}"
            )

        claims = await login.finish()

        msg = "Login was successful"
        if claims:
            msg += ", your token has the following claims:"

        print(f"{Format.GREEN}{Format.BOLD}{msg}{Format.RESET}")
        for key, value in claims.items():
            print(f"{Format.BOLD}{key}: {Format.RESET}{value}")


async def _edit_command(args):
    await client.edit(args.name)


class Format:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
