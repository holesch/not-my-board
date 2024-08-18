# How to Set up an Exporter

This guide shows you how to set up an *Exporter* persistently using `systemd`.

Create a `systemd` unit template, so new *Exporter* instances can easily be
added (replace `<my-hub-address>` with the address or domain name of the *Hub*):
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-exporter@.service

[Unit]
Description=Board Farm Exporter For Place %I

[Service]
ExecStart=/usr/local/bin/not-my-board export https://<my-hub-address> /etc/not-my-board/export-descriptions/%i.toml
Restart=on-failure
RestartSec=10s
SyslogIdentifier=not-my-board-export

[Install]
WantedBy=multi-user.target
```

Create the export description in `/etc/not-my-board/export-descriptions`, for example:
```{code-block} toml
:caption: /etc/not-my-board/export-descriptions/example.toml

port = 29201

[[parts]]
compatible = [ "example-board" ]
usb.usb0 = { usbid = "3-7" }
usb.usb-serial = { usbid = "3-8" }
```

If the host has a firewall, you might need to open ports, so *Agents* can
connect to the *Exporter* directly. To open a range of 100 ports on the `eno1`
interface with `ufw`, use the following command:
```console
$ sudo ufw allow in on eno1 to any port 29200:29299 proto tcp comment 'not-my-board exporter'
```

If authentication is configured in the *Hub*, log in:
```console
$ sudo not-my-board login https://<my-hub-address>
```

Finally enable and start the `systemd` service:
```console
$ sudo systemctl enable --now not-my-board-exporter@example
```

:::{note}
The `example` instance name in the above command refers to the export
description in `/etc/not-my-board/export-descriptions`. By changing the instance
name, you can easily add new *Exporter* instances.
:::
