# Shared POSIX helpers for launch.sh and tests. Not executed directly.
# shellcheck shell=sh

helper_known_key() {
    case $1 in
    HELPER_AGENT | HELPER_MODEL | HELPER_EFFORT | HELPER_CWD | HELPER_SPAWN_KIND | HELPER_PERMISSION | HELPER_EXTRA_ARGS)
        return 0
        ;;
    *)
        return 1
        ;;
    esac
}

helper_unquote() {
    # Sets _helper_unquoted. Rejects shell metacharacters even inside quotes.
    _helper_raw=$1
    case $_helper_raw in
    \"*\")
        _helper_unquoted=${_helper_raw#\"}
        _helper_unquoted=${_helper_unquoted%\"}
        ;;
    \'*\')
        _helper_unquoted=${_helper_raw#\'}
        _helper_unquoted=${_helper_unquoted%\'}
        ;;
    *)
        _helper_unquoted=$_helper_raw
        ;;
    esac
    case $_helper_unquoted in
    *[\$\`\;\|\&\<\>\(\)\{\}]*)
        return 1
        ;;
    esac
    return 0
}

helper_parse_conf() {
    # Reads KEY=value lines into the current shell. Unknown keys / unsafe
    # values fail. Does not source the file.
    _helper_conf=$1
    _helper_line=
    while IFS= read -r _helper_line || [ -n "$_helper_line" ]; do
        case $_helper_line in
        '' | \#*) continue ;;
        esac
        case $_helper_line in
        *=*) ;;
        *)
            printf '%s\n' "invalid config line: $_helper_line" >&2
            return 1
            ;;
        esac
        _helper_key=${_helper_line%%=*}
        _helper_val=${_helper_line#*=}
        helper_known_key "$_helper_key" || {
            printf '%s\n' "unknown config key: $_helper_key" >&2
            return 1
        }
        helper_unquote "$_helper_val" || {
            printf '%s\n' "unsafe value for $_helper_key" >&2
            return 1
        }
        case $_helper_key in
        HELPER_AGENT) HELPER_AGENT=$_helper_unquoted ;;
        HELPER_MODEL) HELPER_MODEL=$_helper_unquoted ;;
        HELPER_EFFORT) HELPER_EFFORT=$_helper_unquoted ;;
        HELPER_CWD) HELPER_CWD=$_helper_unquoted ;;
        HELPER_SPAWN_KIND) HELPER_SPAWN_KIND=$_helper_unquoted ;;
        HELPER_PERMISSION) HELPER_PERMISSION=$_helper_unquoted ;;
        HELPER_EXTRA_ARGS) HELPER_EXTRA_ARGS=$_helper_unquoted ;;
        esac
    done <"$_helper_conf"
    return 0
}

helper_prepend_path() {
    _helper_dir=$1
    [ -d "$_helper_dir" ] || return 0
    case :$PATH: in
    *:"$_helper_dir":*) return 0 ;;
    esac
    PATH="$_helper_dir${PATH:+:$PATH}"
    export PATH
}

helper_extend_user_path() {
    helper_prepend_path /usr/local/bin
    helper_prepend_path /opt/homebrew/bin
    helper_prepend_path "$HOME/bin"
    helper_prepend_path "$HOME/.local/bin"
    helper_prepend_path "$HOME/.grok/bin"
}

helper_detect_agent() {
    _helper_cand=
    for _helper_cand in agent devin claude codex grok; do
        if command -v "$_helper_cand" >/dev/null 2>&1; then
            printf '%s' "$_helper_cand"
            return 0
        fi
    done
    return 1
}

helper_expand_tilde() {
    _helper_path=$1
    case $_helper_path in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s' "$HOME/${_helper_path#\~/}" ;;
    *) printf '%s' "$_helper_path" ;;
    esac
}

helper_normalize_root() {
    # Expand ~ and drop a trailing slash so "$HOME/" still counts as home.
    _helper_root=$(helper_expand_tilde "$1")
    case $_helper_root in
    /) ;;
    */) _helper_root=${_helper_root%/} ;;
    esac
    printf '%s' "$_helper_root"
}

helper_is_inspect() {
    # True when this herdr argv is read-only inspect.
    case ${1:-} in
    --help | -h | help | -V | --version) return 0 ;;
    status) return 0 ;;
    esac
    case "${1:-} ${2:-}" in
    "agent list" | "agent read" | "agent get" | "agent wait" | "agent explain")
        return 0
        ;;
    "workspace list" | "workspace get")
        return 0
        ;;
    "worktree list")
        return 0
        ;;
    "plugin list" | "plugin log" | "plugin logs" | "plugin config-dir")
        return 0
        ;;
    esac
    return 1
}

helper_argv_has_flag() {
    _helper_flag=$1
    shift
    while [ $# -gt 0 ]; do
        [ "$1" = "$_helper_flag" ] && return 0
        shift
    done
    return 1
}

helper_is_agent_prompt() {
    [ "${1:-}" = agent ] && [ "${2:-}" = prompt ] && [ -n "${3:-}" ] && [ -n "${4:-}" ]
}

helper_relay_agent_prompt() {
    # Submit through Herdr with --wait, then Enter + wait if the pane never
    # leaves idle (common when Cursor keeps text in the follow-up field).
    _helper_real=$1
    shift
    _helper_target=$3
    _helper_text=$4
    shift 4
    _helper_extra=
    if ! helper_argv_has_flag --wait "$@"; then
        _helper_extra="--wait --timeout 120000"
    elif ! helper_argv_has_flag --timeout "$@"; then
        _helper_extra="--timeout 120000"
    fi
    # shellcheck disable=SC2086
    if "$_helper_real" agent prompt "$_helper_target" "$_helper_text" "$@" $_helper_extra; then
        return 0
    fi
    "$_helper_real" agent send-keys "$_helper_target" Enter || return $?
    "$_helper_real" agent wait "$_helper_target" --timeout 120000
}

helper_json_value() {
    # Print the first string value for a JSON key read from stdin.
    # Splits on JSON punctuation first so the match cannot run past the
    # field it belongs to. Only for flat string fields.
    _helper_json_key=$1
    tr '{},' '\n\n\n' |
        sed -n "s/.*\"$_helper_json_key\":\"\([^\"]*\)\".*/\1/p" |
        sed -n '1p'
}

helper_workspace_id_by_label() {
    # $1 real herdr, $2 label. Prints the first workspace id with that label.
    # Objects in `workspace list` are flat, so one '{' fragment is one
    # workspace and key order does not matter.
    "$1" workspace list 2>/dev/null |
        tr '{' '\n' |
        grep -F "\"label\":\"$2\"," |
        sed -n 's/.*"workspace_id":"\([^"]*\)".*/\1/p' |
        sed -n '1p'
}

helper_workspace_label() {
    # $1 real herdr, $2 workspace id. Prints the label of that workspace.
    # Fails when the id is empty or gone.
    [ -n "${2:-}" ] || return 1
    _helper_ws_json=$("$1" workspace get "$2" 2>/dev/null) || return 1
    printf '%s' "$_helper_ws_json" | helper_json_value label
}

helper_lantern_pane_workspace() {
    # $1 real herdr, $2 pane id, $3 pane title. Prints the workspace that
    # pane sits in, but only while it is still a live lantern chat. Herdr
    # reuses pane ids after a restart, so the title is checked as well.
    [ -n "${2:-}" ] || return 1
    _helper_pane_json=$("$1" pane get "$2" 2>/dev/null) || return 1
    case $_helper_pane_json in
    *"\"label\":\"$3\""*) ;;
    *) return 1 ;;
    esac
    printf '%s' "$_helper_pane_json" | helper_json_value workspace_id
}

helper_resolve_real_herdr() {
    if [ -n "${HERDR_REAL:-}" ] && [ -x "$HERDR_REAL" ]; then
        printf '%s' "$HERDR_REAL"
        return 0
    fi
    _helper_self_dir=$1
    _helper_cand=${HERDR_BIN_PATH:-}
    if [ -n "$_helper_cand" ] && [ -x "$_helper_cand" ]; then
        case $_helper_cand in
        "$_helper_self_dir"/herdr) ;;
        *)
            printf '%s' "$_helper_cand"
            return 0
            ;;
        esac
    fi
    _helper_old=$PATH
    _helper_new=
    _helper_part=
    IFS=:
    for _helper_part in $_helper_old; do
        [ "$_helper_part" = "$_helper_self_dir" ] && continue
        if [ -n "$_helper_new" ]; then
            _helper_new="$_helper_new:$_helper_part"
        else
            _helper_new=$_helper_part
        fi
    done
    unset IFS
    PATH=$_helper_new
    _helper_found=$(command -v herdr 2>/dev/null) || _helper_found=
    PATH=$_helper_old
    [ -n "$_helper_found" ] || return 1
    printf '%s' "$_helper_found"
}
