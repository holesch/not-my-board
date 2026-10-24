"""Shell completion helpers for not-my-board CLI."""


def import_descriptions():
    """Return shtab-compatible completion dict for import descriptions."""
    return {
        "bash": "_shtab_not_my_board_complete_import_descriptions_bash",
        "zsh": "_shtab_not_my_board_complete_import_descriptions_zsh",
    }


def get_shell_preambles():
    """Return shell-specific preambles for both bash and zsh."""
    common = _get_common_preamble()
    return {
        "bash": common + _get_bash_preamble(),
        "zsh": common + _get_zsh_preamble(),
    }


def _get_common_preamble():
    """Return shell function to find import descriptions (bash/zsh compatible).

    Finds .toml files in .not-my-board/ directories and XDG_CONFIG_HOME.
    May output duplicates; callers is responsible for deduplication.
    """
    return """
_shtab_not_my_board_find_import_descriptions() {
    local home="${HOME}"
    local config_home="${XDG_CONFIG_HOME:-$home/.config}"
    local path="$PWD"
    local dir

    _print_toml_files() {
        local file filename
        for file in "$1"/*.toml; do
            [ -f "$file" ] || continue
            filename="${file##*/}"
            printf '%s\n' "${filename%.toml}"
        done
    }

    # search .not-my-board directories from current dir up to home
    while true; do
        dir="$path/.not-my-board"
        [ -d "$dir" ] && _print_toml_files "$dir"

        [ "$path" != "$home" ] && [ "$path" != "/" ] || break

        path="${path%/*}"
        [ -n "$path" ] || path="/"
    done

    # search XDG_CONFIG_HOME/not-my-board
    dir="$config_home/not-my-board"
    [ -d "$dir" ] && _print_toml_files "$dir"
}
"""


def _get_bash_preamble():
    """Return bash-specific completion wrapper."""
    return """
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
    _shtab_not_my_board_find_import_descriptions | sort -u |
        _shtab_not_my_board_filter_bash "${COMP_WORDS[COMP_CWORD]}"
}
"""


def _get_zsh_preamble():
    """Return zsh-specific completion wrapper."""
    return """
_shtab_not_my_board_complete_import_descriptions_zsh() {
    local -a names=(${(f)"$(_shtab_not_my_board_find_import_descriptions | sort -u)"})
    _describe 'import descriptions' names
}
"""
