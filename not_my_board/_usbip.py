#!/usr/bin/env python3

import asyncio
import contextlib
import logging
import os
import pathlib
import socket
import struct
import traceback

logger = logging.getLogger(__name__)


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

    async def handle_client(self, reader, writer):
        try:
            await _UsbIpConnection(self, reader, writer).handle_client()
        except Exception:
            traceback.print_exc()
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

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
                return True
        except FileNotFoundError:
            # device might have disappeared
            pass
        except Exception as e:
            logger.warning("Error while checking device status: %s", e)

        return False

    @property
    def busid(self):
        return self._sysfs_path.name.encode("utf-8")

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

    def interfaces(self):
        for if_dir in self._sysfs_path.glob(self._sysfs_path.name + ":*"):
            yield _UsbIpDeviceInterface(if_dir)


class _UsbIpDeviceInterface:
    def __init__(self, sysfs_path):
        self._sysfs_path = sysfs_path

    bInterfaceClass = _SysfsFileHex()
    bInterfaceSubClass = _SysfsFileHex()
    bInterfaceProtocol = _SysfsFileHex()


class _UsbIpConnection:
    def __init__(self, device, reader, writer):
        self._device = device
        self._reader = reader
        self._writer = writer
        self._sock = writer.transport.get_extra_info("socket")
        _enable_keep_alive(self._sock)

        # allow client to trigger a refresh
        self._device.refresh()

    async def handle_client(self):
        while True:
            request = await receive_message(self._reader)
            logger.debug("Received: %s", request)

            if isinstance(request, DevlistRequest):
                await self._handle_devlist_request(request)
            elif isinstance(request, ImportRequest):
                await self._handle_import_request(request)
                break
            else:
                raise ProtocolError(f"Unexpected message: {request}")

    async def _handle_devlist_request(self, _):
        reply = await DevlistReply.from_device(self._device)
        logger.debug("Sending: %s", reply)
        await self._send(reply)

    async def _handle_import_request(self, request):
        if request.busid != self._device.busid:
            raise ProtocolError(f"Unexpected Bus ID: {request.busid}")

        async with self._device:
            self._writer.transport.pause_reading()
            fd = self._sock.fileno()
            self._device.export(fd)

            reply = await ImportReply.from_device(self._device)
            logger.debug("Sending: %s", reply)
            await self._send(reply)

            self._writer.close()
            await self._writer.wait_closed()
            await self._device.available()

    async def _send(self, data):
        self._writer.write(bytes(data))
        await self._writer.drain()


async def attach(reader, writer, busid, port):
    sock = writer.transport.get_extra_info("socket")
    # Client waits 2 seconds longer before sending keep alive probes, otherwise
    # both sides start sending at the same time.
    _enable_keep_alive(sock, extra_idle_sec=2)

    request = ImportRequest(busid=busid.encode())
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


def _enable_keep_alive(sock, extra_idle_sec=0):
    # enable TCP keep alive
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    # Drop connections faster than the 2 hour default: Send first probe after 5
    # (+extra_idle_sec) seconds, then every 5 seconds. After 3 unanswered
    # probes the connection is closed.
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5 + extra_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)


class _NamedStruct(struct.Struct):
    def __init__(self, *type_name_pairs):
        format_str = "!"
        self._names = []

        for type_, name in type_name_pairs:
            format_str += type_
            self._names.append(name)

        super().__init__(format_str)

    def unpack(self, buffer):
        values = super().unpack(buffer)
        return dict(zip(self._names, values))

    def iter_unpack(self, buffer):
        for values in super().iter_unpack(buffer):
            yield dict(zip(self._names, values))

    def pack(self, **kwargs):
        values = [kwargs[name] for name in self._names]
        return super().pack(*values)

    @property
    def field_names(self):
        return self._names


# pylint: disable=protected-access
async def receive_message(reader):
    code_class_map = {cls.code: cls for cls in _Message.__subclasses__()}
    code = await _Message._receive_header(reader)
    cls = code_class_map.get(code)
    if cls is None:
        raise ProtocolError(f"Unexpected code: {code}")

    return await cls._from_body(reader)


