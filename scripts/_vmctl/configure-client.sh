#!/bin/sh

PS4=">>> "
set -ex

ip addr add 192.168.200.3/24 dev eth1
ip link set eth1 up

mkdir src
mount -t 9p -o trans=virtio -oversion=9p2000.L vfs src

pip install --no-index --find-links ./src/scripts/_vmctl/img/pip-cache --no-build-isolation --editable ./src/

modprobe vhci-hcd
