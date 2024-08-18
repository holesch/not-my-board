import pathlib


async def test_raw_usb_forwarding(vms):
    async with vms.exporter.ssh_task_root(
        "python3 -m not_my_board._usbip export 2-1", "usbip export", wait_ready=True
    ):
        async with vms.client.ssh_task_root(
            "python3 -m not_my_board._usbip import exporter.local 2-1 0",
            "usbip import",
        ):
            # wait for USB device to appear
            await vms.client.ssh_poll("test -e /dev/usbdisk")

            await vms.client.ssh("doas mount /media/usb")
            try:
                result = await vms.client.ssh("cat /media/usb/hello")
                assert result.stdout == "Hello, World!"
            finally:
                await vms.client.ssh("doas umount /media/usb")

    await vms.client.ssh_poll("! test -e /sys/bus/usb/devices/2-1")


async def test_usb_forwarding(vms):
    async with vms.hub.ssh_task("not-my-board hub", "hub", wait_ready=True):
        async with vms.exporter.ssh_task_root(
            "not-my-board export http://hub.local:2092 ./src/tests/qemu-usb-place.toml",
            "export",
            wait_ready=True,
        ):
            async with vms.client.ssh_task_root(
                "not-my-board agent http://hub.local:2092", "agent", wait_ready=True
            ):
                await vms.client.ssh("not-my-board attach ./src/tests/qemu-usb.toml")
                # TODO attach still returns before the device is available.
                # would be nice if it blocks until the device is ready.
                await vms.client.ssh_poll("test -e /sys/bus/usb/devices/2-1")

                result = await vms.client.ssh("not-my-board status")
                status_str = result.stdout.rstrip()
                status_lines = status_str.split("\n")
                assert len(status_lines) == 2
                header = status_lines[0].split()
                status_line = status_lines[1].split()

                assert header == [
                    "Place",
                    "Part",
                    "Type",
                    "Interface",
                    "Status",
                    "Port",
                ]
                assert status_line == [
                    "qemu-usb",
                    "flash-drive",
                    "USB",
                    "usb0",
                    "Up",
                    "2-1",
                ]

                try:
                    await vms.exporter.usb_detach()
                    await vms.client.ssh_poll("! test -e /sys/bus/usb/devices/2-1")
                finally:
                    await vms.exporter.usb_attach()

                await vms.client.ssh_poll("test -e /sys/bus/usb/devices/2-1")
                await vms.client.ssh("not-my-board detach -k qemu-usb")
                await vms.client.ssh("! test -e /sys/bus/usb/devices/2-1")

                result = await vms.client.ssh("not-my-board status")
                status_str = result.stdout.rstrip()
                status_lines = status_str.split("\n")
                status_line = status_lines[1].split()
                assert status_line[5] == "1-1/2-1"

    # When the exporter is killed, then it should clean up and restore the
    # default USB driver.
    result = await vms.exporter.ssh("readlink /sys/bus/usb/devices/2-1/driver")
    driver_name = pathlib.Path(result.stdout).name
    assert driver_name == "usb"