class _Message:
    _protocol_version = 0x0111
    _header_format = _NamedStruct(
        ("H", "version"),
        ("H", "code"),
        ("I", "status"),
    )
    _strip_fields = [
        "path",
        "busid",
    ]

    def __init__(self, **kwargs):
        self._values = kwargs

    @classmethod
    async def from_reader(cls, reader):
        code = await cls._receive_header(reader)

        if code != cls.code:
            raise ProtocolError(f"Unexpected code: {code}")

        return await cls._from_body(reader)

    @classmethod
    async def _receive_header(cls, reader):
        data = await reader.readexactly(cls._header_format.size)
        header = cls._header_format.unpack(data)
        if header["version"] != cls._protocol_version:
            raise ProtocolError(
                f"Unexpected protocol version: 0x{header['version']:04x}"
            )
        if header["status"] != 0:
            raise ProtocolError(f"Unexpected status: {header['status']}")

        return header["code"]

    @classmethod
    async def _receive_body(cls, reader):
        data = await reader.readexactly(cls._message_format.size)
        values = cls._message_format.unpack(data)
        for name in cls._strip_fields:
            if name in values:
                values[name] = values[name].rstrip(b"\0")

        return values

    @classmethod
    async def _from_body(cls, reader):
        values = await cls._receive_body(reader)
        return cls(**values)

    def __bytes__(self):
        header = self._header_format.pack(
            version=self._protocol_version, code=self.code, status=0
        )

        return header + self._message_format.pack(**self._values)

    def __getattr__(self, attr):
        if attr in self._values:
            return self._values[attr]
        cls_name = type(self).__name__
        raise AttributeError(f"'{cls_name}' object has no attribute '{attr}'")

    def __repr__(self):
        args_str = ", ".join([f"{key}={value}" for key, value in self._values.items()])
        cls_name = type(self).__name__
        return f"<{cls_name}({args_str})>"


class DevlistRequest(_Message):
    code = 0x8005
    _message_format = _NamedStruct()


class DevlistReply(_Message):
    code = 0x0005
    _message_format = _NamedStruct(
        ("I", "n_devices"),
        ("256s", "path"),
        ("32s", "busid"),
        ("I", "busnum"),
        ("I", "devnum"),
        ("I", "speed"),
        ("H", "idVendor"),
        ("H", "idProduct"),
        ("H", "bcdDevice"),
        ("B", "bDeviceClass"),
        ("B", "bDeviceSubClass"),
        ("B", "bDeviceProtocol"),
        ("B", "bConfigurationValue"),
        ("B", "bNumConfigurations"),
        ("B", "bNumInterfaces"),
    )
    _interface_format = _NamedStruct(
        ("B", "bInterfaceClass"),
        ("B", "bInterfaceSubClass"),
        ("Bx", "bInterfaceProtocol"),
    )

    @classmethod
    async def _receive_body(cls, reader):
        values = await super()._receive_body(reader)

        interfaces_size = cls._interface_format.size * values["bNumInterfaces"]
        data = await reader.readexactly(interfaces_size)
        values["interfaces"] = list(cls._interface_format.iter_unpack(data))

        return values

    @classmethod
    async def from_device(cls, device):
        values = {
            name: getattr(device, name) for name in cls._message_format.field_names[1:]
        }
        values["n_devices"] = 1

        values["interfaces"] = [
            {
                name: getattr(interface, name)
                for name in cls._interface_format.field_names
            }
            for interface in device.interfaces()
        ]

        return cls(**values)

    def __bytes__(self):
        interfaces = b"".join(
            [
                self._interface_format.pack(**values)
                for values in self._values["interfaces"]
            ]
        )

        return super().__bytes__() + interfaces


class ImportRequest(_Message):
    code = 0x8003
    _message_format = _NamedStruct(
        ("32s", "busid"),
    )


class ImportReply(_Message):
    code = 0x0003
    _message_format = _NamedStruct(
        ("256s", "path"),
        ("32s", "busid"),
        ("I", "busnum"),
        ("I", "devnum"),
        ("I", "speed"),
        ("H", "idVendor"),
        ("H", "idProduct"),
        ("H", "bcdDevice"),
        ("B", "bDeviceClass"),
        ("B", "bDeviceSubClass"),
        ("B", "bDeviceProtocol"),
        ("B", "bConfigurationValue"),
        ("B", "bNumConfigurations"),
        ("B", "bNumInterfaces"),
    )

    @classmethod
    async def from_device(cls, device):
        values = {
            name: getattr(device, name) for name in cls._message_format.field_names
        }

        return cls(**values)


class ProtocolError(Exception):
    pass


@contextlib.asynccontextmanager
async def _open_read_pipe(*args, **kwargs):
    with open(*args, **kwargs) as pipe:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        await loop.connect_read_pipe(lambda: protocol, pipe)
        yield reader


async def _watch_refresh_pipe(pipe, device):
    while True:
        await pipe.read(4096)
        device.refresh()


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
        server = await asyncio.start_server(
            device.handle_client, port=args.port, family=socket.AF_INET
        )
        async with server:
            pipe_path = pathlib.Path("/run/usbip-refresh-" + args.busid)

            tmp_path = pipe_path.with_name(pipe_path.name + ".new")
            os.mkfifo(tmp_path)
            tmp_path.replace(pipe_path)

            async with _open_read_pipe(pipe_path, "r+b", buffering=0) as pipe:
                tasks = [
                    asyncio.create_task(coro)
                    for coro in [
                        server.serve_forever(),
                        _watch_refresh_pipe(pipe, device),
                    ]
                ]

                try:
                    logger.info("listening")
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
