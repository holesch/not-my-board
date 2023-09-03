#!/bin/sh

main() {
    vm="${1:?}"
    PS4=">>> "
    set -ex

    case "$vm" in
    server)
        setup_network_server
        install_project
        ;;
    exporter)
        setup_network_exporter
        install_project
        reconfigure_device_manager
        ;;
    client)
        setup_network_client
        install_project
        load_vhci_hcd
        ;;
    *)
        echo "error: invalid VM: $vm"
        exit 1
    esac
}

setup_network_server() {
    ip link add name br0 type bridge
    ip link set eth1 up
    ip link set dev eth1 master br0
    ip link set eth2 up
    ip link set dev eth2 master br0

    ip addr add 192.168.200.1/24 dev br0
    ip link set br0 up
}

setup_network_exporter() {
    ip addr add 192.168.200.2/24 dev eth1
    ip link set eth1 up
}

setup_network_client() {
    ip addr add 192.168.200.3/24 dev eth1
    ip link set eth1 up
}

install_project() {
    mount_project_source

    pip install --no-index --find-links ./src/scripts/_vmctl/img/pip-cache --no-build-isolation --config-settings=builddir="$PWD/build" --editable ./src/
    chown -R admin:admin build
}

mount_project_source() {
    # meson-python needs to write in the source directory while installing
    # the package. To not modify the actual source directory, mount it with
    # a writable overlay.
    install -o admin -g admin -d src .src-ro .src-upper .src-work
    mount -t 9p -o trans=virtio -oversion=9p2000.L vfs .src-ro
    mount -t overlay -olowerdir=.src-ro,upperdir=.src-upper,workdir=.src-work overlay src
}

reconfigure_device_manager() {
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
}

load_vhci_hcd() {
    modprobe vhci-hcd
    for file_name in attach detach; do
        chgrp vhci "/sys/devices/platform/vhci_hcd.0/$file_name"
        chmod g+w "/sys/devices/platform/vhci_hcd.0/$file_name"
    done
}

main "$@"
