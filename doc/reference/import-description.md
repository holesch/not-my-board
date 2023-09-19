# Import Description

The import description is used to filter *Places* registered in the board farm.
The *Agent* uses this import description to reserve one of the matching
*Places*. A *Place* matches, when all parts match. A part matches, when:
- the tags in "compatible" are a subset of the exported part
- the USB and TCP interfaces are a subset of the exported interfaces (matched by
  `<if-name>`)

## Settings

### `parts`

**Type:** Table \
**Required:** Yes

Holds all parts, that are expected to be part of a *Place*.

### `parts.<part-name>`

**Type:** Table \
**Required:** Yes

Describes one of the parts, that is expected to be part of a *Place*.
`<part-name>` is a free-form string and is only used locally, e.g. in the output
of the `not-my-board status` command.

### `parts.<parts-name>.compatible`

**Type:** Array of strings \
**Required:** Yes

List of tags, that describe this part. Tags are free-form strings.

Tags are used for filtering *Places*, so they should be defined and agreed upon
by every participant of the board farm.

### `parts.<part-name>.tcp`

**Type:** Table \
**Required:** No

Optional table to request exported TCP ports. Contains zero or more elements.

### `parts.<part-name>.tcp.<if-name>`

**Type:** Table \
**Required:** No

Requests one exported TCP port. `<if-name>` is a free-form string.

`<if-name>s` are used for filtering *Places*, so they should be defined and
agreed upon by every participant of the board farm.

### `parts.<part-name>.tcp.<if-name>.local_port`

**Type:** Number \
**Required:** Yes

Configures the *Agent* to listen on that port on `localhost`. Connections to
that port are forwarded to the exported TCP port with the matching `<if-name>`.
Must be a valid TCP port.

### `parts.<part-name>.usb`

**Type:** Table \
**Required:** No

Optional table to request exported USB ports. Contains zero or more elements.

### `parts.<part-name>.usb.<if-name>`

**Type:** Table \
**Required:** No

Request one exported USB port. `<if-name>` is a free-form string.

`<if-name>s` are used for filtering *Places*, so they should be defined and
agreed upon by every participant of the board farm.

### `parts.<part-name>.usb.<if-name>.port_num`

**Type:** Number \
**Required:** Yes

Configures the virtual USB port, which is used to attach the exported remote USB
device. The *Agent* selects the actual virtual USB hub and port based on
`port_num` and the speed of the imported USB device. The default Kernel config
limits the number of ports, so that `port_num` must be between `0` and `7`.

## Example

Here's an example of an import description:
```{code-block} toml
[parts.pi]
compatible = [ "raspberry-pi" ]
usb.usb0 = { port_num = 0 }
usb.serial = { port_num = 1 }

[parts.power]
compatible = [ "my-power-supply" ]
tcp.scpi = { local_port = 5025 }
```
