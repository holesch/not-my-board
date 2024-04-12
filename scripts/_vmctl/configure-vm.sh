#!/bin/sh

ip_hub=192.168.200.1
ip_exporter=192.168.200.2
ip_client=192.168.200.3

main() {
    vm="${1:?}"
    PS4=">>> "
    set -ex

    case "$vm" in
    hub)
        setup_network_hub
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
        ;;
    *)
        echo "error: invalid VM: $vm"
        exit 1
    esac
}

setup_network_hub() {
    ip link add name br0 type bridge
    ip link set eth1 up
    ip link set dev eth1 master br0
    ip link set eth2 up
    ip link set dev eth2 master br0

    ip addr add "$ip_hub/24" dev br0
    ip link set br0 up

    cat >> /etc/hosts << EOF
127.0.0.1 hub.local
$ip_exporter exporter.local
$ip_client client.local
EOF
}

setup_network_exporter() {
    ip addr add "$ip_exporter/24" dev eth1
    ip link set eth1 up

    cat >> /etc/hosts << EOF
$ip_hub hub.local
127.0.0.1 exporter.local
$ip_client client.local
EOF
}

setup_network_client() {
    ip addr add "$ip_client/24" dev eth1
    ip link set eth1 up

    cat >> /etc/hosts << EOF
$ip_hub hub.local
$ip_exporter exporter.local
127.0.0.1 client.local
EOF
}

install_project() {
    mount_project_source

    pip install \
        --break-system-packages \
        --no-index \
        --find-links ./src/scripts/_vmctl/img/pip-cache \
        --no-build-isolation \
        --config-settings=builddir="$PWD/build" \
        --editable ./src/
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

    # continue with other rules after loading the Kernel driver
    sed -i 's/^$MODALIAS=.*/-\0/' /etc/mdev.conf

    # add not-my-board hook
    cat >> /etc/mdev.conf << "EOF"

SUBSYSTEM=usb;DEVPATH=.;.* root:root 0600 @not-my-board uevent --verbose "$DEVPATH"
EOF

    echo > /dev/mdev.seq
    echo > /dev/mdev.log

    /etc/init.d/mdev restart
}

main "$@"
