# Hub Configuration

The *Hub* loads its configuration on startup from `/etc/not-my-board/hub.toml`.
You can override this location with the environment variable
`NOT_MY_BOARD_HUB_CONFIG`. The file format is [TOML](https://toml.io/en/).

## Settings

### `log_level`

**Type:** String \
**Required:** No

Configures the log level. Can be one of `debug`, `info`, `warning` or `error`.

### `auth`

**Type:** Table \
**Required:** No

Optional authorization configuration. If this is missing, everyone can export
and import *Places*. Permanent deployments should have this configuration.

### `auth.issuer`

**Type:** String \
**Required:** Yes

URL to the OpenID provider. `not-my-board` expects to find the OpenID provider
configuration at `<value>/.well-known/openid-configuration`.

### `auth.client_id`

**Type:** String \
**Required:** Yes

The client ID of `not-my-board`. Get this value from the OpenID provider.

### `auth.issuers`

**Type:** Table \
**Required:** No

Contains extra configuration per OpenID provider.

### `auth.issuers.<issuer_url>`

**Type:** Table \
**Required:** No

Contains configuration for the OpenID provider with the URL matching
`<issuer_url>`.

### `auth.issuers.<issuer_url>.show_claims`

**Type:** Array of strings \
**Required:** No

Allows the administrator to filter the shown claims of the OpenID Connect ID
token. The filtered claims are logged by the *Hub* and are shown to the users,
when they log in. Specify the claims an administrator might need to give the
user permissions. If the option is not set, then all claims are shown. If it's
set to an empty array, then no claims are shown.

### `auth.permissions`

**Type:** Array of tables \
**Required:** Yes

Defines whom to give which permissions based on their ID token.

### `auth.permissions[].claims`

**Type:** Table \
**Required:** Yes

Contains all the claims, that have to be in the ID token, in order for the
permission to be assigned.

### `auth.permissions[].claims.<required_claim>`

**Type:** List of strings, list of numbers, list of booleans, string, number or
boolean \
**Required:** Yes

Defines a claim that needs to be in the ID token of a client. If the value is a
list, then the value in the ID token is expected to be a list and it has to
contain at least the values defined with this option. If the value is not a
list, then the claim has to match exactly.

To uniquely identify a user, only the `sub` claim is necessary.

If the `iss` claim is not specified, it defaults to the value given in
[`auth.issuer`](#authissuer).

### `auth.permissions[].roles`

**Type:** List of strings \
**Required:** Yes

The roles to assign if all required claims are contained in the presented ID
token.

The following roles are defined:
- `exporter`: Can export *Places*
- `importer`: Can reserve and attach *Places*

## Example

Here's an example of a *Hub* configuration:
```{code-block} toml
:caption: /etc/not-my-board/hub.toml

log_level = "info"

[auth]
issuer = "http://keycloak.example.com/realms/master"
client_id = "not-my-board"

[auth.issuers."http://keycloak.example.com/realms/master"]
show_claims = ["sub", "preferred_username"]

[[auth.permissions]]
claims.sub = "11111111-2222-3333-4444-000000000000"
roles = ["exporter"]

[[auth.permissions]]
claims.sub = "11111111-2222-3333-4444-111111111111"
roles = ["importer"]
```

And here's an example with Microsoft Entra ID as OpenID provider:
```{code-block} toml
:caption: /etc/not-my-board/hub.toml
log_level = "info"

[auth]
issuer = "https://login.microsoftonline.com/common/v2.0"
client_id = "11111111-2222-1111-2222-000000000000"

[auth.issuers."https://login.microsoftonline.com/common/v2.0"]
show_claims = ["preferred_username", "oid", "iss"]

[auth.issuers."https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"]
show_claims = ["preferred_username", "oid", "iss"]

[[auth.permissions]]
claims.oid = "11111111-2222-1111-2222-333333333333"
claims.iss = "https://login.microsoftonline.com/9188040d-6c67-4c5b-b112-36a304b66dad/v2.0"
roles = ["exporter", "importer"]
```
