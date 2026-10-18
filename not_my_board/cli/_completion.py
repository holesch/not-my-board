"""Shell completion helpers for not-my-board CLI."""


def import_descriptions():
    """Return shtab-compatible completion dict for import descriptions."""
    return {
        "bash": "_shtab_not_my_board_complete_import_descriptions_bash",
        "zsh": "_shtab_not_my_board_complete_import_descriptions_zsh",
    }


def get_shell_preambles():
    """Return shell-specific preambles for both bash and zsh."""
    return {"bash": _BASH_PREAMBLE, "zsh": _ZSH_PREAMBLE}


_BASH_PREAMBLE = """
# Print the lines from stdin, that start with $1. Unlike compgen -W, this
# doesn't expand the candidates, which might come from untrusted sources.
_shtab_not_my_board_filter_bash() {
    local line
    while IFS= read -r line; do
        [[ "$line" == "$1"* ]] && printf '%s\\n' "$line"
    done
    return 0
}

_shtab_not_my_board_complete_import_descriptions_bash() {
    not-my-board imports 2>/dev/null |
        _shtab_not_my_board_filter_bash "${COMP_WORDS[COMP_CWORD]}"
}
"""

_ZSH_PREAMBLE = """
_shtab_not_my_board_complete_import_descriptions_zsh() {
    local -a names=(${(f)"$(not-my-board imports 2>/dev/null)"})
    _describe 'import descriptions' names
}
"""
