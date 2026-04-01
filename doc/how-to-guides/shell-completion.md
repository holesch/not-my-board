# How to Use Shell Completion

`not-my-board` supports shell completion for bash and zsh, which helps you:

- Discover available subcommands
- Complete import description names from your configuration
- Complete file paths for arguments like `--cacert`

## Bash

Generate and install the completion script:

```console
$ not-my-board --print-completion bash | sudo tee /usr/share/bash-completion/completions/not-my-board
```

Then restart your shell or source the completion script:

```console
$ source /usr/share/bash-completion/completions/not-my-board
```

## Zsh

Generate and install the completion script:

```console
$ not-my-board --print-completion zsh | sudo tee /usr/share/zsh/site-functions/_not-my-board
```

Then restart your shell or reload completions:

```console
$ autoload -U compinit && compinit
```
