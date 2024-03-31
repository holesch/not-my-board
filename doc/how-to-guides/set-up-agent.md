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

Create a `systemd` service file (replace `<my-hub-address>` with the address or
domain name of the *Hub*):
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-agent.service

[Unit]
Description=Board Farm Agent

[Service]
ExecStart=/usr/local/bin/not-my-board agent http://<my-hub-address>

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```console
$ sudo systemctl enable --now not-my-board-agent
```
