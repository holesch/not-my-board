# Components Overview

A board farm set up with `not-my-board` consists of multiple components:

- **Place**: The physical embedded hardware setup.
- **Hub**: A single instance, that schedules access to *Places*.
- **Exporter**: This runs on the host, where the *Place* is connected. It
  registers the *Place* with the *Hub*.
- **Agent**: A board farm user runs this on their host. It requests a *Place*
  from the *Hub*, connects directly to the *Exporter* and tunnels resources from
  the *Exporter* to the host of the user.
- **Client**: The CLI, that controls the *Agent*.

## Place

A *Place* is not only an embedded board, but it includes all the equipment
required to develop on that board, too. Equipment can be for example a
controllable power supply, a wireless access point or a camera. Of course it's
not even limited to a single board: It can be a system consisting of multiple
connected boards. Every board or equipment is called a *Part*.

## Hub

The *Hub* provides its interface over HTTP(S) and WebSocket. *Exporter* and
*Agent* stay registered as long as the WebSocket connection is alive. If the
*Exporter* connection breaks, then the *Place* will no longer be scheduled. If
the *Agent* connection breaks, then the user loses access and the *Place* can be
reserved by another user.

The *Hub* only provides a list of known *Places* with their description. The
*Agent* then filters the list and asks the *Hub* to reserve one of all the
possible candidate *Places*. As soon as one of the candidates is free, the *Hub*
let's both *Exporter* and *Agent* know about the new reservation.

## Exporter

The *Exporter* opens a WebSocket connection to the *Hub* and exports the
resources as an HTTP proxy. By not exporting the ports directly, only the HTTP
proxy port needs to be opened in the firewall and the *Exporter* can
authenticate the user before granting access.

Resources can be exported as a TCP port (like the SCPI interface of a power
supply), or over USB/IP, like the USB port of the board or the USB to serial
converter.

The proxy uses IP-based authentication to avoid the TLS overhead. Once the
*Place* of the *Exporter* is reserved, the *Hub* tells the *Exporter* which IP
address to allow access.

The exporter assigns specific tags to the exported parts of the place. Those
tags describe to what the parts are compatible with. An *Agent* can then filter
based on those tags.

## Agent

The *Agent* is a long running process on the host of the user, to keep the
connection to the *Hub* open and to tunnel the resources from the *Exporter*. It
listens on a Unix domain socket for commands from the *Client*.

The *Client* provides an import description of the *Place* it wants, i.e. which
parts it needs (identified by the compatible tags) and where to attach those
parts. For example: I need a "Raspberry Pi" and want its USB serial adapter
attached to USB port `3-4` and its USB port to `3-5`. The *Agent* then filters
all the exported *Places* based on that description and gives the *Hub* a list
of the matching candidates. As soon as the *Hub* reserves one of the candidates,
the *Agent* connects directly with the *Exporter* and attaches the resources as
requested.

## Client

The *Client* is everything that isn't a long running background process. For
example the `not-my-board attach` command acts as a *Client*: It connects to the
*Agent* UNIX domain socket, request a new *Place* to be attached and then exits
again. Those are the commands, that users would run during their day-to-day
development work.
