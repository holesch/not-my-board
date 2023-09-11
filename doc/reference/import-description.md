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

### `parts.<part-name>.usb.<if-name>.vhci_port`

**Type:** Number \
**Required:** Yes

Configures the *Agent* to attach the exported remote USB device to this local
USB Virtual Host Controller Interface (VHCI). The default Kernel config limits
the number of ports to `16`, that means valid values are in that case numbers
between `0` and `15`.

```{warning}
The port that can be used depends on the speed of the USB device. High Speed
devices can be attached to ports `0` to `7` and SuperSpeed devices can be
attached to ports `8` to `15`. For now the user needs to configure this
correctly, but this will be handled by `not-my-board` in a future version.
```

## Example

Here's an example of an import description:
```{code-block} toml
[parts.pi]
compatible = [ "raspberry-pi" ]
usb.usb0 = { vhci_port = 0 }
usb.serial = { vhci_port = 1 }

[parts.power]
compatible = [ "my-power-supply" ]
tcp.scpi = { local_port = 5025 }
```
