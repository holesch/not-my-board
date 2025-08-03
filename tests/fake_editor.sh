#!/bin/sh -e

exec sed -i 's/port_num = 0/port_num = 1/' "$@"
