# Hub Configuration

The *Hub* loads its configuration on startup from
`/etc/not-my-board/not-my-board-hub.toml`. The file format is
[TOML](https://toml.io/en/).

## Settings

### `log_level`

**Type:** String \
**Required:** No

Configures the log level. Can be one of `debug`, `info`, `warning` or `error`.

## Example

Here's an example of a *Hub* configuration:
```{code-block} toml
:caption: /etc/not-my-board/not-my-board-hub.toml

log_level = "info"
```
