# not-my-board

`not-my-board` is a tool to manage a pool of embedded hardware setups and to
schedule and provide access to those setups over a local network. The concept is
known as a *board farm*.

## Why use a board farm?

- **same board for development and CI:** You have access to the same setup when
  writing the tests as the CI has when running the tests, so you don't have to
  figure out the differences when something goes wrong.
- **share scarce boards and equipment:** If you just have a single setup of a
  kind, you can share it between multiple developers without carrying it around.
- **centralize maintenance of hardware setups:** You can make a person or team
  responsible for maintaining all boards in the farm.
- **remote access from anywhere:** Since you already access the boards remotely
  when in the office, you can access them the same way when working from home.
- **make room on your desk:** There's a limit of how many boards you can fit on
  your desk. With a board farm you have access to all the boards without giving
  up any space on your desk.

## Why use `not-my-board`?

- **raw access to boards**: `not-my-board` puts no abstractions between you and
  your board. It forwards USB devices with USB/IP and tunnels TCP ports to your
  host.
- **use the same tools**: You don't need to learn new tools or libraries to work
  with your board. Just flash your board with the vendor tool you used before or
  access the serial console with `/dev/ttyUSB0`.
- **independent of test framework**: `not-my-board` doesn't run your tests. It
  just makes the board available on your host, so you can run any test framework
  you like.

```{toctree}
:hidden:

installation
```

```{toctree}
:caption: Tutorials
:hidden:

tutorials/sharing-http-server
```

```{toctree}
:caption: How-to Guides
:hidden:

how-to-guides/deploy-hub
how-to-guides/set-up-exporter
how-to-guides/set-up-agent
how-to-guides/usb-export
how-to-guides/usb-import
how-to-guides/import-places-in-ci
```

```{toctree}
:caption: Reference
:hidden:

reference/cli
reference/hub-configuration
reference/export-description
reference/import-description
```

```{toctree}
:caption: Explanation
:hidden:

explanation/overview
explanation/sensitive-usb-devices
```
