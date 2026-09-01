#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./install.sh --agent <claude-code|codex> [--global] --skill <name[,name...]|all>

Options:
  --agent NAME       Installation target agent: claude-code or codex.
  --global           Install to the user's global skills directory.
                     Without this option, install to the current project.
  --skill NAMES      Skills to install. May be repeated, comma-separated, or all.
  -h, --help         Show this help.

Examples:
  ./install.sh --agent claude-code --skill all
  ./install.sh --agent codex --global --skill plan-generator,plan-executor
  ./install.sh --agent codex --skill learning-tutor --skill repo-committer
EOF
}

die() {
  echo "Error: $*" >&2
  exit 1
}

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
agent=""
global_install=false
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
      global_install=true
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
((${#requested_skills[@]} > 0)) || die "at least one --skill is required"

case "$agent" in
  claude-code) agent_dir=".claude" ;;
  codex) agent_dir=".codex" ;;
esac

if "$global_install"; then
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
for skill in "${selected_skills[@]}"; do
  target_dir="$skills_dir/$skill"
  mkdir -p "$target_dir"
  cp -R "$script_dir/$skill/." "$target_dir/"
  echo "Installed $skill -> $target_dir"
done

echo "Done: ${#selected_skills[@]} skill(s) installed for $agent."
