#!/usr/bin/env python3

import asyncio
import contextlib
import dataclasses
import logging
import os
import pathlib
import socket
import struct
import sys

import not_my_board._util as util

if sys.version_info < (3, 9):
    from typing_extensions import Annotated
else:
    from typing import Annotated


logger = logging.getLogger(__name__)
_vhci_status_attached = {}


class UsbIpServer:
    def __init__(self, devices):
        self._devices = {d.busid: d for d in devices}

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            for _, device in self._devices.items():
                await stack.enter_async_context(
                    util.background_task(_refresh_task(device))
                )

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._stack.__aexit__(exc_type, exc, tb)

    async def handle_client(self, reader, writer):
        sock = writer.transport.get_extra_info("socket")
        _enable_keep_alive(sock)

        request = await ImportRequest.from_reader(reader)
        logger.debug("Received: %s", request)
        if request.busid not in self._devices:
            raise ProtocolError(f"Unexpected Bus ID: {request.busid}")

        device = self._devices[request.busid]

        # allow client to trigger a refresh
        device.refresh()

        async with device:
            writer.transport.pause_reading()
            fd = sock.fileno()
            device.export(fd)

            reply = ImportReply.from_device(device)
            logger.debug("Sending: %s", reply)
            writer.write(bytes(reply))
            await writer.drain()

            writer.close()
            await writer.wait_closed()
            await device.available()


class _SysfsFileInt:
    def __set_name__(self, owner, name):
        self._name = name

    def __init__(self, base=10, default=None):
        self._base = base
        self._default = default

    def __get__(self, instance, owner=None):
        try:
            path = instance._sysfs_path / self._name
            return int(path.read_text(), base=self._base)
        except ValueError:
            if self._default is not None:
                return self._default
            else:
                raise


class _SysfsFileHex(_SysfsFileInt):
    def __init__(self, default=None):
        super().__init__(16, default)


class UsbIpDevice:
    def __init__(self, busid):
        self._busid = busid
        self._sysfs_path = pathlib.Path("/sys/bus/usb/devices/") / busid
        self._lock = asyncio.Lock()
        self._refresh_event = asyncio.Event()
        self._is_exported = False

    def refresh(self):
        self._refresh_event.set()

    async def __aenter__(self):
        async with contextlib.AsyncExitStack() as stack:
            await stack.enter_async_context(self._lock)
            await self.available()

            self._stack = stack.pop_all()
            await self._stack.__aenter__()
            return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._is_exported:
            try:
                (self._sysfs_path / "usbip_sockfd").write_text("-1\n")
            except (OSError, FileNotFoundError):
                # client might have disconnected or device disappeared
                pass
            except Exception as e:
                logger.warning("Error while stopping export: %s", e)

        await self._stack.__aexit__(exc_type, exc, tb)

    async def available(self):
        while not self._is_available():
            self._refresh_event.clear()
            await self._refresh_event.wait()

    def export(self, fd):
        (self._sysfs_path / "usbip_sockfd").write_text(f"{fd}\n")
        self._is_exported = True

    def _is_available(self):
        try:
            status_available = 1
            if self.usbip_status == status_available:
                logger.debug("Device %s is available", self.busid)
                return True
        except FileNotFoundError:
            # device might have disappeared
            pass
        except Exception as e:
            logger.warning("Error while checking device status: %s", e)

        logger.debug("Device %s is not available, yet", self.busid)
        return False

    @property
    def busid(self):
        return self._busid.encode("utf-8")

    @property
    def path(self):
        return self._sysfs_path.as_posix().encode("utf-8")

    @property
    def speed(self):
        string_to_code = {
            "1.5": 1,
            "12": 2,
            "480": 3,
            "53.3-480": 4,
            "5000": 5,
        }
        string = (self._sysfs_path / "speed").read_text()[:-1]  # strip newline
        return string_to_code.get(string, 0)

    usbip_status = _SysfsFileInt()
    busnum = _SysfsFileInt()
    devnum = _SysfsFileInt()
    idVendor = _SysfsFileHex()
    idProduct = _SysfsFileHex()
    bcdDevice = _SysfsFileHex()
    bDeviceClass = _SysfsFileHex()
    bDeviceSubClass = _SysfsFileHex()
    bDeviceProtocol = _SysfsFileHex()
    bConfigurationValue = _SysfsFileHex(default=0)
    bNumConfigurations = _SysfsFileHex()
    bNumInterfaces = _SysfsFileHex(default=0)


async def attach(reader, writer, busid, port):
    sock = writer.transport.get_extra_info("socket")
    # Client waits 2 seconds longer before sending keep alive probes, otherwise
    # both sides start sending at the same time.
    _enable_keep_alive(sock, extra_idle_sec=2)

    request = ImportRequest(busid.encode())
    logger.debug("Sending: %s", request)
    writer.write(bytes(request))
    await writer.drain()

    reply = await ImportReply.from_reader(reader)
    logger.debug("Received: %s", reply)

    fd = os.dup(sock.fileno())

    try:
        writer.close()
        await writer.wait_closed()

        attach_path = pathlib.Path("/sys/devices/platform/vhci_hcd.0/attach")
        devid = (reply.busnum << 16) | reply.devnum
        attach_path.write_text(f"{port} {fd} {devid} {reply.speed}\n")
    finally:
        os.close(fd)


def detach(port):
    detach_path = pathlib.Path("/sys/devices/platform/vhci_hcd.0/detach")
    try:
        detach_path.write_text(f"{port}")
    except OSError:
        # not attached anymore
        pass


