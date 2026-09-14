#!/bin/sh
set -eu

station_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
station_source=${1:-"$station_root/dist/term-station"}
if [ "$#" -gt 1 ]; then
    printf '%s\n' '用法：./install.sh [可执行文件路径]' >&2
    exit 1
fi
if [ ! -x "$station_source" ]; then
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
station_temporary=$(mktemp "$station_bin/.term-station.XXXXXX")
trap 'if [ -n "$station_temporary" ]; then rm -f -- "$station_temporary"; fi' EXIT
trap 'exit 1' HUP INT TERM
install -m 755 "$station_source" "$station_temporary"
# Replacing the directory entry leaves any running executable intact.
mv -f -- "$station_temporary" "$station_destination"
station_temporary=''

quote_path() {
    printf "'"
    printf '%s' "$1" | sed "s/'/'\\\\''/g"
    printf "'"
}

station_quoted_bin=$(quote_path "$station_bin")
configure_path() {
    station_rc=$1
    if [ -f "$station_rc" ] && grep -Fqx '# >>> term-station PATH >>>' "$station_rc"; then
        return
    fi
    if [ -e "$station_rc" ]; then
        station_backup=$(mktemp "$station_rc.term-station-backup.XXXXXX")
        cp -p "$station_rc" "$station_backup"
    fi
    {
        printf '\n%s\n' '# >>> term-station PATH >>>'
        printf '%s\n' 'case ":$PATH:" in'
        printf '    *:%s:*) ;;\n' "$station_quoted_bin"
        printf '    *) export PATH=%s:"$PATH" ;;\n' "$station_quoted_bin"
        printf '%s\n' 'esac' '# <<< term-station PATH <<<'
    } >> "$station_rc"
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
