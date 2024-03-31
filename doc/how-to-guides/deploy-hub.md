# How to Deploy the Hub

This guide shows you how to deploy the *Hub*. There are of course many different
ways to deploy a Python application, but this guide shows you one way to get
started.

Configure `systemd` to listen on port `80`:
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-hub.socket

[Unit]
Description=Board Farm Hub Socket

[Socket]
ListenStream=80

[Install]
WantedBy=sockets.target
```

Then configure the `systemd` service, that is started, when someone connects to
this socket. With this service file, `systemd` drops privileges, starts uvicorn
(an ASGI server), which takes the socket and handles the requests with the
`not-my-board` *Hub* (written as an ASGI application):
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-hub.service

[Unit]
Description=Board Farm Hub

[Service]
ExecStart=/opt/pipx/venvs/not-my-board/bin/uvicorn --fd 0 not_my_board:asgi_app
StandardInput=socket
StandardOutput=journal
PrivateTmp=yes
PrivateNetwork=yes
User=nobody
```

Finally enable and start the socket:
```console
$ sudo systemctl enable --now not-my-board-hub.socket
```