def refresh_vhci_status():
    status_path = pathlib.Path("/sys/devices/platform/vhci_hcd.0/status")
    status_attached = 6  # VDEV_ST_USED
    with status_path.open() as f:
        # skip header:
        # hub port sta spd dev      sockfd local_busid
        f.readline()

        for line in f:
            entries = line.split()
            port = int(entries[1])
            status = int(entries[2])
            _vhci_status_attached[port] = status == status_attached
    # TODO parse other status files if Kernel is compiled with more vhci
    # ports


def is_attached(port):
    return _vhci_status_attached[port]


def _enable_keep_alive(sock, extra_idle_sec=0):
    # enable TCP keep alive
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    # Drop connections faster than the 2 hour default: Send first probe after 5
    # (+extra_idle_sec) seconds, then every 5 seconds. After 3 unanswered
    # probes the connection is closed.
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5 + extra_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)


class StructType:
    def __init__(self, format_str):
        self.format_str = format_str


class StructStr(StructType):
    pass


UInt8 = Annotated[int, StructType("B")]
UInt16 = Annotated[int, StructType("H")]
UInt32 = Annotated[int, StructType("I")]


class Char(bytes):
    def __class_getitem__(cls, key):
        return Annotated[bytes, StructStr(f"{key}s")]


# pylint: disable=W0212,E1101
# - accessing protected member of cls, e.g. cls._struct
# - 'serializable' has no '_struct' member
def serializable(cls):
    cls = dataclasses.dataclass(cls)

    format_str = "!"
    to_strip = set()

    for field in dataclasses.fields(cls):
        for metadata in field.type.__metadata__:
            if isinstance(metadata, StructType):
                format_str += metadata.format_str
                if isinstance(metadata, StructStr):
                    to_strip.add(field.name)
                break
        else:
            raise ProtocolError(f"Field {field!r} not annotated with StructType")

    cls._struct = struct.Struct(format_str)
    cls._to_strip = to_strip

    def __bytes__(self):
        return self._struct.pack(
            *(getattr(self, f.name) for f in dataclasses.fields(self))
        )

    @classmethod
    async def from_reader(cls, reader):
        data = await reader.readexactly(cls._struct.size)
        values = cls._struct.unpack(data)
        init_values = []
        for field, value in zip(dataclasses.fields(cls), values):
            if field.init:
                if field.name in cls._to_strip:
                    value = value.rstrip(b"\0")
                init_values.append(value)
            else:
                if value != field.default:
                    raise ProtocolError(
                        f"Expected {field.name}={field.default}, got={value}"
                    )

        # pylint: disable=E1120
        # No value for argument 'cls': false positive
        return cls(*init_values)

    cls.__bytes__ = __bytes__
    cls.from_reader = from_reader

    return cls


def no_init(default):
    return dataclasses.field(default=default, init=False)


@serializable
class Header:
    version: UInt16 = no_init(0x0111)
    code: UInt16
    status: UInt32 = no_init(0)


@serializable
class ImportRequest(Header):
    code: UInt16 = no_init(0x8003)
    busid: Char[32]


@serializable
class ImportReply(Header):
    code: UInt16 = no_init(0x0003)
    path: Char[256]
    busid: Char[32]
    busnum: UInt32
    devnum: UInt32
    speed: UInt32
    idVendor: UInt16
    idProduct: UInt16
    bcdDevice: UInt16
    bDeviceClass: UInt8
    bDeviceSubClass: UInt8
    bDeviceProtocol: UInt8
    bConfigurationValue: UInt8
    bNumConfigurations: UInt8
    bNumInterfaces: UInt8

    @classmethod
    def from_device(cls, device):
        return cls(
            *(
                getattr(device, field.name)
                for field in dataclasses.fields(cls)
                if field.init
            )
        )


class ProtocolError(Exception):
    pass


async def _refresh_task(device):
    pipe_path = pathlib.Path("/run/usbip-refresh-" + device.busid.decode())

    tmp_path = pipe_path.with_name(pipe_path.name + ".new")
    os.mkfifo(tmp_path)
    tmp_path.replace(pipe_path)

    async with _open_read_pipe(pipe_path, "r+b", buffering=0) as pipe:
        while True:
            await pipe.read(4096)
            device.refresh()


@contextlib.asynccontextmanager
async def _open_read_pipe(*args, **kwargs):
    with open(*args, **kwargs) as pipe:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        await loop.connect_read_pipe(lambda: protocol, pipe)
        yield reader


# pylint: disable=import-outside-toplevel
async def _main():
    import argparse

    logging.basicConfig(
        format="%(asctime)s %(levelname)s: %(name)s: %(message)s", level=logging.DEBUG
    )

    parser = argparse.ArgumentParser(description="Import and export USB ports")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    subparser = subparsers.add_parser("export", help="export a USB device")
    subparser.add_argument(
        "busid", help='busid of the device to export, e.g. "1-5.1.4"'
    )
    subparser.add_argument("-p", "--port", default=3240, help="port to listen on")

    subparser = subparsers.add_parser("import", help="import a USB device")
    subparser.add_argument("host", help="host to connect to")
    subparser.add_argument(
        "busid", help='busid of the device to import, e.g. "1-5.1.4"'
    )
    subparser.add_argument("vhci_port", help='vhci port to attach device to, e.g. "0"')
    subparser.add_argument("-p", "--port", default=3240, help="port to connect to")

    args = parser.parse_args()

    if args.command == "import":
        while True:
            logger.info("Connecting")
            reader, writer = await asyncio.open_connection(
                args.host, args.port, family=socket.AF_INET
            )
            await attach(reader, writer, args.busid, args.vhci_port)
    else:
        device = UsbIpDevice(args.busid)
        async with UsbIpServer([device]) as usbip_server:
            server = util.Server(
                usbip_server.handle_client, port=args.port, family=socket.AF_INET
            )
            async with server:
                logger.info("listening")
                await server.serve_forever()


if __name__ == "__main__":
    util.run(_main())
