#!/bin/sh

PS4=">>> "
set -ex

ip addr add 192.168.200.2/24 dev eth1
ip link set eth1 up

# meson-python needs to write in the source directory while installing the
# package. To not modify the actual source directory, mount it with a writable
# overlay.
install -o admin -g admin -d src .src-ro .src-upper .src-work
mount -t 9p -o trans=virtio -oversion=9p2000.L vfs .src-ro
mount -t overlay -olowerdir=.src-ro,upperdir=.src-upper,workdir=.src-work overlay src

pip install --no-index --find-links ./src/scripts/_vmctl/img/pip-cache --no-build-isolation --config-settings=builddir="$PWD/build" --editable ./src/
chown -R admin:admin build

echo 0 > /sys/bus/usb/drivers_autoprobe
modprobe usbip-host

cat >> /etc/mdev.conf << "EOF"
SUBSYSTEM=usb;DEVTYPE=usb_device;DEVPATH=.;.* root:root 0600 @not-my-board uevent "$DEVPATH"
EOF

echo > /dev/mdev.seq
echo > /dev/mdev.log
# mark busid 2-1, so it will be bound to usbip-host driver
echo > /run/usbip-refresh-2-1

/etc/init.d/mdev restart
