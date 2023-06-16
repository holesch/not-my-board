#!/bin/sh

PS4=">>> "
set -ex

rm -f /etc/motd

setup-hostname -n qemu

cat > /etc/resolv.conf << EOF
nameserver 8.8.8.8
nameserver 8.8.4.4
EOF

setup-interfaces -i << EOF
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
EOF

setup-timezone -z UTC

setup-user -au admin
echo "permit nopass :wheel" >> /etc/doas.d/doas.conf

# configure sshd
sed -e 's/#PermitEmptyPasswords no/PermitEmptyPasswords yes/' \
    -i /etc/ssh/sshd_config
cat >> /etc/conf.d/sshd << EOF
sshd_disable_keygen="yes"
EOF

# disable gettys
sed -i 's/tty\d::.*getty.*/#\0/' /etc/inittab
# auto login
cat >> /etc/inittab << EOF
ttyS0::respawn:/bin/login -f admin
EOF

cat >> /etc/modules << EOF
virtio-net
virtio-pci
xhci-pci
EOF

enable_services() {
    runlevel="$1"
    shift
    for service in "$@"; do
        rc-update add "$service" "$runlevel"
    done
}

enable_services boot \
    bootmisc \
    devfs \
    hostname \
    mdev \
    modules \
    networking \
    sysctl \
    sysfs \
    syslog \
    ;

enable_services default \
    sshd \
    ;

enable_services shutdown \
    killprocs \
    mount-ro \
    savecache \
    ;

pip download --dest /mnt/scripts/_vmctl/img/pip-cache /mnt
