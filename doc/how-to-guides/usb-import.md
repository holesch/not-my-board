# How to Import USB Devices

This guide shows you how to attach remote USB devices from the board farm.

## Preparing the USB/IP Driver

To be able to run `not-my-board` client commands without root, you need to
allow your user to access the agent.

First, create a new group `not-my-board` and add your user to it:
```{code-block} console
$ sudo groupadd --system not-my-board
$ sudo usermod -a -G not-my-board "$USER"
```

Log out and log back in again for the changes to take effect.

## Importing the device

Create the import description for the place you want to import, e.g.:
```{code-block} toml
:caption: example.toml

[parts.example]
compatible = [ "example-board" ]
usb.usb0 = { port_num = 0 }
usb.usb-serial = { port_num = 1 }
```

Just count up the `port_num` for every USB interface. With the Kernel defaults
there are `8` ports available.

Finally, reserve and attach the place:
```console
$ not-my-board attach ./example.toml
```
