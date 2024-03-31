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
there are `8` ports available.

Finally, reserve and attach the place:
```console
$ not-my-board attach ./example.toml
```
