# How to Import USB Devices

This guide shows you how to attach remote USB devices from the board farm.

Create the import description for the place you want to import, for example:
```{code-block} toml
:caption: example.toml

[parts.example]
compatible = [ "example-board" ]
usb.usb0 = { port_num = 0 }
usb.usb-serial = { port_num = 1 }
```

Just count up the `port_num` for every USB interface. With the Kernel defaults
there are `8` ports available. Use `not-my-board usb-ports --all` to list the
available ports, and to see which of them are in use. See the
[command line reference](../reference/cli.md) for details.

Finally, reserve and attach the place:
```console
$ not-my-board attach ./example.toml
```
