# About Sensitive USB Devices

Some USB implementations are more sensitive than others. Since there are slight
differences in the packets sent, when using USB/IP, those devices might break or
behave differently as they would, if they were connected directly.

One mitigation is to disable the automatic binding to device drivers on the host
side: By default the Kernel binds a matching driver, so that the device can be
used immediately. When exporting a USB device though, we need to bind the device
to the `usbip-host` driver instead of the default one. For most USB devices it
is fine to just unbind the default driver and bind the USB/IP one. This however
enumerates the device twice and can cause issues in sensitive USB stacks. So
instead of unbinding the default driver and binding the USB/IP driver after
that, we need to prevent binding the default driver in the first place. We do
this by changing the `drivers_autoprobe` setting (see
[](../how-to-guides/usb-export.md)) and deciding in user-space which driver we
want to bind (with `not-my-board uevent`). One example of a USB implementation,
that can't handle being probed twice, is the `i.MX 8M Nano` ROM-Code USB stack.
