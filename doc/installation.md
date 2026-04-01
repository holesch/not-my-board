# Installation

Since you'll need to run `not-my-board` as root user for some operations, you
should install it globally in an isolated environment with [`pipx`][1].

[1]: https://pypa.github.io/pipx/

Install `pipx`, e.g. on Ubuntu/Debian:
```console
$ sudo apt-get install pipx
```
and install `not-my-board` globally:
```console
$ sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install not-my-board
```

## Shell Completion (Optional)

For a better command-line experience, you can enable shell completion for bash
or zsh.

See [Shell Completion](how-to-guides/shell-completion) for setup instructions.
