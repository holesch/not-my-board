# Import Description

The import description is used to filter *Places* registered in the board farm.
The *Agent* uses this import description to reserve one of the matching
*Places*. A *Place* matches, when all parts match. A part matches, when:
- the tags in "compatible" are a subset of the exported part
- the USB and TCP interfaces are a subset of the exported interfaces (matched by
  `<if-name>`)

## Settings

### `auto_return_time`

**Type:** String \
**Required:** No \
**Default:** `"10h"`

Delay after which the reservation is automatically returned. The timer is reset
after editing the import description with the `not-my-board edit` command. The
time format uses a sequence of positive integers followed by lowercase time
units:

:::{table}
:align: left

Unit | Description
---- | -----------
`w`  | weeks
`d`  | days
`h`  | hours
`m`  | minutes
`s`  | seconds (optional)
:::

Units must appear in descending order of significance (e.g. weeks before days).
A delay of `0` disables the auto return.

Examples:
- `600`: 600 seconds or 10 minutes
- `10m`: 10 minutes
- `1h30m`: 1 hour 30 minutes (90 minutes)

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

Specifies the local virtual USB port number to which the remote USB device will
be attached. With the default Kernel configuration, there are 8 ports available
(values from `0` to `7`). Together with the speed of the imported device (High
Speed (USB 2.0) or Super Speed (USB 3.0)) the appropriate USB port is selected.

On a given host, the USB port will always be the same, if the device speed and
`port_num` value are the same. With that guarantee, consistent device manager
rules (e.g. udev rules) can be created.

Only one device can be attached to one port. Choose unique `port_num` values for
each import description.

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
