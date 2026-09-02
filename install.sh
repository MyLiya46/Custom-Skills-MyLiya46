#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./install.sh --agent <claude-code|codex> [--global|--local] [--update] [--skill <name[,name...]|all>]

Options:
  --agent NAME       Installation target agent: claude-code or codex.
  --global           Install to the user's global skills directory (default).
  --local            Install to the current project's skills directory.
                     If neither is given, --global is assumed.
  --update           Replace each selected target skill directory completely.
                     This removes stale files and local edits in that skill.
  --skill NAMES      Skills to install. May be repeated, comma-separated, or all.
                     If omitted, defaults to 'all'.
  -h, --help         Show this help.

Examples:
  ./install.sh --agent claude-code                     # installs all skills globally
  ./install.sh --agent codex --local --skill plan-generator
  ./install.sh --agent claude-code --global --update --skill repo-committer
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
agent=""
install_mode=""          # "global" or "project"
update_install=false
requested_skills=()

while (($# > 0)); do
  case "$1" in
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
      [[ -z "$install_mode" ]] || die "--global and --local are mutually exclusive"
      install_mode="global"
      shift
      ;;
    --local)
      [[ -z "$install_mode" ]] || die "--global and --local are mutually exclusive"
      install_mode="project"
      shift
      ;;
    --update)
      update_install=true
      shift
      ;;
    --skill)
      (($# >= 2)) || die "--skill requires a value"
      requested_skills+=("$2")
      shift 2
      ;;
    --skill=*)
      requested_skills+=("${1#*=}")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
done

[[ "$agent" == "claude-code" || "$agent" == "codex" ]] \
  || die "--agent must be claude-code or codex"

# If no --skill given, default to "all"
if ((${#requested_skills[@]} == 0)); then
  requested_skills+=("all")
fi

case "$agent" in
  claude-code) agent_dir=".claude" ;;
  codex) agent_dir=".codex" ;;
esac

# Determine install directory
if [[ -z "$install_mode" || "$install_mode" == "global" ]]; then
  [[ -n "${HOME:-}" ]] || die "HOME is not set"
  skills_dir="$HOME/$agent_dir/skills"
else
  skills_dir="$PWD/$agent_dir/skills"
fi

available_skills=()
while IFS= read -r skill_dir; do
  available_skills+=("$(basename -- "$skill_dir")")
done < <(find "$script_dir" -mindepth 1 -maxdepth 1 -type d -exec test -f '{}/SKILL.md' \; -print | sort)

((${#available_skills[@]} > 0)) || die "no skills were found in $script_dir"

selected_skills=()
add_skill() {
  local candidate="$1"
  [[ -n "$candidate" ]] || die "skill name cannot be empty"
  [[ "$candidate" != */* && "$candidate" != .* ]] \
    || die "invalid skill name: $candidate"
  for skill in "${selected_skills[@]}"; do
    [[ "$skill" != "$candidate" ]] || return 0
  done
  selected_skills+=("$candidate")
}

for requested in "${requested_skills[@]}"; do
  IFS=',' read -r -a names <<< "$requested"
  for name in "${names[@]}"; do
    if [[ "$name" == "all" ]]; then
      selected_skills=("${available_skills[@]}")
    else
      add_skill "$name"
    fi
  done
done

for skill in "${selected_skills[@]}"; do
  source_dir="$script_dir/$skill"
  [[ -f "$source_dir/SKILL.md" ]] \
    || die "skill not found: $skill (use --skill all to see the available set)"
done

mkdir -p "$skills_dir"
skills_real="$(CDPATH= cd -- "$skills_dir" && pwd -P)"

for skill in "${selected_skills[@]}"; do
  target_dir="$skills_real/$skill"

  # If not updating and target already exists, skip it
  if ! "$update_install" && [[ -e "$target_dir" || -L "$target_dir" ]]; then
    echo "Skipping $skill -> $target_dir (already exists, use --update to overwrite)"
    continue
  fi

  # For --update: remove existing target (safety checks kept)
  if "$update_install"; then
    if [[ -e "$target_dir" || -L "$target_dir" ]]; then
      if [[ ! -d "$target_dir" || -L "$target_dir" ]]; then
        die "refusing --update for non-directory target: $target_dir"
      fi
      case "$target_dir" in
        "$skills_real"/*) ;;
        *) die "refusing --update outside skills directory: $target_dir" ;;
      esac
      if [[ "$target_dir" == "$skills_real" ]]; then
        die "refusing to remove skills directory itself"
      fi
      rm -rf -- "$target_dir"
    fi
  fi

  mkdir -p "$target_dir"
  cp -R "$script_dir/$skill/." "$target_dir/"
  if "$update_install"; then
    echo "Updated $skill -> $target_dir"
  else
    echo "Installed $skill -> $target_dir"
  fi
done

echo "Done: ${#selected_skills[@]} skill(s) installed for $agent."