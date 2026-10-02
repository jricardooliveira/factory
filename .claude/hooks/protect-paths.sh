#!/usr/bin/env bash
# PreToolUse guardrail (Write|Edit) — AI-Native SDLC playbook, Stage 3:
# "hooks as build-time guardrails: block edits to protected paths".
#
# Protected here:
#   1. Generated factory OUTPUT (projects/*/repo/, mvp/repo/) — the product of a
#      pipeline run, not source. Hand-editing it invalidates the run's evidence.
#   2. SQLite state (*.db) — regenerable runtime state, never hand-authored.
#
# A block explains the reason AND the approval route, as the playbook requires.
set -uo pipefail

payload=$(cat)
path=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')
[ -z "$path" ] && exit 0

deny() {
  jq -nc --arg r "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $r
    }
  }'
  exit 0
}

case "$path" in
  */mvp/projects/*/repo/*|*/mvp/repo/*|*/projects/*/repo/*)
    deny "Protected path: this is generated factory OUTPUT, not source. It is produced by a pipeline run and gitignored; hand-editing it invalidates that run's trust package and git baseline. Route: change the agent definition, the gates, or the project spec and re-run the pipeline (\`factory run --project <id> \"...\"\`). To edit deliberately anyway, do it outside Claude Code."
    ;;
esac

case "$path" in
  *.db|*.db-wal|*.db-shm|*.sqlite|*.sqlite3)
    deny "Protected path: SQLite state is regenerable runtime state and is gitignored. Route: change it through the factory's own accessors in mvp/src/factory/state/db.py, or with an explicit sqlite3 command you have reviewed."
    ;;
esac

exit 0
