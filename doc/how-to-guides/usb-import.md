# How to Import USB Devices

This guide shows you how to attach remote USB devices from the board farm.

## Preparing the USB/IP Driver

To be able to run the `not-my-board` Agent without root, you need to allow your
user to attach devices shared with USB/IP.

First, create a new group `vhci` and add your user to it:
```{code-block} console
$ sudo groupadd --system vhci
$ sudo usermod -a -G vhci "$USER"
```

Then configure the system to load the `vhci-hcd` Kernel module on every boot:
```{code-block} none
:caption: /etc/modules-load.d/vhci-hcd.conf

vhci-hcd
```

Now configure `systemd-tmpfiles` to change the permissions of the files in
sysfs, that are used to attach remote devices:
```{code-block} none
:caption: /etc/tmpfiles.d/not-my-board.conf

# Allow users in the group "vhci" to attach and detach devices with USB/IP
z /sys/devices/platform/vhci_hcd.0/attach 0220 root vhci
z /sys/devices/platform/vhci_hcd.0/detach 0220 root vhci
```

Reboot your system for the changes to take effect.

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
