# How to Attach Places in CI

This guide shows you how to use `not-my-board` in Continuous Integration (CI) to
attach *Places* from the board farm.

## Install USB Device Drivers

Most images targeting VMs don't include USB device drivers. On Ubuntu based
images you can add the missing Kernel modules with:

```{code-block} console
# apt install "linux-modules-extra-$(uname -r)"
```

## Authentication

Since OpenID Connect is interactive, you can't use it in CI. Instead, let the CI
system generate an ID token for your job.

For example with GitHub Actions, add permissions to the *Hub* config:
```{code-block} toml
:caption: /etc/not-my-board/hub.toml

[...]

[[auth.permissions]]
claims.actor_id = "8659229"  # holesch
claims.repository = "holesch/not-my-board"
claims.workflow = "on-push"
claims.iss = "https://token.actions.githubusercontent.com"
roles = ["importer"]
```

Then add the `id-token` permission to your job:

```{code-block} yaml
:caption: .github/workflows/example.yml

jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      id-token: write
```

And let the *Agent* generate ID tokens with the GitHub Actions API:

```{code-block} console
# token_cmd="curl -sH 'Authorization: bearer  $ACTIONS_ID_TOKEN_REQUEST_TOKEN' '$ACTIONS_ID_TOKEN_REQUEST_URL&audience=\$client_id' | jq -r '.value'"
# not-my-board agent --token-cmd "$token_cmd" https://hub.example.com &
```

If you're using Jenkins, you can have a similar setup with the [OpenID Connect
Provider Plugin](https://plugins.jenkins.io/oidc-provider/).

With the *Agent* running in the background, you should then be able to attach
*Places* from the board farm as usual with `not-my-board attach <name>`.
