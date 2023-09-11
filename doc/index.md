# not-my-board

`not-my-board` is a tool to manage a pool of embedded hardware setups and to
schedule and provide access to those setups over a local network. The concept is
known as a *board farm*.

This project aims to give users raw access to the hardware setup without any
abstractions. Users should be able to use the same tools they'd use, if they
plug in the hardware directly. Maintaining abstractions separate from the board
farm has the benefit, that those abstractions can be used on locally attached
hardware as well.

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

how-to-guides/usb-export
how-to-guides/usb-import
```

```{toctree}
:caption: Reference
:hidden:

reference/cli
reference/export-description
reference/import-description
```

```{toctree}
:caption: Explanation
:hidden:

explanation/overview
explanation/sensible-usb-devices
```
