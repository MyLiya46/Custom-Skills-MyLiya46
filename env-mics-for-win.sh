#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./install-misc.sh --agent <claude-code|codex> [--global|--local] [--install|--update|--append]

Description:
  Install the agent-specific instruction file from misc/.
  The default operation is append and the default location is global.

Options:
  --agent NAME       Agent to configure: claude-code or codex.
  --global           Install to the user's global agent directory (default).
  --local            Install to the current project's agent directory.
  --mode MODE        Operation: install, update, or append.
  --install          Same as --mode install.
  --update           Same as --mode update.
  --append           Same as --mode append.
  -h, --help         Show this help.

Operations:
  install             Create the target file; fail if it already exists.
  update              Replace the target file with the source file.
  append              Append the source file to the target file (default).

Examples:
  ./install-misc.sh --agent codex
  ./install-misc.sh --update --agent claude-code --global
  ./install-misc.sh --append --agent codex --local
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

set_operation() {
  local requested="$1"

  case "$requested" in
    install|update|append) ;;
    *) die "invalid operation: $requested (expected install, update, or append)" ;;
  esac

  if [[ "$operation_set" == true && "$operation" != "$requested" ]]; then
    die "operation was specified more than once"
  fi

  operation="$requested"
  operation_set=true
}

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
misc_dir="$script_dir/misc"
agent=""
location="global"
location_set=false
operation="append"
operation_set=false

while (($# > 0)); do
  case "$1" in
    install|update|append)
      set_operation "$1"
      shift
      ;;
    --agent)
      (($# >= 2)) || die "--agent requires a value"
      agent="$2"
      shift 2
      ;;
    --agent=*)
      agent="${1#*=}"
      shift
      ;;
    --global)
      [[ "$location_set" == false || "$location" == global ]] \
        || die "--global and --local are mutually exclusive"
      location="global"
      location_set=true
      shift
      ;;
    --local)
      [[ "$location_set" == false || "$location" == local ]] \
        || die "--global and --local are mutually exclusive"
      location="local"
      location_set=true
      shift
      ;;
    --location)
      (($# >= 2)) || die "--location requires global or local"
      [[ "$2" == global || "$2" == local ]] \
        || die "--location must be global or local"
      if [[ "$location_set" == true && "$location" != "$2" ]]; then
        die "installation location was specified more than once"
      fi
      location="$2"
      location_set=true
      shift 2
      ;;
    --location=*)
      requested_location="${1#*=}"
      [[ "$requested_location" == global || "$requested_location" == local ]] \
        || die "--location must be global or local"
      if [[ "$location_set" == true && "$location" != "$requested_location" ]]; then
        die "installation location was specified more than once"
      fi
      location="$requested_location"
      location_set=true
      shift
      ;;
    --mode|--operation)
      (($# >= 2)) || die "$1 requires a value"
      set_operation "$2"
      shift 2
      ;;
    --mode=*|--operation=*)
      set_operation "${1#*=}"
      shift
      ;;
    --install)
      set_operation install
      shift
      ;;
    --update)
      set_operation update
      shift
      ;;
    --append)
      set_operation append
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$agent" in
  claude|claude-code)
    agent_dir=".claude"
    source_name="CLAUDE.md"
    ;;
  codex)
    agent_dir=".codex"
    source_name="AGENTS.md"
    ;;
  "")
    die "--agent is required (expected claude-code or codex)"
    ;;
  *)
    die "unsupported agent: $agent (expected claude-code or codex)"
    ;;
esac

source_file="$misc_dir/$source_name"
[[ -f "$source_file" ]] || die "source file not found: $source_file"

if [[ "$location" == global ]]; then
  [[ -n "${HOME:-}" ]] || die "HOME is not set; use --local or set HOME"
  target_dir="$HOME/$agent_dir"
else
  target_dir="$PWD/$agent_dir"
fi

target_file="$target_dir/$source_name"
mkdir -p "$target_dir"

case "$operation" in
  install)
    [[ ! -e "$target_file" && ! -L "$target_file" ]] \
      || die "target already exists: $target_file (use update or append)"
    cp -- "$source_file" "$target_file"
    echo "Installed $source_name -> $target_file"
    ;;
  update)
    if [[ -e "$target_file" || -L "$target_file" ]]; then
      [[ -f "$target_file" && ! -L "$target_file" ]] \
        || die "target is not a regular file: $target_file"
    fi
    # cp replaces the file contents while preserving the target path.
    cp -- "$source_file" "$target_file"
    echo "Updated $source_name -> $target_file"
    ;;
  append)
    if [[ ! -e "$target_file" && ! -L "$target_file" ]]; then
      cp -- "$source_file" "$target_file"
    else
      [[ -f "$target_file" && ! -L "$target_file" ]] \
        || die "target is not a regular file: $target_file"

      # Keep the two documents separated even when the existing file has no
      # trailing newline.  The source is then appended exactly as stored.
      if [[ -s "$target_file" && "$(tail -c 1 -- "$target_file" | wc -l)" -eq 0 ]]; then
        printf '\n' >> "$target_file"
      fi
      cat -- "$source_file" >> "$target_file"
    fi
    echo "Appended $source_name -> $target_file"
    ;;
esac
