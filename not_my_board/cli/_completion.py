"""Shell completion helpers for not-my-board CLI."""


def import_descriptions():
    """Return shtab-compatible completion dict for import descriptions."""
    return {
        "bash": "_shtab_not_my_board_complete_import_descriptions_or_files_bash",
        "zsh": "_shtab_not_my_board_complete_import_descriptions_or_files_zsh",
    }


def get_shell_preambles():
    """Return shell-specific preambles for both bash and zsh."""
    return {"bash": _BASH_PREAMBLE, "zsh": _ZSH_PREAMBLE}


# shtab runs the Bash completers in a subshell and reads candidates from their
# stdout, so they can't modify COMPREPLY or call compopt. shtab enables the
# "filenames" option for completers with "_files" in their name.
_BASH_PREAMBLE = r"""
# Print the lines from stdin, that start with $1. Unlike compgen -W, this
# doesn't expand the candidates, which might come from untrusted sources.
_shtab_not_my_board_filter_bash() {
    local line
    while IFS= read -r line; do
        [[ "$line" == "$1"* ]] && printf '%s\n' "$line"
    done
    return 0
}

_shtab_not_my_board_complete_import_descriptions_or_files_bash() {
    not-my-board imports 2>/dev/null | _shtab_not_my_board_filter_bash "$1"
    # without a "/" the argument is treated as a name, so prefix files in
    # the current directory with "./"
    if [[ "$1" == */* ]]; then
        compgen -f -- "$1"
    else
        compgen -f -- "./$1"
    fi
}
"""

_ZSH_PREAMBLE = r"""
# Without a "/" the argument is treated as a name, so complete files in the
# current directory with a "./" prefix.
_shtab_not_my_board_files_zsh() {
    if [[ $PREFIX == */* || $PREFIX == \~* ]]; then
        _files
    else
        _files -P ./ -W "$PWD"
    fi
}

_shtab_not_my_board_complete_import_descriptions_or_files_zsh() {
    local -a names=(${(f)"$(not-my-board imports 2>/dev/null)"})
    _alternative \
        'names:import description:compadd -a names' \
        'files:file:_shtab_not_my_board_files_zsh'
}
"""
