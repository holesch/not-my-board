#!/bin/sh

PS4=">>> "
set -ex

ip link add name br0 type bridge
ip link set eth1 up
ip link set dev eth1 master br0
ip link set eth2 up
ip link set dev eth2 master br0

ip addr add 192.168.200.1/24 dev br0
ip link set br0 up

mkdir src
mount -t 9p -o trans=virtio -oversion=9p2000.L vfs src

pip install --no-index --find-links ./src/scripts/_vmctl/img/pip-cache --no-build-isolation --editable ./src/
