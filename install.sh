#!/bin/sh
set -eu

station_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
station_source=${1:-"$station_root/dist/term-station"}
if [ "$#" -gt 1 ]; then
    printf '%s\n' '用法：./install.sh [程序目录或可执行文件路径]' >&2
    exit 1
fi
station_bundle=''
if [ -d "$station_source" ]; then
    station_bundle=$station_source
    station_source="$station_bundle/term-station"
elif [ -d "$(dirname -- "$station_source")/_internal" ]; then
    station_bundle=$(dirname -- "$station_source")
fi
if [ ! -f "$station_source" ] || [ ! -x "$station_source" ]; then
    printf '%s\n' "找不到可执行文件：$station_source，请先运行 ./build.sh。" >&2
    exit 1
fi

station_home=${TERM_STATION_INSTALL_HOME:-"$HOME"}
station_bin="$station_home/.local/bin"
station_destination="$station_bin/term-station"
station_shell=${SHELL:-/bin/zsh}
station_shell=${station_shell##*/}
case "$station_shell" in
    zsh) station_config_dir=${ZDOTDIR:-"$station_home"} ;;
    bash|sh) station_config_dir=$station_home ;;
    *) printf '%s\n' "暂不支持自动配置 $station_shell 的 PATH。" >&2; exit 1 ;;
esac

# Verify the artifact before changing the installed program or shell config.
"$station_source" --version
mkdir -p "$station_bin" "$station_config_dir"
station_work=$(mktemp -d "$station_bin/.term-station.XXXXXX")
station_release=''
trap 'rm -rf -- "$station_work"; if [ -n "$station_release" ]; then rm -rf -- "$station_release"; fi' EXIT
trap 'exit 1' HUP INT TERM
if [ -n "$station_bundle" ]; then
    station_releases="$station_home/.local/share/term-station/releases"
    mkdir -p "$station_releases"
    station_release=$(mktemp -d "$station_releases/runtime.XXXXXX")
    cp -R "$station_bundle/." "$station_release/"
    # Validate the relocated runtime before making it the active command.
    "$station_release/term-station" --version
    ln -s "$station_release/term-station" "$station_work/term-station"
else
    # Continue to accept older, standalone one-file artifacts explicitly.
    install -m 755 "$station_source" "$station_work/term-station"
fi
# Keep previous release directories: a detached daemon may still import from
# them. Only replace the command's directory entry, never its running target.
mv -f -- "$station_work/term-station" "$station_destination"
station_release=''

quote_path() {
    printf "'"
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
    printf "'"
}

station_quoted_bin=$(quote_path "$station_bin")
# Put this installation first even if another installer put Homebrew ahead of
# an existing .local/bin entry. Remove previous occurrences before prepending.
{
    printf '%s\n' '# >>> term-station PATH >>>' 'term_station_path=":${PATH-}:"'
    printf 'while case "$term_station_path" in *:%s:*) true ;; *) false ;; esac; do\n' "$station_quoted_bin"
    printf '    term_station_path="${term_station_path%%%%:%s:*}:${term_station_path#*:%s:}"\n' "$station_quoted_bin" "$station_quoted_bin"
    printf '%s\n' 'done' 'term_station_path=${term_station_path#:}' 'term_station_path=${term_station_path%:}'
    printf 'export PATH=%s:"$term_station_path"\n' "$station_quoted_bin"
    printf '%s\n' 'unset term_station_path' '# <<< term-station PATH <<<'
} > "$station_work/path-block"

configure_path() {
    station_rc=$1
    station_existing=$station_rc
    if [ ! -e "$station_existing" ]; then station_existing=/dev/null; fi
    # Replace only our own marked block; preserve the user's other settings.
    awk '
        NR == FNR { block = block $0 "\n"; next }
        $0 == "# >>> term-station PATH >>>" { printf "%s", block; found = 1; inside = 1; next }
        $0 == "# <<< term-station PATH <<<" && inside { inside = 0; next }
        !inside { print }
        END { if (inside) exit 1; if (!found) printf "\n%s", block }
    ' "$station_work/path-block" "$station_existing" > "$station_work/config"
    if cmp -s "$station_rc" "$station_work/config"; then return; fi
    if [ -e "$station_rc" ]; then
        station_backup=$(mktemp "$station_rc.term-station-backup.XXXXXX")
        cp -p "$station_rc" "$station_backup"
    fi
    cat "$station_work/config" > "$station_rc"
}

case "$station_shell" in
    zsh)
        configure_path "$station_config_dir/.zprofile"
        configure_path "$station_config_dir/.zshrc"
        ;;
    bash)
        station_profile="$station_home/.profile"
        for station_candidate in .bash_profile .bash_login; do
            if [ -f "$station_home/$station_candidate" ]; then
                station_profile="$station_home/$station_candidate"
                break
            fi
        done
        configure_path "$station_profile"
        configure_path "$station_home/.bashrc"
        ;;
    sh) configure_path "$station_home/.profile" ;;
esac

printf '%s\n' "已安装：$station_destination" '新开终端后输入 term-station 即可启动。'
printf '当前终端立即生效：export PATH=%s:"$PATH"\n' "$station_quoted_bin"
