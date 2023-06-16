#!/bin/sh

PS4=">>> "
set -ex

ip addr add 192.168.200.2/24 dev eth1
ip link set eth1 up

mkdir src
mount -t 9p -o trans=virtio -oversion=9p2000.L vfs src

pip install --no-index --find-links ./src/scripts/_vmctl/img/pip-cache --no-build-isolation --editable ./src/

echo 0 > /sys/bus/usb/drivers_autoprobe
modprobe usbip-host

mkdir /usr/local/lib/mdev
cat > /usr/local/lib/mdev/usbip << "EOF"
busid="${DEVPATH##*/}"
printf "add $busid" > /sys/bus/usb/drivers/usbip-host/match_busid
printf "$busid" > /sys/bus/usb/drivers/usbip-host/bind
printf '.' 1<> "/run/usbip-refresh-$busid"
EOF
chmod +x /usr/local/lib/mdev/usbip

cat >> /etc/mdev.conf << "EOF"
bus/usb/.* root:root 0600 @/usr/local/lib/mdev/usbip
EOF

/etc/init.d/mdev restart
