# Installation

Since you'll need to run `not-my-board` as root user to export USB devices,
you should install it globally in an isolated environment with [`pipx`][1].

[1]: https://pypa.github.io/pipx/

Install `pipx`, e.g. on Ubuntu/Debian:
```console
$ sudo apt-get install pipx
```
and install `not-my-board` globally:
```console
$ sudo PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install not-my-board
```
