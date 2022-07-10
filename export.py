#!/usr/bin/env python3

import socket
import struct
import collections
import dataclasses
import operator
import pathlib

PROTOCOL_VERSION = 0x0111
REQ_IMPORT_CODE = 0x8003
REP_IMPORT_CODE = 0x0003
REQ_DEVLIST_CODE = 0x8005
REP_DEVLIST_CODE = 0x0005

def attr_speed(sysfs_path):
    return {
        "1.5\n": 1,
        "12\n": 2,
        "480\n": 3,
        "53.3-480\n": 4,
        "5000\n": 5,
    }.get((sysfs_path / 'speed').read_text(), 0)

def build_rep_devlist(sysfs_path):
    attr_dir = sysfs_path
    def attr_int(name):
        return int((attr_dir / name).read_text())

    def attr_hex(name):
        return int((attr_dir / name).read_text(), base=16)

    num_if = attr_hex('bNumInterfaces')

    rep_vals = [
        ('H', PROTOCOL_VERSION),
        ('H', REP_DEVLIST_CODE),
        ('I', 0), # status
        ('I', 1), # n_devices

        ('256s', sysfs_path.as_posix().encode('utf-8')), # path
        ('32s', sysfs_path.name.encode('utf-8')), # busid

        ('I', attr_int('busnum')),
        ('I', attr_int('devnum')),
        ('I', attr_speed(sysfs_path)),

        ('H', attr_hex('idVendor')),
        ('H', attr_hex('idProduct')),
        ('H', attr_hex('bcdDevice')),

        ('B', attr_hex('bDeviceClass')),
        ('B', attr_hex('bDeviceSubClass')),
        ('B', attr_hex('bDeviceProtocol')),
        ('B', attr_hex('bConfigurationValue')),
        ('B', attr_hex('bNumConfigurations')),
        ('B', num_if),
    ]

    for if_dir in sysfs_path.glob(sysfs_path.name + ':*'):
        attr_dir = if_dir
        rep_vals += [
            ('B', attr_hex('bInterfaceClass')),
            ('B', attr_hex('bInterfaceSubClass')),
            ('B', attr_hex('bInterfaceProtocol')),
            ('B', 0), # padding
        ]

    format_str = "".join(map(operator.itemgetter(0), rep_vals))
    values = map(operator.itemgetter(1), rep_vals)
    return struct.pack(f'!{format_str}', *values)

def build_rep_import(sysfs_path):
    def attr_int(name):
        return int((sysfs_path / name).read_text())

    def attr_hex(name):
        return int((sysfs_path / name).read_text(), base=16)

    rep_vals = [
        ('H', PROTOCOL_VERSION),
        ('H', REP_IMPORT_CODE),
        ('I', 0), # status

        ('256s', sysfs_path.as_posix().encode('utf-8')), # path
        ('32s', sysfs_path.name.encode('utf-8')), # busid

        ('I', attr_int('busnum')),
        ('I', attr_int('devnum')),
        ('I', attr_speed(sysfs_path)),

        ('H', attr_hex('idVendor')),
        ('H', attr_hex('idProduct')),
        ('H', attr_hex('bcdDevice')),

        ('B', attr_hex('bDeviceClass')),
        ('B', attr_hex('bDeviceSubClass')),
        ('B', attr_hex('bDeviceProtocol')),
        ('B', attr_hex('bConfigurationValue')),
        ('B', attr_hex('bNumConfigurations')),
        ('B', attr_hex('bNumInterfaces')),
    ]

    format_str = "".join(map(operator.itemgetter(0), rep_vals))
    values = map(operator.itemgetter(1), rep_vals)
    return struct.pack(f'!{format_str}', *values)

def build_rep_import_error():
    rep_vals = [
        ('H', PROTOCOL_VERSION),
        ('H', REP_IMPORT_CODE),
        ('I', 1), # status
    ]

    format_str = "".join(map(operator.itemgetter(0), rep_vals))
    values = map(operator.itemgetter(1), rep_vals)
    return struct.pack(f'!{format_str}', *values)

class StructStream:
    def __init__(self, conn):
        self._conn = conn
        self._data = b''
        self._offset = 0

    def unpack(self, format):
        s = struct.Struct(format)
        while (len(self._data) - self._offset) < s.size:
            data = self._conn.recv(1024)
            if not data:
                break
            self._data += data

        unpacked = s.unpack_from(self._data, self._offset)
        self._offset += s.size
        return unpacked

def export_device(sysfs_path, conn):
    status = int((sysfs_path / 'usbip_status').read_text())
    STATUS_AVAILABLE = 1
    if status != STATUS_AVAILABLE:
        raise Error("Device unavailable")

    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    sockfd = conn.fileno()
    (sysfs_path / 'usbip_sockfd').write_text(f"{sockfd}\n")


class ProtocolError(Exception):
    pass

class Error(Exception):
    pass

def main():
    busid = "1-5.1.4"
    sysfs_path = pathlib.Path("/sys/bus/usb/devices/") / busid

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('localhost', 3240))
    s.listen()

    conn, addr = s.accept()
    with conn:
        stream = StructStream(conn)
        version, code, status = stream.unpack('!HHI')
        if version != PROTOCOL_VERSION:
            raise ProtocolError(f"Unexpected protocol version: 0x{version:04x}")
        if status != 0:
            raise ProtocolError(f"Unexpected status: {status}")

        if code == REQ_DEVLIST_CODE:
            conn.sendall(build_rep_devlist(sysfs_path))
        elif code == REQ_IMPORT_CODE:
            busid_req = stream.unpack('!32s')[0].rstrip(b'\0').decode('utf-8')
            if busid_req != busid:
                raise ProtocolError(f"Unexpected Bus ID: {busid_req}")
            try:
                export_device(sysfs_path, conn)
                conn.sendall(build_rep_import(sysfs_path))
            except Error as e:
                print(e)
                conn.sendall(build_rep_import_error())
        else:
            raise ProtocolError(f"Unexpected command code: 0x{code:04x}")

if __name__ == '__main__':
    main()
