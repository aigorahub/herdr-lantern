# Shared POSIX helpers for launch.sh and tests. Not executed directly.
# shellcheck shell=sh

helper_known_key() {
    case $1 in
    HELPER_AGENT | HELPER_PROVIDER | HELPER_MODEL | HELPER_EFFORT | HELPER_CWD | HELPER_SPAWN_KIND | HELPER_SPAWN_MODEL | HELPER_SPAWN_EFFORT | HELPER_PERMISSION | HELPER_EXTRA_ARGS)
        return 0
        ;;
    *)
        return 1
        ;;
    esac
}

helper_conf_value_ok() {
    # Values are serialized as KEY="value". Reject shell metacharacters,
    # quotes, and line breaks so a model phrase cannot inject another key.
    # Do not use $(printf '\n'): command substitution strips the newline
    # and the match then succeeds for every value.
    _helper_cv=$1
    case $_helper_cv in
    *[\$\`\;\|\&\<\>\(\)\{\}\"\']*)
        return 1
        ;;
    esac
    _helper_cv_flat=$(printf '%s' "$_helper_cv" | tr -d '\n\r')
    [ "$_helper_cv_flat" = "$_helper_cv" ]
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
    helper_conf_value_ok "$_helper_unquoted"
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
        HELPER_PROVIDER) HELPER_PROVIDER=$_helper_unquoted ;;
        HELPER_MODEL) HELPER_MODEL=$_helper_unquoted ;;
        HELPER_EFFORT) HELPER_EFFORT=$_helper_unquoted ;;
        HELPER_CWD) HELPER_CWD=$_helper_unquoted ;;
        HELPER_SPAWN_KIND) HELPER_SPAWN_KIND=$_helper_unquoted ;;
        HELPER_SPAWN_MODEL) HELPER_SPAWN_MODEL=$_helper_unquoted ;;
        HELPER_SPAWN_EFFORT) HELPER_SPAWN_EFFORT=$_helper_unquoted ;;
        HELPER_PERMISSION) HELPER_PERMISSION=$_helper_unquoted ;;
        HELPER_EXTRA_ARGS) HELPER_EXTRA_ARGS=$_helper_unquoted ;;
        esac
    done <"$_helper_conf"
    return 0
}

helper_conf_set() {
    # $1 file, $2 key, $3 unquoted value. Replaces the key’s line or
    # appends it. Preserves comments and other keys. Never sources.
    _helper_set_file=$1
    _helper_set_key=$2
    _helper_set_val=$3
    helper_known_key "$_helper_set_key" || {
        printf '%s\n' "unknown config key: $_helper_set_key" >&2
        return 1
    }
    helper_conf_value_ok "$_helper_set_val" || {
        printf '%s\n' "unsafe value for $_helper_set_key" >&2
        return 1
    }
    _helper_set_line="${_helper_set_key}=\"${_helper_set_val}\""
    _helper_set_dir=$(dirname "$_helper_set_file")
    _helper_set_tmp=$(mktemp "${_helper_set_dir}/.helper.conf.XXXXXX") || return 1
    _helper_set_found=0
    if [ -f "$_helper_set_file" ]; then
        while IFS= read -r _helper_set_old || [ -n "$_helper_set_old" ]; do
            case $_helper_set_old in
            "${_helper_set_key}="*)
                printf '%s\n' "$_helper_set_line"
                _helper_set_found=1
                ;;
            *)
                printf '%s\n' "$_helper_set_old"
                ;;
            esac
        done <"$_helper_set_file" >"$_helper_set_tmp" || {
            rm -f "$_helper_set_tmp"
            return 1
        }
    else
        : >"$_helper_set_tmp" || {
            rm -f "$_helper_set_tmp"
            return 1
        }
    fi
    if [ "$_helper_set_found" = 0 ]; then
        printf '%s\n' "$_helper_set_line" >>"$_helper_set_tmp" || {
            rm -f "$_helper_set_tmp"
            return 1
        }
    fi
    mv "$_helper_set_tmp" "$_helper_set_file" || {
        rm -f "$_helper_set_tmp"
        return 1
    }
}

helper_conf_set_keys() {
    # $1 file, then repeating KEY value. One rewrite: write to a temp
    # copy, parse it, then replace the original. Does not source.
    _helper_sk_file=$1
    shift
    _helper_sk_dir=$(dirname "$_helper_sk_file")
    _helper_sk_tmp=$(mktemp "${_helper_sk_dir}/.helper.conf.XXXXXX") || return 1
    if [ -f "$_helper_sk_file" ]; then
        cp "$_helper_sk_file" "$_helper_sk_tmp" || {
            rm -f "$_helper_sk_tmp"
            return 1
        }
    else
        : >"$_helper_sk_tmp" || {
            rm -f "$_helper_sk_tmp"
            return 1
        }
    fi
    while [ $# -ge 2 ]; do
        helper_conf_set "$_helper_sk_tmp" "$1" "$2" || {
            rm -f "$_helper_sk_tmp"
            return 1
        }
        shift 2
    done
    if [ $# -ne 0 ]; then
        rm -f "$_helper_sk_tmp"
        return 1
    fi
    helper_parse_conf "$_helper_sk_tmp" || {
        rm -f "$_helper_sk_tmp"
        return 1
    }
    mv "$_helper_sk_tmp" "$_helper_sk_file" || {
        rm -f "$_helper_sk_tmp"
        return 1
    }
}

helper_spawn_summary() {
    # Prints the user’s spawn default from the current shell vars.
    _helper_ss_kind=${HELPER_SPAWN_KIND:-claude}
    _helper_ss_model=${HELPER_SPAWN_MODEL:-}
    _helper_ss_effort=${HELPER_SPAWN_EFFORT:-}
    if [ -n "$_helper_ss_model" ] && [ -n "$_helper_ss_effort" ]; then
        printf '%s · %s · %s' "$_helper_ss_kind" "$_helper_ss_model" "$_helper_ss_effort"
    elif [ -n "$_helper_ss_model" ]; then
        printf '%s · %s' "$_helper_ss_kind" "$_helper_ss_model"
    else
        printf '%s · live default' "$_helper_ss_kind"
    fi
}

helper_onboard_needed() {
    # $1 state_dir. Prints 1 when the helper should interview for a
    # spawn default. One marker is the whole gate: a first open that
    # seeded helper.conf and then died must still interview next time.
    _helper_ob_state=$1
    mkdir -p "$_helper_ob_state" || return 1
    if [ -f "$_helper_ob_state/onboarded" ]; then
        printf '0'
        return 0
    fi
    printf '1'
    return 0
}

helper_onboard_mark() {
    _helper_ob_state=$1
    mkdir -p "$_helper_ob_state" || return 1
    printf 'user\n' >"$_helper_ob_state/onboarded"
}

helper_list_helpers() {
    # Prints the helper CLIs on PATH, space-separated, or nothing.
    _helper_list=
    _helper_cand=
    for _helper_cand in agent devin claude codex grok pi; do
        if command -v "$_helper_cand" >/dev/null 2>&1; then
            if [ -n "$_helper_list" ]; then
                _helper_list="$_helper_list $_helper_cand"
            else
                _helper_list=$_helper_cand
            fi
        fi
    done
    printf '%s' "$_helper_list"
}

helper_normalize_spawn_kind() {
    # $1 spoken or stored kind. Prints the herdr --kind token.
    _helper_nk=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
    case $_helper_nk in
    agent | cursor) printf 'cursor' ;;
    'grok build' | grok-build | supergrok) printf 'grok' ;;
    *) printf '%s' "$_helper_nk" ;;
    esac
}

helper_normalize_spawn_model() {
    # $1 kind, $2 spoken model. A product name for that kind means the
    # live default, not a resolver phrase.
    _helper_nm_kind=$1
    _helper_nm_model=$2
    _helper_nm_lc=$(printf '%s' "$_helper_nm_model" | tr '[:upper:]' '[:lower:]')
    case $_helper_nm_kind in
    grok)
        case $_helper_nm_lc in
        '' | grok | 'grok build' | grok-build | supergrok)
            printf ''
            return 0
            ;;
        esac
        ;;
    esac
    printf '%s' "$_helper_nm_model"
}

helper_plugin_id_listed() {
    # $1 plugin-list text, $2 plugin id. Exact token after splitting on
    # whitespace and common JSON punctuation. A substring or regex
    # wildcard must not count.
    _helper_pl_id=$2
    [ -n "$_helper_pl_id" ] || return 1
    printf '%s\n' "$1" | tr ',:"[]{}' ' ' | tr ' ' '\n' | grep -Fx "$_helper_pl_id" >/dev/null
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

helper_force_front_path() {
    # Put a directory at the very front of PATH, wherever it already sits.
    #
    # This is for the wrapper directory only, and it is a security control,
    # not a convenience. helper_prepend_path leaves a directory that is
    # already on PATH in its inherited position, and helper_extend_user_path
    # then puts /usr/local/bin and /opt/homebrew/bin ahead of it, so on a
    # machine whose PATH already carries the plugin's bin a bare `herdr`
    # reached the real binary and the mutate gate never ran.
    #
    # Every copy goes, so helper_resolve_real_herdr still finds the real
    # binary when it strips this directory back out.
    _helper_dir=$1
    [ -d "$_helper_dir" ] || return 0
    _helper_rest=
    _helper_part=
    IFS=:
    for _helper_part in $PATH; do
        [ "$_helper_part" = "$_helper_dir" ] && continue
        if [ -n "$_helper_rest" ]; then
            _helper_rest="$_helper_rest:$_helper_part"
        else
            _helper_rest=$_helper_part
        fi
    done
    unset IFS
    PATH="$_helper_dir${_helper_rest:+:$_helper_rest}"
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
    for _helper_cand in agent devin claude codex grok pi; do
        if command -v "$_helper_cand" >/dev/null 2>&1; then
            printf '%s' "$_helper_cand"
            return 0
        fi
    done
    return 1
}

helper_cursor_default_model() {
    # The one model default the plugin owns: Cursor agent with an empty
    # HELPER_MODEL. launch.sh builds the argv from this and the chat
    # identity names it, so it lives here once.
    printf 'cursor-grok-4.6-high-fast'
}

helper_chat_identity() {
    # $1 HELPER_AGENT, $2 HELPER_MODEL, $3 HELPER_EFFORT, $4 HELPER_PROVIDER
    # (Pi). Prints what the chat actually runs — "claude · opus · high" —
    # for the tab label and the light-up line. It mirrors launch.sh rather
    # than echoing the conf: Cursor agent's empty model means the documented
    # default, Devin's model lives in Devin's own config so none is claimed
    # for it, and effort only reaches the CLIs launch.sh passes it to.
    _helper_ci_agent=$1
    _helper_ci_model=${2:-}
    _helper_ci_effort=${3:-}
    _helper_ci_provider=${4:-}
    case $_helper_ci_agent in
    agent | cursor)
        _helper_ci='cursor agent'
        [ -n "$_helper_ci_model" ] ||
            _helper_ci_model=$(helper_cursor_default_model)
        ;;
    devin)
        _helper_ci=devin
        _helper_ci_model=
        ;;
    pi)
        _helper_ci=pi
        # Fold provider into the label only when a model is present and
        # not already provider/id. Provider with no model is not what Pi
        # names, and prefixing a qualified id would print
        # google/google/gemini-....
        case $_helper_ci_model in
        '' | */*) ;;
        *)
            if [ -n "$_helper_ci_provider" ]; then
                _helper_ci_model="$_helper_ci_provider/$_helper_ci_model"
            fi
            ;;
        esac
        ;;
    *) _helper_ci=$_helper_ci_agent ;;
    esac
    helper_agent_takes_effort "$_helper_ci_agent" || _helper_ci_effort=
    if [ -n "$_helper_ci_model" ]; then
        _helper_ci="$_helper_ci · $_helper_ci_model"
    fi
    if [ -n "$_helper_ci_effort" ]; then
        _helper_ci="$_helper_ci · $_helper_ci_effort"
    fi
    printf '%s' "$_helper_ci"
}

helper_detect_python() {
    # Prints a working Python 3 command, or fails.
    #
    # Windows keeps a zero-byte Microsoft Store alias named python3 on PATH.
    # It satisfies `command -v` and then opens the Store instead of running,
    # so a name on PATH proves nothing. A candidate is accepted only when the
    # file has content and the interpreter reports major version 3.
    #
    # Known limit: a shadowing stub hides a real python3 further along PATH.
    # The fallbacks below cover that on Windows, where the stub lives.
    for _helper_py in python3 python; do
        _helper_py_path=$(command -v "$_helper_py" 2>/dev/null) || continue
        [ -s "$_helper_py_path" ] || continue
        if "$_helper_py" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' \
            >/dev/null 2>&1; then
            printf '%s' "$_helper_py"
            return 0
        fi
    done
    # The Windows launcher picks the interpreter itself and is not a plain
    # file on PATH, so it gets its own check. Two words on purpose.
    if command -v py >/dev/null 2>&1; then
        if py -3 -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' \
            >/dev/null 2>&1; then
            printf '%s' 'py -3'
            return 0
        fi
    fi
    return 1
}

helper_posix_path() {
    # Prints a path that both this shell and the programs it starts can use.
    #
    # Herdr on Windows reports HERDR_PLUGIN_ROOT as an extended-length path,
    # \\?\C:\path. MSYS reads that happily, so every shell test passes, but
    # appending a child gives \\?\C:\path/bin/goals-floor, and Windows does not
    # accept a forward slash after the \\?\ prefix. Python answers OSError 22
    # and, because the snapshot sends stderr to /dev/null, the only symptom is
    # an empty file. MSYS converts an ordinary POSIX path correctly on the way
    # to a native program, so convert once here and hand that form on.
    #
    # Identity wherever cygpath does not exist.
    if command -v cygpath >/dev/null 2>&1; then
        if _helper_posix=$(cygpath -u "$1" 2>/dev/null) && [ -n "$_helper_posix" ]; then
            printf '%s' "$_helper_posix"
            return 0
        fi
    fi
    printf '%s' "$1"
}

helper_native_path() {
    # Prints a path in the form the native herdr binary expects.
    #
    # Under Git Bash $HOME is /c/Users/name and herdr.exe wants C:\Users\name.
    # MSYS converts most arguments on its way to a native program, but that is
    # a heuristic on the argument text, and --cwd decides where a workspace is
    # created. Convert it here instead of hoping.
    #
    # Identity wherever cygpath does not exist, which is everywhere but
    # Windows, so macOS and Linux are untouched.
    if command -v cygpath >/dev/null 2>&1; then
        if _helper_native=$(cygpath -w "$1" 2>/dev/null) && [ -n "$_helper_native" ]; then
            printf '%s' "$_helper_native"
            return 0
        fi
    fi
    printf '%s' "$1"
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

helper_manifest_version() {
    # Prints the version string from a herdr-plugin.toml read on stdin.
    # Fails when there is none. One extraction for the local file and the
    # fetched manifest alike, so the line format lives in exactly one place.
    _helper_mv=$(sed -n 's/^version = "\(.*\)"$/\1/p' | sed -n '1p')
    [ -n "$_helper_mv" ] || return 1
    printf '%s' "$_helper_mv"
}

helper_agent_takes_effort() {
    # True for the CLIs launch.sh passes an effort flag to. The identity
    # string consults the same membership, so the label can never claim an
    # effort the CLI was not given.
    case $1 in
    claude | codex | grok | pi) return 0 ;;
    *) return 1 ;;
    esac
}

helper_effective_flag() {
    # $1 flag name without dashes, $2 conf value, $3 HELPER_EXTRA_ARGS.
    # Prints the value that will actually run: launch.sh appends the extra
    # args after its own flags, a later flag wins in these CLIs, so a
    # --<flag> there overrides the conf field, and the identity has to
    # follow the winner.
    _helper_ef_flag=$1
    _helper_ef=$2
    _helper_ef_prev=
    set -f
    for _helper_ef_tok in $3; do
        if [ "$_helper_ef_prev" = "--$_helper_ef_flag" ]; then
            _helper_ef=$_helper_ef_tok
        fi
        case $_helper_ef_tok in
        --$_helper_ef_flag=*) _helper_ef=${_helper_ef_tok#--$_helper_ef_flag=} ;;
        esac
        _helper_ef_prev=$_helper_ef_tok
    done
    set +f
    printf '%s' "$_helper_ef"
}

helper_effective_model() {
    helper_effective_flag model "$1" "$2"
}

helper_version_is_xyz() {
    # True when $1 is exactly three dot-separated numbers. The dot count
    # is checked as a shape first, because cut passes a line through whole
    # when its delimiter is absent — a bare "9" would otherwise read as
    # field one, two, and three.
    case $1 in
    *.*.*.*) return 1 ;;
    *.*.*) ;;
    *) return 1 ;;
    esac
    _helper_vx_i=1
    while [ "$_helper_vx_i" -le 3 ]; do
        case $(printf '%s' "$1" | cut -d. -f"$_helper_vx_i") in
        '' | *[!0-9]*) return 1 ;;
        esac
        _helper_vx_i=$((_helper_vx_i + 1))
    done
    return 0
}

helper_version_gt() {
    # True when $1 names a strictly newer x.y.z than $2. Each field compares
    # as a number, so 0.10.0 beats 0.9.9. Anything that is not exactly three
    # numbers is never newer — checked in full before any field compares, or
    # "0.9" would beat "0.8.0" on its second field with the missing third
    # never looked at: a manifest somebody is mid-edit on, or a truncated
    # fetch, must not read as an update.
    helper_version_is_xyz "$1" || return 1
    helper_version_is_xyz "$2" || return 1
    _helper_vg_i=1
    while [ "$_helper_vg_i" -le 3 ]; do
        _helper_vg_a=$(printf '%s' "$1" | cut -d. -f"$_helper_vg_i")
        _helper_vg_b=$(printf '%s' "$2" | cut -d. -f"$_helper_vg_i")
        [ "$_helper_vg_a" -gt "$_helper_vg_b" ] && return 0
        [ "$_helper_vg_a" -lt "$_helper_vg_b" ] && return 1
        _helper_vg_i=$((_helper_vg_i + 1))
    done
    return 1
}

helper_update_snapshot() {
    # $1 plugin root, $2 output file. One honest line about whether a newer
    # plugin version is published, for the update offer in prompt.md. The
    # fetch gets a few seconds and no more, a failure is written as what it
    # is rather than guessed around, and every branch writes the file, so
    # last light-up's answer can never be read as this one's.
    #
    # The line names which install this is. There is no `herdr plugin
    # update`: a GitHub install refreshes with `herdr plugin install`, and
    # running that over a linked checkout would orphan the link, so the
    # offer the lantern makes has to differ between the two.
    _helper_up_root=$1
    _helper_up_out=$2
    _helper_up_url=${LANTERN_UPDATE_URL:-https://raw.githubusercontent.com/aigorahub/herdr-lantern/main/herdr-plugin.toml}
    # Which install this is decides the offer, and the wrong answer is not
    # symmetrical: reinstalling over a link orphans it, while calling an
    # install a checkout only costs the shortcut. A .git proves nothing —
    # Herdr clones GitHub installs, so they carry one too. The reliable
    # signal is where Herdr keeps them: its own plugins/github directory.
    # Anything anywhere else gets the cautious answer. The root arrives in
    # POSIX form (launch.sh normalises it), so the slashes are forward.
    case $_helper_up_root in
    */plugins/github/*) _helper_up_kind="GitHub install" ;;
    *) _helper_up_kind="linked checkout" ;;
    esac
    if ! _helper_up_local=$(cat "$_helper_up_root/herdr-plugin.toml" 2>/dev/null |
        helper_manifest_version); then
        _helper_up_line="update check unavailable: this install's manifest has no version"
    elif ! command -v curl >/dev/null 2>&1; then
        _helper_up_line="update check unavailable: curl not found on PATH (this is v$_helper_up_local, $_helper_up_kind)"
    elif ! _helper_up_toml=$(curl -fsS --max-time 4 "$_helper_up_url" 2>/dev/null); then
        _helper_up_line="update check unavailable: could not fetch the published manifest (this is v$_helper_up_local, $_helper_up_kind)"
    else
        _helper_up_remote=$(printf '%s\n' "$_helper_up_toml" |
            helper_manifest_version) || _helper_up_remote=
        if [ -z "$_helper_up_remote" ]; then
            _helper_up_line="update check unavailable: the published manifest has no version (this is v$_helper_up_local, $_helper_up_kind)"
        elif helper_version_gt "$_helper_up_remote" "$_helper_up_local"; then
            _helper_up_line="update available: v$_helper_up_remote is published, this is v$_helper_up_local ($_helper_up_kind)"
        else
            _helper_up_line="up to date: v$_helper_up_local ($_helper_up_kind)"
        fi
    fi
    printf '%s\n' "$_helper_up_line" >"$_helper_up_out" 2>/dev/null || true
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
    "tab list" | "tab get")
        return 0
        ;;
    "pane list" | "pane current" | "pane get" | "pane layout" | \
        "pane process-info" | "pane neighbor" | "pane edges" | "pane read")
        return 0
        ;;
    "worktree list")
        return 0
        ;;
    "session list")
        return 0
        ;;
    "plugin list" | "plugin log" | "plugin logs" | "plugin config-dir")
        return 0
        ;;
    "integration status")
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

helper_argv_option_value() {
    # Print the value of --flag from an argv list. Stops at -- so agent
    # args cannot supply a fake kind or pane. Accepts --flag value and
    # --flag=value.
    _helper_opt=$1
    shift
    while [ $# -gt 0 ]; do
        [ "$1" = -- ] && return 1
        if [ "$1" = "$_helper_opt" ]; then
            [ -n "${2:-}" ] || return 1
            printf '%s' "$2"
            return 0
        fi
        case $1 in
        "$_helper_opt"=*)
            printf '%s' "${1#"$_helper_opt"=}"
            return 0
            ;;
        esac
        shift
    done
    return 1
}

helper_is_agent_prompt() {
    [ "${1:-}" = agent ] && [ "${2:-}" = prompt ] && [ -n "${3:-}" ] && [ -n "${4:-}" ]
}

helper_is_agent_start() {
    [ "${1:-}" = agent ] && [ "${2:-}" = start ] && [ -n "${3:-}" ] || return 1
    case $3 in
    -*) return 1 ;;
    esac
    return 0
}

helper_pane_holds_text() {
    # True when pane text ($2) is showing the start of message ($1).
    #
    # A short probe on purpose: a pane wraps, and the input field carries a
    # prefix, so anything longer is split across lines and never matches.
    _helper_probe=$(printf '%s\n' "$1" | sed -n '1p' | cut -c1-24)
    [ -n "$_helper_probe" ] || return 1
    printf '%s\n' "$2" | grep -qF -- "$_helper_probe"
}

helper_prompt_seat_is_ready() {
    # Older Herdr hook seats omit interactive_ready. Settled state still
    # permits a prompt; an explicit false value does not.
    printf '%s\n' "$1" | grep -qE '"agent_status"[[:space:]]*:[[:space:]]*"(idle|done)"' || return 1
    printf '%s\n' "$1" | grep -qE '"interactive_ready"[[:space:]]*:[[:space:]]*false' && return 1
    return 0
}

helper_pane_has_login_picker() {
    printf '%s\n' "$1" | tr '\n\r' '  ' | grep -qiE \
        'sign in|log in|login|choose.*account|select.*account|select.*login|authentication method'
}

helper_relay_agent_prompt() {
    # Submit through Herdr with --wait. The Enter fallback is for one failure
    # only: Cursor leaves the text in the follow-up field and the pane never
    # leaves idle, so the prompt times out with the message visible in the
    # pane. Any other failure - unknown target, herdr not running, a flag
    # herdr rejected - never puts it there, and Enter into one of those is a
    # keystroke nobody asked for typed into somebody else's session.
    _helper_real=$1
    shift
    _helper_target=$3
    _helper_text=$4
    shift 4
    _helper_got=$("$_helper_real" agent get "$_helper_target" 2>/dev/null) || return $?
    helper_prompt_seat_is_ready "$_helper_got" || {
        printf '%s\n' "lantern: $_helper_target is not ready; observe with agent get/read/wait" >&2
        return 2
    }
    _helper_extra=
    if ! helper_argv_has_flag --wait "$@"; then
        _helper_extra="--wait --timeout 120000"
    elif ! helper_argv_has_flag --timeout "$@"; then
        _helper_extra="--timeout 120000"
    fi
    # An `if` that takes neither branch answers 0, so the failing status is
    # kept off an AND-OR list instead.
    # shellcheck disable=SC2086
    "$_helper_real" agent prompt "$_helper_target" "$_helper_text" "$@" $_helper_extra &&
        return 0
    _helper_status=$?
    _helper_before=$("$_helper_real" agent read "$_helper_target" --lines 60 2>/dev/null) ||
        _helper_before=
    helper_pane_holds_text "$_helper_text" "$_helper_before" ||
        return $_helper_status
    helper_pane_has_login_picker "$_helper_before" && return "$_helper_status"
    _helper_got=$("$_helper_real" agent get "$_helper_target" 2>/dev/null) || return $?
    helper_prompt_seat_is_ready "$_helper_got" || return "$_helper_status"
    "$_helper_real" agent send-keys "$_helper_target" Enter || return $?
    "$_helper_real" agent wait "$_helper_target" --timeout 120000 || return $?
    # The wait proves nothing by itself: a pane that swallowed the Enter is
    # idle already, so it returns at once and the caller is told the message
    # went in. A pane that has not moved at all is the one answer that means
    # it did not.
    _helper_after=$("$_helper_real" agent read "$_helper_target" --lines 60 2>/dev/null) ||
        _helper_after=
    if [ "$_helper_after" = "$_helper_before" ]; then
        printf '%s\n' "lantern: Enter did not submit the message in $_helper_target" >&2
        return 1
    fi
    return 0
}

helper_codex_flat_pane() {
    printf '%s\n' "$1" | tr '\n\r' '  '
}

helper_codex_pane_is_later_prompt() {
    # True when the pane is a later permission prompt, not a first-run gate.
    _helper_flat=$(helper_codex_flat_pane "$1")
    case $_helper_flat in
    *"Allow command[?]"* | *"allow command[?]"* | \
        *"press enter to confirm or esc to cancel"*)
        return 0
        ;;
    esac
    return 1
}

helper_codex_pane_has_trust() {
    helper_pane_has_login_picker "$1" && return 1
    helper_codex_pane_is_later_prompt "$1" && return 1
    _helper_flat=$(helper_codex_flat_pane "$1")
    case $_helper_flat in
    *"Do you trust the contents of this directory"* | *"Yes, continue"*)
        return 0
        ;;
    esac
    return 1
}

helper_claude_pane_has_trust() {
    helper_pane_has_login_picker "$1" && return 1
    # True when pane text ($1) is Claude's first-run folder trust screen:
    # Accessing workspace, Yes, I trust this folder, Enter to confirm.
    # Later permission prompts are not a match, and neither is the Codex
    # directory-trust dialog, which is a different screen on a different
    # kind of seat.
    helper_codex_pane_is_later_prompt "$1" && return 1
    _helper_flat=$(helper_codex_flat_pane "$1")
    case $_helper_flat in
    *"trust this folder"*)
        return 0
        ;;
    esac
    return 1
}

helper_codex_pane_has_yn() {
    helper_pane_has_login_picker "$1" && return 1
    helper_codex_pane_is_later_prompt "$1" && return 1
    _helper_flat=$(helper_codex_flat_pane "$1")
    case $_helper_flat in
    *'[y/n]'* | *'yes (y)'* | *'Yes (y)'*) ;;
    *) return 1 ;;
    esac
    case $_helper_flat in
    *[Nn]ew\ chat* | *[Nn]ew-chat* | *[Nn]ew\ conversation* | \
        *[Rr]esume\ this\ session*)
        return 0
        ;;
    esac
    return 1
}

helper_codex_startup_key() {
    helper_pane_has_login_picker "$1" && return 1
    # Prints Enter or y when pane text ($1) is a Codex first-run gate.
    # Trust wins when both are visible on the first key. Later permission
    # prompts are not a match.
    helper_codex_pane_is_later_prompt "$1" && return 1
    helper_codex_pane_has_trust "$1" && {
        printf 'Enter'
        return 0
    }
    helper_codex_pane_has_yn "$1" && {
        printf 'y'
        return 0
    }
    return 1
}

helper_seat_is_ready() {
    # True when agent get JSON ($1) says the seat can take prompts. Both
    # kinds report the same fields, so one check serves both.
    # Unfocused new seats report done rather than idle, so both count.
    case $1 in
    *"\"interactive_ready\":true"*) ;;
    *) return 1 ;;
    esac
    case $1 in
    *"\"agent_status\":\"idle\""* | *"\"agent_status\":\"done\""*)
        return 0
        ;;
    esac
    return 1
}

helper_seat_ok() {
    # True when agent get JSON ($1) is still kind ($3) in pane ($2).
    _helper_got_pane=$(printf '%s' "$1" | helper_json_value pane_id)
    [ "$_helper_got_pane" = "$2" ] || return 1
    _helper_got_agent=$(printf '%s' "$1" | helper_json_value agent)
    [ "$_helper_got_agent" = "$3" ] || return 1
    return 0
}

helper_codex_seat_ok() {
    # True when agent get JSON ($1) is still the Codex seat in pane ($2).
    helper_seat_ok "$1" "$2" codex
}

helper_claude_startup_gate() {
    # Claude first-run folder trust. Only that screen, only the named
    # start pane, and only one Enter. $1 real herdr, $2 name, $3 pane,
    # $4 the start status to return when this is not that gate.
    _helper_real=$1
    _helper_name=$2
    _helper_pane=$3
    _helper_status=$4
    _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
        return "$_helper_status"
    helper_seat_ok "$_helper_got" "$_helper_pane" claude ||
        return "$_helper_status"
    _helper_miss=0
    while :; do
        _helper_before=$("$_helper_real" agent read "$_helper_name" --lines 60 2>/dev/null) ||
            _helper_before=
        helper_claude_pane_has_trust "$_helper_before" && break
        _helper_miss=$((_helper_miss + 1))
        [ "$_helper_miss" -ge 2 ] && return "$_helper_status"
        # Herdr already called it blocked; the dialog may still be
        # painting. --until idle times out while it stays blocked.
        "$_helper_real" agent wait "$_helper_name" --until idle --until done \
            --timeout 2000 >/dev/null 2>&1 || true
    done
    _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
        return "$_helper_status"
    helper_seat_ok "$_helper_got" "$_helper_pane" claude ||
        return "$_helper_status"
    "$_helper_real" agent send-keys "$_helper_name" Enter || return $?
    "$_helper_real" agent wait "$_helper_name" --until idle --until done \
        --timeout 120000 || return $?
    _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
        _helper_got=
    if helper_seat_ok "$_helper_got" "$_helper_pane" claude &&
        helper_seat_is_ready "$_helper_got"; then
        return 0
    fi
    printf '%s\n' "lantern: Claude in $_helper_name did not become ready after the folder trust gate" >&2
    return 1
}

helper_relay_agent_start() {
    # Codex and Claude first-run survival. Herdr returns agent_not_ready
    # as soon as detection reports blocked during startup, so a
    # first-run gate kills the seat. For Codex, trust and a new-chat
    # confirm can appear in sequence, and a [y/n] prompt may still need
    # Enter after y. For Claude, the only gate handled is the folder
    # trust screen, and the only key is one Enter. This stays on that
    # same named pane and sends only those keys. Any other failure,
    # another kind, or another agent's pane is left alone.
    _helper_real=$1
    shift
    _helper_name=$3
    _helper_kind=$(helper_argv_option_value --kind "$@") || _helper_kind=
    _helper_pane=$(helper_argv_option_value --pane "$@") || _helper_pane=
    # Codex seats must not stop for command or sandbox confirms. Put the
    # unattended flag immediately after -- so it stays a Codex global
    # option, before resume or review. Drop any copy already in the kind
    # args, including one after the subcommand, then insert exactly one.
    if [ "$_helper_kind" = codex ]; then
        _helper_flag=--dangerously-bypass-approvals-and-sandbox
        _helper_dd_at=0
        _helper_i=0
        for _helper_a in "$@"; do
            _helper_i=$((_helper_i + 1))
            if [ "$_helper_a" = -- ]; then
                _helper_dd_at=$_helper_i
                break
            fi
        done
        if [ "$_helper_dd_at" -eq 0 ]; then
            set -- "$@" -- "$_helper_flag"
        else
            _helper_prefix=$((_helper_dd_at - 1))
            _helper_kind_n=$(($# - _helper_dd_at))
            _helper_i=0
            while [ "$_helper_i" -lt "$_helper_prefix" ]; do
                _helper_a=$1
                shift
                set -- "$@" "$_helper_a"
                _helper_i=$((_helper_i + 1))
            done
            shift
            _helper_i=0
            while [ "$_helper_i" -lt "$_helper_kind_n" ]; do
                _helper_a=$1
                shift
                _helper_i=$((_helper_i + 1))
                if [ "$_helper_a" != "$_helper_flag" ]; then
                    set -- "$@" "$_helper_a"
                fi
            done
            _helper_i=0
            while [ "$_helper_i" -lt "$_helper_prefix" ]; do
                _helper_a=$1
                shift
                set -- "$@" "$_helper_a"
                _helper_i=$((_helper_i + 1))
            done
            set -- -- "$_helper_flag" "$@"
            _helper_keep=$(($# - _helper_prefix))
            _helper_i=0
            while [ "$_helper_i" -lt "$_helper_keep" ]; do
                _helper_a=$1
                shift
                set -- "$@" "$_helper_a"
                _helper_i=$((_helper_i + 1))
            done
        fi
    fi
    _helper_errf=$(mktemp) || {
        "$_helper_real" "$@"
        return $?
    }
    "$_helper_real" "$@" 2>"$_helper_errf"
    _helper_status=$?
    _helper_err=$(cat "$_helper_errf")
    cat "$_helper_errf" >&2
    rm -f "$_helper_errf"
    [ "$_helper_status" -eq 0 ] && return 0
    case $_helper_kind in
    codex | claude) ;;
    *) return $_helper_status ;;
    esac
    [ -n "$_helper_name" ] && [ -n "$_helper_pane" ] || return $_helper_status
    case $_helper_err in
    *agent_not_ready* | *"blocked during startup"*) ;;
    *) return $_helper_status ;;
    esac
    if [ "$_helper_kind" = claude ]; then
        helper_claude_startup_gate "$_helper_real" "$_helper_name" \
            "$_helper_pane" "$_helper_status"
        return $?
    fi
    _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
        return $_helper_status
    helper_codex_seat_ok "$_helper_got" "$_helper_pane" || return $_helper_status
    _helper_sent=0
    _helper_miss=0
    _helper_post_miss=0
    _helper_did_trust=0
    _helper_did_y=0
    _helper_did_yn_enter=0
    while [ "$_helper_sent" -lt 3 ]; do
        _helper_before=$("$_helper_real" agent read "$_helper_name" --lines 60 2>/dev/null) ||
            _helper_before=
        if helper_codex_pane_is_later_prompt "$_helper_before"; then
            [ "$_helper_sent" -gt 0 ] && break
            return $_helper_status
        fi
        _helper_key=
        if helper_codex_pane_has_yn "$_helper_before" &&
            [ "$_helper_did_y" -eq 1 ] && [ "$_helper_did_yn_enter" -eq 0 ]; then
            _helper_key=Enter
        elif helper_codex_pane_has_yn "$_helper_before" &&
            [ "$_helper_did_y" -eq 0 ] &&
            { [ "$_helper_did_trust" -eq 1 ] ||
                ! helper_codex_pane_has_trust "$_helper_before"; }; then
            _helper_key=y
        elif helper_codex_pane_has_trust "$_helper_before" &&
            [ "$_helper_did_trust" -eq 0 ]; then
            _helper_key=Enter
        elif helper_codex_pane_has_yn "$_helper_before" &&
            [ "$_helper_did_y" -eq 0 ]; then
            _helper_key=y
        fi
        if [ -z "$_helper_key" ]; then
            _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
                _helper_got=
            helper_codex_seat_ok "$_helper_got" "$_helper_pane" || {
                [ "$_helper_sent" -gt 0 ] && break
                return $_helper_status
            }
            helper_seat_is_ready "$_helper_got" && return 0
            if [ "$_helper_sent" -gt 0 ]; then
                _helper_post_miss=$((_helper_post_miss + 1))
                [ "$_helper_post_miss" -ge 3 ] && break
                "$_helper_real" agent wait "$_helper_name" --until idle --until done \
                    --timeout 2000 >/dev/null 2>&1 || true
                continue
            fi
            _helper_miss=$((_helper_miss + 1))
            [ "$_helper_miss" -ge 2 ] && return $_helper_status
            # Herdr already called it blocked; the dialog may still be
            # painting. --until idle times out while it stays blocked.
            "$_helper_real" agent wait "$_helper_name" --until idle --until done \
                --timeout 2000 >/dev/null 2>&1 || true
            continue
        fi
        _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
            return $_helper_status
        helper_codex_seat_ok "$_helper_got" "$_helper_pane" || return $_helper_status
        "$_helper_real" agent send-keys "$_helper_name" "$_helper_key" || return $?
        _helper_sent=$((_helper_sent + 1))
        _helper_post_miss=0
        if [ "$_helper_key" = y ]; then
            _helper_did_y=1
        elif [ "$_helper_did_y" -eq 1 ]; then
            _helper_did_yn_enter=1
        else
            _helper_did_trust=1
        fi
        if "$_helper_real" agent wait "$_helper_name" --until idle --until done \
            --timeout 5000; then
            _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) ||
                _helper_got=
            helper_codex_seat_ok "$_helper_got" "$_helper_pane" || return 1
            helper_seat_is_ready "$_helper_got" && return 0
        fi
    done
    [ "$_helper_sent" -gt 0 ] || return $_helper_status
    "$_helper_real" agent wait "$_helper_name" --until idle --until done \
        --timeout 120000 ||
        return $?
    _helper_got=$("$_helper_real" agent get "$_helper_name" 2>/dev/null) || {
        printf '%s\n' "lantern: Codex in $_helper_name did not become ready after the startup gate" >&2
        return 1
    }
    helper_codex_seat_ok "$_helper_got" "$_helper_pane" || return 1
    helper_seat_is_ready "$_helper_got" && return 0
    printf '%s\n' "lantern: Codex in $_helper_name did not become ready after the startup gate" >&2
    return 1
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

helper_lantern_pane_in_workspace() {
    # $1 real herdr, $2 workspace id, $3 pane title. Prints a live lantern
    # chat already sitting in that workspace. Herdr does not deduplicate
    # plugin panes, so this is what stops a second chat.
    # `pane list` objects nest `scroll`, so the fragment that carries the
    # title carries pane_id but not workspace_id. Each candidate is
    # confirmed with `pane get`.
    [ -n "${2:-}" ] || return 1
    _helper_cands=$("$1" pane list 2>/dev/null |
        tr '{' '\n' |
        grep -F -e "\"label\":\"$3\"" |
        sed -n 's/.*"pane_id":"\([^"]*\)".*/\1/p') || return 1
    # Pane ids hold no spaces, so a plain word loop keeps this out of a
    # subshell and lets the function fail when it finds nothing.
    for _helper_cand in $_helper_cands; do
        _helper_cand_ws=$(helper_lantern_pane_workspace "$1" "$_helper_cand" "$3") ||
            continue
        if [ "$_helper_cand_ws" = "$2" ]; then
            printf '%s' "$_helper_cand"
            return 0
        fi
    done
    return 1
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
