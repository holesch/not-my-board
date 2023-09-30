# How to Export USB Devices

This guide shows you how to make USB devices available in the board farm.

## Device Manager Integration

`not-my-board` exports not only the currently plugged in USB device, but every
USB device, that appears on a specific USB port. For that to work,
`not-my-board` needs support from the device manager to get notified when a new
device appears and to load the `usbip-host` driver instead of the default
driver.

### Integration with `udev`

Most popular Linux distributions use `udev` as a device manager. To integrate
`not-my-board` create a new rules file:

```{code-block}
:caption: /etc/udev/rules.d/85-usbip.rules

# disable autoprobe
ACTION=="add|change", KERNEL=="usb", SUBSYSTEM=="subsystem", \
       ATTR{drivers_autoprobe}="0"

ACTION=="add", SUBSYSTEM=="usb", \
    RUN+="/usr/bin/systemd-cat -t not-my-board-uevent /usr/local/bin/not-my-board uevent --verbose '$devpath'"
```

```{note}
Drivers need to be loaded, before the device can be probed successfully. Make
sure this rules file comes after the driver loading rules file (by default
`80-drivers.rules`).
```

Then you need to reload the rules files and trigger the USB subsystem, to
disable auto-probe.

```{code-block} console
$ sudo udevadm control --reload
$ sudo udevadm trigger /sys/bus/usb
```

```{note}
This change is permanent, you don't need to repeat this after a reboot.
```

### Integration with `mdev`

Some Linux distributions, like Alpine Linux, use `mdev` as a device manager by
default. To integrate `not-my-board` add a new rule to the `mdev` config file:

```{code-block}
:caption: /etc/mdev.conf

SUBSYSTEM=usb;DEVPATH=.;.* root:root 0600 @not-my-board uevent --verbose "$DEVPATH"
```

Make sure to put this rule after the `MODALIAS` driver loading rule and modify
the `MODALIAS` rule to continue with the other rules by prepending a `-`:

```
-$MODALIAS=.*    root:root 0660 @modprobe -b "$MODALIAS"
```

Then disable auto-probe:
```console
$ sudo sh -c 'echo 0 > /sys/bus/usb/drivers_autoprobe'
```

## Exporting the Device

Before you can export the device, you need to find out the `usbid` of the
device. With `udev` you can monitor Kernel uevents while you plug in the device.
In the following example the `usbid` is `3-7`:
```console
$ sudo udevadm monitor --kernel --subsystem-match=usb
[...]
KERNEL[14386.901673] add      /devices/pci0000:00/0000:00:14.0/usb3/3-7 (usb)
[...]
```

Alternatively you can check the Kernel logs with `dmesg`:
```console
$ sudo dmesg | grep usb
[...]
[14386.747654] usb 3-7: new high-speed USB device number 18 using xhci_hcd
[...]
```

Now create the export description with the `usbid` of the USB device. If your
board has more than one USB interface, you can of course add them all:
```{code-block} toml
:caption: /etc/not-my-board/example.toml

port = 2192

[[parts]]
compatible = [ "example-board" ]
usb.usb0 = { usbid = "3-7" }
usb.usb-serial = { usbid = "3-8" }
```

Finally, use the export description to register the place in the board farm:
```console
$ sudo not-my-board export http://<board-farm-address>:2092 /etc/not-my-board/example.toml
```
