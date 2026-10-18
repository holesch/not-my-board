"""Shell completion helpers for not-my-board CLI."""

_PREFIX = "_shtab_not_my_board_complete"


def _complete(kind):
    return {
        "bash": f"{_PREFIX}_{kind}_bash",
        "zsh": f"{_PREFIX}_{kind}_zsh",
    }


def import_descriptions():
    """Completion for import description names and paths."""
    return _complete("import_descriptions_or_files")


def import_descriptions_with_places():
    """Completion for import description names and paths with @place_name."""
    return _complete("import_descriptions_with_places_or_files")


def reservations():
    """Completion for names of reserved places."""
    return _complete("reservations")


def reservations_or_import_descriptions():
    """Completion for reserved places, import description names and paths."""
    return _complete("reservations_or_import_descriptions_or_files")


def reservations_or_places():
    """Completion for reserved places and @place_name."""
    return _complete("reservations_or_places")


def get_shell_preambles():
    """Return shell-specific preambles for both bash and zsh."""
    return {
        "bash": _SHARED_PREAMBLE + _BASH_PREAMBLE,
        "zsh": _SHARED_PREAMBLE + _ZSH_PREAMBLE,
    }


_SHARED_PREAMBLE = r"""
_shtab_not_my_board_list_names() {
    # `list` prints "<name>@<place_name>  <status>"
    not-my-board list -n 2>/dev/null | awk '{ sub(/@.*/, "", $1); print $1 }'
}
"""

# shtab runs the Bash completers in a subshell and reads candidates from their
# stdout, so they can't modify COMPREPLY or call compopt. shtab enables the
# "filenames" option for completers with "_files" in their name.
_BASH_PREAMBLE = r"""
# Print the lines from stdin, that start with $1, prefixed with $2. Unlike
# compgen -W, this doesn't expand the candidates, which might come from
# untrusted sources.
_shtab_not_my_board_filter_bash() {
    local line
    while IFS= read -r line; do
        [[ "$line" == "$1"* ]] && printf '%s\n' "$2$line"
    done
    return 0
}

# Print "<import_description>@<place_name>" candidates. Returns 1 if the
# current word has no "@", so that the caller can complete something else.
_shtab_not_my_board_print_places_bash() {
    local cur="$1"
    [[ "$cur" == *@* ]] || return 1
    local desc="${cur%%@*}" places
    if [[ -n "$desc" ]]; then
        places=$(not-my-board search "$desc" 2>/dev/null)
    else
        places=$(not-my-board search 2>/dev/null)
    fi
    _shtab_not_my_board_filter_bash "@${cur#*@}" "$desc" <<< "$places"
}

_shtab_not_my_board_print_names_bash() {
    _shtab_not_my_board_list_names | _shtab_not_my_board_filter_bash "$1"
}

_shtab_not_my_board_print_import_descriptions_bash() {
    not-my-board imports 2>/dev/null | _shtab_not_my_board_filter_bash "$1"
    # without a "/" the argument is treated as a name, so prefix files in
    # the current directory with "./"
    if [[ "$1" == */* ]]; then
        compgen -f -- "$1"
    else
        compgen -f -- "./$1"
    fi
}

_shtab_not_my_board_complete_reservations_bash() {
    _shtab_not_my_board_print_names_bash "$1"
}

_shtab_not_my_board_complete_reservations_or_places_bash() {
    if [[ "$1" == @* ]]; then
        _shtab_not_my_board_print_places_bash "$1"
    else
        _shtab_not_my_board_print_names_bash "$1"
    fi
}

_shtab_not_my_board_complete_import_descriptions_or_files_bash() {
    _shtab_not_my_board_print_import_descriptions_bash "$1"
}

_shtab_not_my_board_complete_import_descriptions_with_places_or_files_bash() {
    _shtab_not_my_board_print_places_bash "$1" || \
        _shtab_not_my_board_print_import_descriptions_bash "$1"
}

_shtab_not_my_board_complete_reservations_or_import_descriptions_or_files_bash() {
    _shtab_not_my_board_print_places_bash "$1" && return
    _shtab_not_my_board_print_names_bash "$1"
    _shtab_not_my_board_print_import_descriptions_bash "$1"
}
"""

_ZSH_PREAMBLE = r"""
# Complete "<import_description>@<place_name>". Returns 1 if the current word
# has no "@", so that the caller can complete something else.
_shtab_not_my_board_complete_places_zsh() {
    compset -P '*@' || return 1
    local desc="${IPREFIX%@}"
    local -a places
    if [[ -n "$desc" ]]; then
        places=(${(f)"$(not-my-board search "$desc" 2>/dev/null)"})
    else
        places=(${(f)"$(not-my-board search 2>/dev/null)"})
    fi
    places=(${places#@})
    compadd -a places
}

# Without a "/" the argument is treated as a name, so complete files in the
# current directory with a "./" prefix.
_shtab_not_my_board_files_zsh() {
    if [[ $PREFIX == */* || $PREFIX == \~* ]]; then
        _files
    else
        _files -P ./ -W "$PWD"
    fi
}

_shtab_not_my_board_complete_reservations_zsh() {
    local -a names=(${(f)"$(_shtab_not_my_board_list_names)"})
    _describe 'place' names
}

_shtab_not_my_board_complete_reservations_or_places_zsh() {
    if [[ $PREFIX == @* ]]; then
        _shtab_not_my_board_complete_places_zsh
    else
        _shtab_not_my_board_complete_reservations_zsh
    fi
}

_shtab_not_my_board_complete_import_descriptions_or_files_zsh() {
    local -a imports=(${(f)"$(not-my-board imports 2>/dev/null)"})
    _alternative \
        'imports:import description:compadd -a imports' \
        'files:file:_shtab_not_my_board_files_zsh'
}

_shtab_not_my_board_complete_import_descriptions_with_places_or_files_zsh() {
    _shtab_not_my_board_complete_places_zsh && return
    _shtab_not_my_board_complete_import_descriptions_or_files_zsh
}

_shtab_not_my_board_complete_reservations_or_import_descriptions_or_files_zsh() {
    _shtab_not_my_board_complete_places_zsh && return
    local -a imports=(${(f)"$(not-my-board imports 2>/dev/null)"})
    local -a reserved=(${(f)"$(_shtab_not_my_board_list_names)"})
    _alternative \
        'imports:import description:compadd -a imports' \
        'reserved:reserved place:compadd -a reserved' \
        'files:file:_shtab_not_my_board_files_zsh'
}
"""
