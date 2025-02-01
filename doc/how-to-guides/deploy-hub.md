# How to Deploy the Hub

This guide shows you how to deploy the *Hub*. There are of course many different
ways to deploy a Python application, but this guide shows you one way to get
started.

## Generate Self-Signed Certificates

This step is not necessary, if you have another way to get certificates. If you
don't, then continue.

First generate the self signed root CA:
```console
$ sudo openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -days 365000 -nodes -keyout /etc/not-my-board/not-my-board-root-ca.key -out /etc/not-my-board/not-my-board-root-ca.crt -subj "/CN=not-my-board-root-ca" -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign"
```

Then generate the certificate for the *Hub*. Replace `example.com` with the
hostname of your server:
```console
$ hostname="example.com"
$ sudo openssl req -x509 -CA /etc/not-my-board/not-my-board-root-ca.crt -CAkey /etc/not-my-board/not-my-board-root-ca.key -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -days 365000 -nodes -keyout /etc/not-my-board/not-my-board.key -out /etc/not-my-board/not-my-board.crt -subj "/CN=$hostname" -addext "subjectAltName=DNS:$hostname,DNS:*.$hostname" -addext "basicConstraints=CA:FALSE" -addext "keyUsage=digitalSignature,keyEncipherment" -addext "extendedKeyUsage=serverAuth"
```

Finally delete the root CA key, so no malicious certificate can be generated
from it:
```console
$ sudo rm /etc/not-my-board/not-my-board-root-ca.key
```

Share the root CA certificate (`/etc/not-my-board/not-my-board-root-ca.crt`)
with users of the board farm. They will need to start their *Agents* and
*Exporters* with the {option}`--cacert <not-my-board --cacert>` option to trust
this root CA.

## Configuring Authentication and Authorization

Authentication and authorization is optional, but recommended for a permanent
deployment. `not-my-board` uses OpenID Connect for authentication. If you don't
already have an OpenID provider, you can use either self-host an open source
implementation like Keycloak or use services like Google Identity or Microsoft
Entra ID.

Start by creating a new Client in the OpenID provider. `not-my-board` uses the
"Authorization Code Flow with PKCE". The redirect URI is `https://<host>/oidc-callback`.

Add the OpenID configuration information to the *Hub* configuration. For example
when using Keycloak:

```{code-block} toml
:caption: /etc/not-my-board/hub.toml

[auth]
issuer = "http://keycloak.example.com/realms/master"
# The Client ID is configured in Keycloak
client_id = "not-my-board"
# Don't show all claims when logging in
show_claims = ["sub", "preferred_username"]
```

Then add permissions for users. You can get the claims from users after they log
in with `not-my-board login`, or from the OpenID provider.

```{code-block} toml
:caption: /etc/not-my-board/hub.toml

# [...]

[[auth.permissions]]
claims.sub = "11111111-2222-3333-4444-000000000000"
roles = ["exporter", "importer"]
```

:::{note}
The *Hub* needs to be restarted, when the configuration changes.
:::

## Configuring the systemd Service

Configure `systemd` to listen on port `443`:
```{code-block} systemd
:caption: /etc/systemd/system/not-my-board-hub.socket

[Unit]
Description=Board Farm Hub Socket

[Socket]
ListenStream=443

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
LoadCredential=certkey:/etc/not-my-board/not-my-board.key
ExecStart=/opt/pipx/venvs/not-my-board/bin/uvicorn --fd 0 --ssl-keyfile ${CREDENTIALS_DIRECTORY}/certkey --ssl-certfile /etc/not-my-board/not-my-board.crt not_my_board:asgi_app
StandardInput=socket
StandardOutput=journal
SyslogIdentifier=not-my-board-hub
DynamicUser=yes
```

Finally enable and start the socket:
```console
$ sudo systemctl enable --now not-my-board-hub.socket
```
