#!/bin/sh
# install.sh: put the `agent-shield` command on your PATH.
#
# It only creates one symlink, ~/.local/bin/agent-shield, pointing into this
# repository. It installs no packages and changes nothing outside your home
# directory. The real setup is `agent-shield install`, which asks before the
# one step that needs sudo.
#
#   ./install.sh              install the command, then run `agent-shield doctor`
#   ./install.sh --dry-run    only print what would happen
#   ./install.sh --uninstall  remove the command again
set -eu

repo=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
bin_dir=${XDG_BIN_HOME:-$HOME/.local/bin}
link=$bin_dir/agent-shield
dry_run=no
action=install

for arg in "$@"; do
    case $arg in
        --dry-run) dry_run=yes ;;
        --uninstall) action=uninstall ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: unknown option: $arg" >&2; exit 2 ;;
    esac
done

# step COMMAND...: print the command, and run it unless this is a dry run.
step() {
    if [ "$dry_run" = yes ]; then
        echo "[dry-run] run: $*"
    else
        echo "+ $*"
        "$@"
    fi
}

if [ "$(id -u)" -eq 0 ]; then
    echo "install.sh: run this as your normal user, not as root." >&2
    exit 1
fi

if [ "$action" = uninstall ]; then
    if [ ! -L "$link" ]; then
        echo "Nothing to remove: $link is not an agent-shield symlink."
        exit 0
    fi
    echo "Reminder: 'agent-shield uninstall' reverts the setup (units, logind drop-in)."
    echo "Run it before this if you haven't yet."
    step rm "$link"
    exit 0
fi

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    echo "install.sh: agent-shield needs Python 3.11 or newer as python3." >&2
    exit 1
fi
if [ -e "$link" ] && [ ! -L "$link" ]; then
    echo "install.sh: $link exists and is not a symlink; not touching it." >&2
    exit 1
fi

step mkdir -p "$bin_dir"
step ln -sfn "$repo/bin/agent-shield" "$link"

case ":$PATH:" in
    *":$bin_dir:"*) ;;
    *) echo "Note: $bin_dir is not on your PATH. Add this to ~/.profile:"
       echo "  export PATH=\"$bin_dir:\$PATH\"" ;;
esac

if [ "$dry_run" = no ]; then
    echo
    "$link" doctor || true
    echo
    echo "Next: agent-shield install"
fi
