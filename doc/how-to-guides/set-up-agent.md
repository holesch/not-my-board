# How to Set up an Agent

This guide shows you how to persistently set up an *Agent* with `systemd`.

## Configuring Permissions

To be able to run `not-my-board` client commands without root, you need to
allow your user to access the agent.

First, create a new group `not-my-board` and add your user to it:
```{code-block} console
$ sudo groupadd --system not-my-board
$ sudo usermod -a -G not-my-board "$USER"
```

Log out and log back in again for the changes to take effect.

## Configuring the Service

Configure `systemd` to create and listen on a Unix domain socket:
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-agent.socket

[Unit]
Description=Board Farm Agent Socket

[Socket]
ListenStream=/run/not-my-board-agent.sock
SocketGroup=not-my-board
SocketMode=0660

[Install]
WantedBy=sockets.target
```

Then create a `systemd` service, that is started, the fist time a `not-my-board`
command connects to the *Agent* (replace `<my-hub-address>` with the address or
domain name of the *Hub*):
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-agent.service

[Unit]
Description=Board Farm Agent

[Service]
ExecStart=/usr/local/bin/not-my-board agent --fd 0 https://<my-hub-address>
StandardInput=socket
StandardOutput=journal
SyslogIdentifier=not-my-board-agent
```

If authentication is configured in the *Hub*, log in:
```console
$ sudo not-my-board login https://<my-hub-address>
```

Enable and start the socket:
```console
$ sudo systemctl enable --now not-my-board-agent.socket
```
