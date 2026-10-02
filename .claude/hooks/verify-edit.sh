#!/usr/bin/env bash
# PostToolUse feedback loop (Write|Edit) — AI-Native SDLC playbook, Stage 4:
# "give Claude a way to verify its own work before a human sees it".
#
# Fast and scoped to the file that changed (the playbook's requirement for hooks):
#   - a factory .py file -> py_compile it, so a syntax error surfaces immediately
#     rather than at the next `make check`;
#   - an AGENT-CONFIGURATION file (agent .md, gates.py, model_tiers.py, prompt
#     assembly) -> remind that this is a behaviour change requiring `make evals`.
set -uo pipefail

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_response.filePath // .tool_input.file_path // ""')
[ -z "$path" ] && exit 0
[ -f "$path" ] || exit 0

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
py="$repo_root/mvp/.venv/bin/python"
notes=()

case "$path" in
  *"/mvp/src/factory/"*.py|*"/mvp/tests/"*.py)
    if [ -x "$py" ]; then
      if ! err=$("$py" -m py_compile "$path" 2>&1); then
        notes+=("SYNTAX ERROR in $(basename "$path") — fix before continuing:"$'\n'"${err:0:600}")
      fi
    fi
    ;;
esac

case "$path" in
  *"/.opencode/agents/"*.md|*"/mvp/src/factory/gates.py"|*"/mvp/src/factory/model_tiers.py"|*"/mvp/src/factory/context_pack.py"|*"/mvp/src/factory/pipeline.py")
    notes+=("AGENT-CONFIGURATION CHANGE ($(basename "$path")). This changes factory behaviour, not just code. Run \`cd mvp && make evals\` (and \`make check\` before claiming done) — the eval suite is the regression net for the agent configuration.")
    ;;
esac

[ ${#notes[@]} -eq 0 ] && exit 0

printf '%s\n' "${notes[@]}" | jq -Rsc '{
  hookSpecificOutput: { hookEventName: "PostToolUse", additionalContext: . }
}'
exit 0
