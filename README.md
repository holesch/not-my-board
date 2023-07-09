# not-my-board

not-my-board is a tool to manage a pool of embedded hardware setups and to
schedule and provide access to those setups over a local network. The concept is
known as a *board farm*.

**Note:** This project is in a very early stage of development. It's basically
just an idea and a few code snippets to test the idea. Don't expect a working
tool, yet.

## Scope

not-my-board aims to give users raw access to the hardware setup without any
abstractions. Users should be able to use the same tools they'd use, if they
plug in the hardware directly. Maintaining abstractions separate from the board
farm has the benefit, that they can be used on locally attached hardware as
well.

## Overview

There are a few parts involved in setting up this board farm:

- **Place**: The physical embedded hardware setup. This can consist of multiple
  parts, like one or more boards and equipment, like power supply, a wireless
  access point, etc.
- **Server**: A single instance, that schedules access to *Places*.
- **Exporter**: This runs on the host, where the *Place* is connected. It
  registers the *Place* with the *Server*.
- **Agent**: A board farm user runs this on their host. It requests a *Place*
  from the *Server*, connects directly to the *Exporter* and tunnels resources
  from the *Exporter* to the host of the user.
- **Client**: The CLI, that controls the *Agent*.

### Server

The *Server* provides its interface over HTTPS and WebSocket. *Exporter* and
*Agent* stay registered as long as the WebSocket connection is alive. If the
*Exporter* connection breaks, then the *Place* will no longer be scheduled. If
the *Agent* connection breaks, then the user loses access and the *Place* can be
reserved by another user.

The *Server* only provides a list of known *Places* with their description. The
*Agent* then filters the list and asks the *Server* to reserve one of all the
possible candidate *Places*. As soon as one of the candidates is free, the
*Server* let's both *Exporter* and *Agent* know about the new reservation.

### Exporter

The *Exporter* opens a WebSocket connection to the *Server* and exports the
resources as an HTTP proxy. By not exporting the ports directly, only the HTTP
proxy port needs to be opened in the firewall and the *Exporter* can
authenticate the user before granting access.

Resources can be exported as a TCP port (like the SCPI interface of a power
supply), or over USB/IP, like the USB port of the board or the USB to serial
converter.

The proxy uses IP-based authentication to avoid the TLS overhead. Once the
*Place* of the *Exporter* is reserved, the *Server* tells the *Exporter* which
IP address to allow access.

The exporter assigns specific tags to the exported parts of the place. Those
tags describe to what the parts are compatible with. An *Agent* can then filter
based on those tags.

### Agent

The *Agent* is a long running process on the host of the user, to keep the
connection to the *Server* open and to tunnel the resources from the *Exporter*.
It listens on a Unix domain socket for commands from the *Client*.

The *Client* provides a specification of the *Place* it wants, i.e. which parts
it needs (identified by the compatible tags) and where to attach those parts.
For example: I need a "Raspberry Pi" and want its USB serial adapter attached to
USB port 3-4 and its USB port to 3-5. The *Agent* then filters all the exported
*Places* based on that description and gives the *Server* a list of the matching
candidates. As soon as the *Server* reserves one of the candidates, the *Agent*
connects directly with the *Exporter* and attaches the resources as requested.

## USB/IP

not-my-board uses the USB/IP protocol to tunnel USB devices over the network.
Since the USB device is controlled by the software on the exported board, we
want to export any device, that appears on a specific USB port. With the usbipd
tool, which is part of the Linux Kernel, this would only be possible by polling.
The USB/IP implementation in not-my-board behaves a bit different: It doesn't
return an error if nothing is attached on the requested port, but it blocks
until there is a device. That way the client can just request a device and it
will get it as soon as it's available.

For that to work, udevd needs to be configured to bind the device to the
usbip-host driver when the device appears. Additionally some USB devices can't
handle getting probed twice, so the exporter needs to disable
`drivers_autoprobe` and bind usbip-host directly instead of unbinding the
default driver first.

### udev Rules

`/etc/udev/rules.d/30-usbip.rules`:

```
# disable autoprobe
ACTION=="add|change", KERNEL=="usb", SUBSYSTEM=="subsystem", \
       ATTR{drivers_autoprobe}="0"

ACTION!="add", GOTO="usbip_end"
SUBSYSTEM!="usb", GOTO="usbip_end"

# devices to export
ATTR{busnum}=="1", ATTR{devpath}=="4.1", GOTO="usbip_apply"
ATTR{busnum}=="1", ATTR{devpath}=="4.2", GOTO="usbip_apply"

# default: probe drivers
RUN+="/bin/sh -c \"printf '$kernel' > '$sys/bus/usb/drivers_probe'\""
GOTO="usbip_end"

LABEL="usbip_apply"
RUN{builtin}+="kmod load usbip-host"
RUN+="/usr/bin/systemd-cat -t usbip-bind /usr/local/libexec/usbip-bind '$kernel' '$sys'"

LABEL="usbip_end"
```

`/usr/local/libexec/usbip-bind`:

```
#!/bin/sh -e

busid=${1:?} # udev: $kernel
sys=${2:?} # sysfs mountpoint, usually "/sys"

echo "Binding to usbip-host: \"$busid\"" >&2
printf "add $busid" > "$sys/bus/usb/drivers/usbip-host/match_busid"
printf "$busid" > "$sys/bus/usb/drivers/usbip-host/bind"
printf '.' 1<> "/run/usbip-refresh-$busid"
```

```console
$ sudo chmod +x /usr/local/libexec/usbip-bind
$ sudo udevadm control -R
$ sudo udevadm trigger /sys/bus/usb
```

To export:
```console
$ sudo python3 -m not_my_board._usbip export 1-5.1.1.1.4
```

To import:
```console
$ sudo python3 -m not_my_board._usbip import <IP> 1-5.1.1.1.4 0
```
