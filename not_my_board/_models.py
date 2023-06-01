import ipaddress
from typing import Dict, List

import pydantic

UsbId = pydantic.constr(regex=r"^[1-9][0-9]*-[1-9][0-9]*(\.[1-9][0-9]*)*$")


class UsbSpec(pydantic.BaseModel):
    vhci_port: pydantic.NonNegativeInt


class TcpSpec(pydantic.BaseModel):
    local_port: pydantic.PositiveInt


class SpecPart(pydantic.BaseModel):
    compatible: List[str]
    usb: Dict[str, UsbSpec] = {}
    tcp: Dict[str, TcpSpec] = {}


class Spec(pydantic.BaseModel):
    name: str
    parts: Dict[str, SpecPart]


class UsbDesc(pydantic.BaseModel):
    usbid: UsbId


class TcpDesc(pydantic.BaseModel):
    host: str
    port: pydantic.PositiveInt


class ExportedPart(pydantic.BaseModel):
    compatible: List[str]
    usb: Dict[str, UsbDesc] = {}
    tcp: Dict[str, TcpDesc] = {}


class ExportDesc(pydantic.BaseModel):
    port: pydantic.PositiveInt
    parts: List[ExportedPart]


class Place(ExportDesc):
    id: pydantic.PositiveInt
    host: ipaddress.IPv4Address
