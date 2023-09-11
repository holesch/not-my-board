# Export Description

The export description describes the *Place*, that the exporter makes available
in the board farm. The file format is [TOML](https://toml.io/en/).

## Settings

### `port`

**Type:** Number \
**Required:** Yes

Configures the port, that the exporter listens on. Must be a valid TCP port. The
port must be open for agents.

### `parts`

**Type:** Array of tables \
**Required:** Yes

List of parts, that the *Place* is made out of. There must be at least one
element.

### `parts[].compatible`

**Type:** Array of strings \
**Required:** Yes

List of tags, that describe this part. Tags are free-form strings.

Tags are used by users of the board farm for filtering *Places*, so they should
be defined and agreed upon by every participant of the board farm.

### `parts[].tcp`

**Type:** Table \
**Required:** No

Optional table to describe exported TCP ports. Contains zero or more elements.

### `parts[].tcp.<if-name>`

**Type:** Table \
**Required:** No

Describes one exported TCP port. `<if-name>` is a free-form string.

`<if-name>s` are used by users of the board farm for filtering *Places*, so they
should be defined and agreed upon by every participant of the board farm.

### `parts[].tcp.<if-name>.host`

**Type:** String \
**Required:** Yes

Must be a valid host name or IP address. This value together with `port` defines
the TCP port, that is exported.

### `parts[].tcp.<if-name>.port`

**Type:** Number \
**Required:** Yes

Must be a valid TCP port. This value together with `host` defines the TCP port,
that is exported.

### `parts[].usb`

**Type:** Table \
**Required:** No

Optional table to describe exported USB ports. Contains zero or more elements.

### `parts[].usb.<if-name>`

**Type:** Table \
**Required:** No

Describes one exported USB port. `<if-name>` is a free-form string.

`<if-name>s` are used by users of the board farm for filtering *Places*, so they
should be defined and agreed upon by every participant of the board farm.

### `parts[].usb.<if-name>.usbid`

**Type:** String \
**Required:** Yes

Configures a USB port, that is exported. Must be a valid USB ID in the form
`<busnum>-<devpath>`.

## Example

Here's an example of an export description:
```{code-block} toml
port = 2192

[[parts]]
compatible = [
    "raspberry-pi",
]
usb.usb0 = { usbid = "1-3" }
usb.serial = { usbid = "1-4.2" }

[[parts]]
compatible = [
    "my-power-supply",
]
tcp.scpi = { host = "192.168.116.8", port = 5025 }
```
