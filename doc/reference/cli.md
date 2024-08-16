# Command Line Interface

Here's a description of all the commands and options `not-my-board` supports.

## Commands

**`hub`** \[**`-h`**|**`--help`**\]
: Start the board farm *Hub*. There should be only one hub in the entire
  network.

**`export`** \[**`-h`**|**`--help`**\] \[**`--cacert`** *cacert*\] \[**`--token-cmd`** *token_cmd*\] *hub_url* *export_description*
: Make connected boards and equipment available in the board farm.

**`agent`** \[**`-h`**|**`--help`**\] \[**`--cacert`** *cacert*\] \[**`--token-cmd`** *token_cmd*\] \[**`--fd`** *fd*\] *hub_url*
: Start an *Agent*.

**`login`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] \[**`--cacert`** *cacert*\] *hub_url*
: Login user.

**`reserve`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] \[**`-n`**|**`--with-name`** *name*\] *import_description*
: Reserve a *Place*.

**`return`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] *name*
: Return a reserved *Place*.

**`attach`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] \[**`-k`**|**`--keep-others`**\] *name*|*import_description*
: Attach a reserved *Place*. As a convenience this will also implicitly reserve
  the *Place*, if it's not reserved, yet.

**`detach`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] \[**`-k`**|**`--keep`**\] *name*
: Detach an attached *Place*. By default this will also return the reservation:
  Use {option}`--keep <not-my-board --keep>` to keep the reservation.

**`list`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] \[**`-n`**|**`--no-header`**\]
: List reserved *Places*.

**`status`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] \[**`-n`**|**`--no-header`**\]
: Show status of attached places and its interfaces.

**`edit`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] *name*
: Edit the import description of a reserved or attached place. It opens a
  temporary file with the current import description with the configured editor.
  After the editor is closed, the reservation is updated. The editor used is
  chosen from the `VISUAL` or the `EDITOR` environment variable, in that order.
  If none is set, then `vi` is used. This doesn't modify the actual import
  description used to attach the place.

**`uevent`** \[**`-h`**|**`--help`**\] \[**`-v`**|**`--verbose`**\] *devpath*
: Handle Kernel uevent for USB devices. This should be called by the device
  manager, e.g. *udev*(7).

## Options

```{program} not-my-board
```

```{option} -h, --help
Show help message and exit.
```

```{option} --cacert cacert
Load additional CA certificates to trust when using TLS. *cacert* is a path to a
file of concatenated CA certificates in PEM format. Can be used multiple times
to load more than one file. System default CA certificates are always trusted.
```

```{option} --token-cmd token_cmd
Get the ID token used for authentication from the shell command *token_cmd*. The
*token_cmd* can have the placeholders `${issuer}` and `${client_id}`. The curly
braces are optional, if no alphanumeric character (including underscores) is
right after the placeholder. A `$` can be escaped with `$$`.

This option is an alternative to the `login` command. It can be used in
non-interactive environments.
```

```{option} --fd fd
Use file descriptor *fd*, instead of creating the listening socket. Should be a
Unix domain socket with the address `/run/not-my-board-agent.sock`.
```

```{option} hub_url
HTTP or HTTPS URL of the *Hub*.
```

```{option} export_description
Path to an export description file.
```

```{option} import_description
Path to an import description file or name of an import description. If a name
is given, then the file is searched for in `./.not-my-board/<name>.toml` of the
current working directory and every parent up to either `$HOME` or `/`. If it's
not found, then it falls back to `$XDG_CONFIG_HOME/not-my-board/<name>.toml` or
`~/.config/not-my-board/<name>.toml` if `$XDG_CONFIG_HOME` is not set.
```

```{option} -v, --verbose
Enable debug logs.
```

```{option} -n name, --with-name name
Reserve under a different name.
```

```{option} name
Name of a reserved place.
```

```{option} -k, --keep-others
Don't return all other reservations.
```

```{option} -k, --keep
Don't return reservation.
```

```{option} -n, --no-header
Hide table header.
```

```{option} devpath
devpath attribute of uevent.
```
