#!/bin/bash

set -euo pipefail

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$_SCRIPT_DIR/lib/common.sh"
# shellcheck source=lib/json.sh
source "$_SCRIPT_DIR/lib/json.sh"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'

SANITY_PID_FILE="${TMPDIR:-/tmp}/vast-claw-sanity.pid"
# Consecutive failed detail polls before aborting (API down during cleanup/redeploy).
API_FAIL_MAX=12

stop_sanity() {
  if [ ! -f "$SANITY_PID_FILE" ]; then
    echo -e "${YELLOW}⚠ no active sanity run (missing $SANITY_PID_FILE)${NC}" >&2
    exit 0
  fi
  local pid
  pid="$(cat "$SANITY_PID_FILE" 2>/dev/null || true)"
  if [ -z "$pid" ]; then
    rm -f "$SANITY_PID_FILE"
    echo -e "${YELLOW}⚠ removed empty sanity pid file${NC}" >&2
    exit 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$SANITY_PID_FILE"
    echo -e "${YELLOW}⚠ removed stale sanity pid file (pid $pid not running)${NC}" >&2
    exit 0
  fi
  echo -e "${CYAN}▸${NC} stopping sanity run (pid $pid)…" >&2
  kill -TERM "$pid" 2>/dev/null || true
  pkill -TERM -P "$pid" 2>/dev/null || true
  local i=0
  while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    pkill -KILL -P "$pid" 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f "$SANITY_PID_FILE"
  echo -e "${GREEN}✓${NC} sanity stopped" >&2
}

case "${1:-}" in
  stop|--stop|-stop) stop_sanity; exit 0 ;;
  ""|run) ;;
  *)
    echo "Usage: $0 [stop]" >&2
    echo "  (no args)  run the suite (WORKFLOWS_COUNT_PER_DEP × 2 depts + sales delete target)" >&2
    echo "  stop       kill the active sanity run (use before cleanup/redeploy)" >&2
    exit 1
    ;;
esac

# ── CONFIG — edit before running ──────────────────────────────────────────────
CLUSTER="${CLUSTER:-v3121}"
BASE="${BASE:-}"                       # empty → https://vastclaw.solutions.vastdata.com
BASE_DOMAIN="${BASE_DOMAIN:-vastdata.com}"
SALES_USER="${SALES_USER:-sales-agent-1}"
FINANCE_USER="${FINANCE_USER:-finance-agent-1}"
SALES_PASS="${SALES_PASS:-Aa123456!}"  # VMS password for sales-agent-1
FINANCE_PASS="${FINANCE_PASS:-}"       # empty → same as SALES_PASS
WORKFLOWS_COUNT_PER_DEP="${WORKFLOWS_COUNT_PER_DEP:-1}"
TIMEOUT_SECS="${TIMEOUT_SECS:-900}"        # per research run (observed max ~520s)
QA_TIMEOUT_SECS="${QA_TIMEOUT_SECS:-180}"  # per Q&A answer (observed ~22s)
SANDBOX_GONE_SECS="${SANDBOX_GONE_SECS:-60}"  # force-sleep: pods to stop Running
PVC_REAP_SECS="${PVC_REAP_SECS:-60}"       # delete: CSI/PV teardown lags the API
POLL_SECS=5
SKIP_ACL=0                    # 1 = skip cross-department ACL check
SKIP_SLEEP_WAKE="${SKIP_SLEEP_WAKE:-0}"   # 1 = skip force-sleep + continue (remount PVC)
SKIP_QA="${SKIP_QA:-0}"                    # 1 = skip Quick Q&A check
SKIP_DELETE="${SKIP_DELETE:-0}"           # 1 = skip workflow-delete check (owner ACL + cascade)
MULTILINE_TASK_ID="sales-a"
INSECURE=0                    # 1 = curl -k (skip TLS verify)
COLLECT_DIAGNOSTICS=1         # 1 = scan backend + agent pod logs for errors after the run
DIAG_STRICT=0                 # 1 = collected log issues count as failures (default: warnings)
DIAG_MAX_SIGNATURES=8         # distinct error signatures reported per source
UI_NS="vastclaw-observability"
BACKEND_DEPLOY="vastclaw-workflow-ui-backend"
DEPARTMENT_NS=(vastclaw-sales vastclaw-finance)
# ─────────────────────────────────────────────────────────────────────────────

if [ -z "$BASE" ]; then
  [ -n "$CLUSTER" ] || { echo -e "${RED}✗ set CLUSTER or BASE${NC}"; exit 1; }
  BASE="https://vastclaw.solutions.vastdata.com"
fi

FINANCE_PASS="${FINANCE_PASS:-$SALES_PASS}"
[ -n "$SALES_PASS" ] || { echo -e "${RED}✗ set SALES_PASS in CONFIG${NC}"; exit 1; }
[ -n "$FINANCE_PASS" ] || { echo -e "${RED}✗ set FINANCE_PASS in CONFIG (or leave empty to reuse SALES_PASS)${NC}"; exit 1; }

case "$WORKFLOWS_COUNT_PER_DEP" in
  1|2|3) ;;
  *)
    echo -e "${RED}✗ WORKFLOWS_COUNT_PER_DEP must be 1, 2, or 3 (got: $WORKFLOWS_COUNT_PER_DEP)${NC}" >&2
    exit 1
    ;;
esac
TOTAL_TASKS=$((WORKFLOWS_COUNT_PER_DEP * 2 + 1))

command -v curl >/dev/null 2>&1 || { echo -e "${RED}✗ curl required${NC}"; exit 1; }
command -v jq >/dev/null 2>&1 || { echo -e "${RED}✗ jq required${NC}"; exit 1; }

CURL=(curl -sS)
[ "$INSECURE" = "1" ] && CURL+=(-k)

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
TASK_LABEL=""
SANITY_STATS_DIR=""
SANITY_TASK_ID=""

log()  { echo -e "${CYAN}▸${NC} ${TASK_LABEL:+[$TASK_LABEL] }$*" >&2; }
ok()   {
  echo -e "${GREEN}✓${NC} ${TASK_LABEL:+[$TASK_LABEL] }$*" >&2
  if [ -n "$SANITY_STATS_DIR" ] && [ -n "$SANITY_TASK_ID" ]; then
    echo 1 >> "$SANITY_STATS_DIR/$SANITY_TASK_ID.pass"
  else
    PASS_COUNT=$((PASS_COUNT + 1))
  fi
}
fail() {
  echo -e "${RED}✗${NC} ${TASK_LABEL:+[$TASK_LABEL] }$*" >&2
  if [ -n "$SANITY_STATS_DIR" ] && [ -n "$SANITY_TASK_ID" ]; then
    echo 1 >> "$SANITY_STATS_DIR/$SANITY_TASK_ID.fail"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}
warn() {
  echo -e "${YELLOW}⚠${NC} ${TASK_LABEL:+[$TASK_LABEL] }$*" >&2
  if [ -n "$SANITY_STATS_DIR" ] && [ -n "$SANITY_TASK_ID" ]; then
    echo 1 >> "$SANITY_STATS_DIR/$SANITY_TASK_ID.warn"
  else
    WARN_COUNT=$((WARN_COUNT + 1))
  fi
}

count_stat_files() {
  local kind="$1" total=0 f n
  shopt -s nullglob
  for f in "$SANITY_STATS_DIR"/*."$kind"; do
    n=$(wc -l < "$f" | tr -d ' ')
    total=$((total + n))
  done
  shopt -u nullglob
  echo "$total"
}

aggregate_parallel_stats() {
  PASS_COUNT=$(count_stat_files pass)
  FAIL_COUNT=$(count_stat_files fail)
  WARN_COUNT=$(count_stat_files warn)
}

api() {
  local method="$1" path="$2" token="${3:-}" body="${4:-}"
  local -a args=("${CURL[@]}" -X "$method" "$BASE$path" -H 'Content-Type: application/json')
  [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
  [ -n "$body" ] && args+=(-d "$body")
  "${args[@]}"
}

login() {
  local user="$1" pass="$2"
  local resp token
  resp=$(api POST /api/v1/login "" "{\"username\":\"$user\",\"password\":\"$pass\"}") || {
    fail "login request failed for $user"; return 1; }
  if ! vc_is_json "$resp"; then
    fail "login for $user: API returned non-JSON (platform down?)"
    return 1
  fi
  token=$(vc_json "$resp" -r .access_token)
  if [ -z "$token" ] || [ "$token" = "null" ]; then
    fail "login rejected for $user: $(vc_json "$resp" -c '.detail // .' 2>/dev/null || printf '%s' "$resp" | head -c 120)"
    return 1
  fi
  echo "$token"
}

extract_plan() {
  vc_json "$1" -c '
    [.graph.nodes[]
     | select(.attributes.plan_json? != null and .attributes.plan_json != "")
     | .attributes.plan_json
     | fromjson] | .[0] // empty
  ' 2>/dev/null || true
}

extract_summary() {
  vc_json "$1" -r '
    [.graph.nodes[]
     | select(.attributes.summary? != null and .attributes.summary != "")
     | .attributes.summary] | .[0] // empty
  ' 2>/dev/null || true
}

count_subagents() {
  vc_json_or "$1" "0" '[.graph.nodes[] | select(.agent_name != "research-lead" and .agent_name != "synthesis")] | length'
}

validate_multiline_spawn() {
  local label="$1" detail="$2" agents
  if printf '%s' "$detail" | grep -qi 'newline or carriage return'; then
    fail "$label: multi-line prompt broke sub-agent spawn (env not sanitized)"
    return 1
  fi
  agents=$(count_subagents "$detail")
  if [ "${agents:-0}" -ge 1 ]; then
    ok "$label: multi-line prompt spawned $agents sub-agent(s) (env-sanitize OK)"
  else
    fail "$label: multi-line prompt produced no sub-agent nodes (spawn path suspect)"
    return 1
  fi
}

approve_plan() {
  local wf="$1" token="$2" detail="$3"
  local plan
  plan=$(extract_plan "$detail")
  if [ -z "$plan" ] || [ "$plan" = "null" ]; then
    fail "workflow $wf: awaiting_approval but no plan_json found"
    return 1
  fi
  log "workflow $wf: approving plan ($(printf '%s' "$plan" | command jq 'length' 2>/dev/null || echo '?') sub-agent(s))"
  api POST "/api/v1/workflows/$wf/approve" "$token" "{\"items\":$plan}" >/dev/null
}

poll_workflow() {
  local wf="$1" token="$2" label="$3"
  local start now detail status elapsed api_fails=0
  start=$(date +%s)
  while true; do
    now=$(date +%s)
    elapsed=$((now - start))
    if [ "$elapsed" -ge "$TIMEOUT_SECS" ]; then
      fail "$label: timed out after ${TIMEOUT_SECS}s (workflow $wf)"
      return 1
    fi
    detail=$(api GET "/api/v1/workflows/$wf/detail" "$token")
    if ! vc_is_json "$detail"; then
      api_fails=$((api_fails + 1))
      printf "  [%3ds] %-28s status=(API unavailable)\n" "$elapsed" "$label" >&2
      if [ "$api_fails" -ge "$API_FAIL_MAX" ]; then
        fail "$label: API unavailable for ${api_fails} polls (~$((api_fails * POLL_SECS))s) — platform down? stop sanity before cleanup/redeploy: ./deploy/vast-claw-sanity.sh stop"
        return 1
      fi
      sleep "$POLL_SECS"
      continue
    fi
    api_fails=0
    if openclaw_response_failed "$label" "$detail"; then
      return 1
    fi
    status=$(vc_json "$detail" -r .workflow.status 2>/dev/null || echo "")
    printf "  [%3ds] %-28s status=%s\n" "$elapsed" "$label" "${status:-?}" >&2
    case "$status" in
      awaiting_approval)
        # Fallback for older backends without auto_approve_plan on create.
        approve_plan "$wf" "$token" "$detail" || return 1
        ;;
      completed)
        echo "$detail"
        return 0
        ;;
      error|dispatch_failed)
        fail "$label: run ended with status=$status"
        vc_json "$detail" -c '.graph.nodes[] | select(.status=="error") | {agent: .agent_name, err: .attributes.error}' 2>/dev/null >&2 || true
        return 1
        ;;
    esac
    sleep "$POLL_SECS"
  done
}

summary_has_any() {
  local summary="$1"; shift
  local lower word
  lower=$(echo "$summary" | tr '[:upper:]' '[:lower:]')
  for word in "$@"; do
    if echo "$lower" | grep -q "$word"; then
      return 0
    fi
  done
  return 1
}

count_summary_terms() {
  local summary="$1"; shift
  local lower count=0 term
  lower=$(echo "$summary" | tr '[:upper:]' '[:lower:]')
  for term in "$@"; do
    if echo "$lower" | grep -q "$term"; then
      count=$((count + 1))
    fi
  done
  echo "$count"
}

validate_task_quality() {
  local label="$1" detail="$2" min_matches="$3"
  shift 3
  local -a expected=("$@")
  local summary matches total=${#expected[@]} agents

  summary=$(extract_summary "$detail")
  if [ -z "$summary" ]; then
    fail "$label: no synthesis summary in graph"
    return 1
  fi

  agents=$(count_subagents "$detail")
  ok "$label: summary present (${#summary} chars, $agents sub-agent node(s))"

  matches=$(count_summary_terms "$summary" "${expected[@]}")
  if [ "$matches" -ge "$min_matches" ]; then
    ok "$label: corpus-grounded ($matches/$total expected themes: ${expected[*]})"
  elif [ "$matches" -ge 1 ]; then
    warn "$label: weakly grounded ($matches/$min_matches required themes; got: ${expected[*]})"
  else
    fail "$label: summary not grounded in expected corpus themes (${expected[*]})"
    return 1
  fi
}

validate_persona() {
  local dept="$1" label="$2" detail="$3"
  local summary
  summary=$(extract_summary "$detail")
  [ -n "$summary" ] || return 0

  if [ "$dept" = "sales" ]; then
    if summary_has_any "$summary" "next step" competitive demand outlook guidance sector company account action pipeline; then
      ok "$label: reads sales-oriented (persona keywords)"
    else
      warn "$label: may lack sales persona cues (next step / competitive / demand / company)"
    fi
  elif [ "$dept" = "finance" ]; then
    if summary_has_any "$summary" pricing margin guidance revenue capital cost commercial economics credit expense; then
      ok "$label: reads finance-oriented (persona keywords)"
    else
      warn "$label: may lack finance persona cues (guidance / margin / capital / pricing)"
    fi
  fi
}

validate_storage() {
  local label="$1" detail="$2"
  local storage snaps clones subagents

  if ! vc_is_json "$detail"; then
    warn "$label: storage check skipped (API unavailable)"
    return 0
  fi
  storage=$(vc_json "$detail" -c '.storage // empty' 2>/dev/null || echo "")
  if [ -z "$storage" ] || [ "$storage" = "null" ]; then
    warn "$label: no storage telemetry (kubernetes mode / RBAC / snapshotter?)"
    return 0
  fi

  snaps=$(vc_json "$detail" '[.storage.snapshots[]?] | length' 2>/dev/null || echo 0)
  clones=$(vc_json "$detail" '[.storage.pvcs[]? | select(.clone_source_pvc != "")] | length' 2>/dev/null || echo 0)
  subagents=$(vc_json "$detail" '[.storage.pvcs[]? | select(.role == "subagent")] | length' 2>/dev/null || echo 0)

  if [ "$snaps" -ge 1 ]; then
    ok "$label: VolumeSnapshot checkpoint ($snaps snapshot(s))"
  else
    warn "$label: no VolumeSnapshot yet (checkpoint_before_fanout off or snapshotter missing?)"
  fi

  if [ "$clones" -ge 1 ]; then
    ok "$label: clone-on-spawn ($clones sub-agent PVC(s) with clone source)"
  elif [ "$subagents" -ge 1 ]; then
    warn "$label: sub-agent PVCs exist but no clone_source_pvc (clone disabled?)"
  else
    warn "$label: no sub-agent PVCs in storage view yet"
  fi
}

# ── Post-run: sleep/wake ──────────────────────────────────────────────────────

dept_ns() { echo "vastclaw-$1"; }

workflow_pvc_count() {
  local ns="$1" wf="$2"
  kubectl -n "$ns" get pvc -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
    | grep -c "^${wf}" || true
}

workflow_running_pods() {
  local ns="$1" wf="$2"
  kubectl -n "$ns" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.phase}{"\n"}{end}' 2>/dev/null \
    | awk -v p="$wf" '$1 ~ ("^" p) && $2 == "Running" { c++ } END { print c+0 }'
}

force_sleep_sandboxes() {
  # Delete OpenShell sandboxes + pods for this workflow; keep PVCs (the sleep model).
  local ns="$1" wf="$2"
  local name
  log "force-sleep: deleting sandboxes/pods with prefix ${wf}-* in $ns (PVCs retained)"
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    kubectl -n "$ns" delete "sandboxes.agents.x-k8s.io/$name" --wait=false --ignore-not-found >/dev/null 2>&1 || true
  done < <(kubectl -n "$ns" get sandboxes.agents.x-k8s.io -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep "^${wf}" || true)
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    kubectl -n "$ns" delete pod "$name" --force --grace-period=0 --ignore-not-found >/dev/null 2>&1 || true
  done < <(kubectl -n "$ns" get pods -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | grep "^${wf}" || true)

  local i running
  for i in $(seq 1 $((SANDBOX_GONE_SECS / 2))); do
    running=$(workflow_running_pods "$ns" "$wf")
    [ "$running" -eq 0 ] && return 0
    sleep 2
  done
  fail "force-sleep: still $running Running pod(s) for $wf after ~${SANDBOX_GONE_SECS}s"
  return 1
}

test_sleep_wake() {
  local dept="$1" user="$2" pass="$3" wf="$4"
  local ns token detail head_before head_after pvc_before pvc_after hint woke label
  local continue_prompt
  ns=$(dept_ns "$dept")
  label="sleep-wake/$dept"
  echo ""
  echo -e "${YELLOW}━━ $label (workflow $wf) ━━${NC}"

  token=$(login "$user" "$pass") || return 1
  detail=$(api GET "/api/v1/workflows/$wf/detail" "$token")
  if ! vc_is_json "$detail"; then
    fail "$label: detail not JSON"; return 1
  fi
  head_before=$(vc_json "$detail" -r '.workflow.pvc_name // empty' 2>/dev/null || echo "")
  [ -n "$head_before" ] || head_before="$wf"
  pvc_before=$(workflow_pvc_count "$ns" "$wf")
  if [ "${pvc_before:-0}" -lt 1 ]; then
    fail "$label: no PVCs for $wf before sleep (nothing to remount)"
    return 1
  fi
  ok "$label: ${pvc_before} PVC(s) present; head=$head_before"

  force_sleep_sandboxes "$ns" "$wf" || return 1
  pvc_after=$(workflow_pvc_count "$ns" "$wf")
  if [ "${pvc_after:-0}" -lt 1 ]; then
    fail "$label: PVCs disappeared after force-sleep (expected retain)"
    return 1
  fi
  ok "$label: sandboxes gone; ${pvc_after} PVC(s) retained"

  detail=$(api GET "/api/v1/workflows/$wf/detail" "$token")
  hint=$(vc_json_or "$detail" "false" -r '.wake_hint.continue_will_wake // false')
  if [ "$hint" = "true" ]; then
    ok "$label: wake_hint.continue_will_wake=true"
  else
    warn "$label: wake_hint.continue_will_wake=$hint (continuing anyway)"
  fi

  continue_prompt='Briefly confirm the prior findings in 2-3 sentences. Do not expand scope.'
  log "$label: POST /turns (continue → wake + remount)"
  detail=$(api POST "/api/v1/workflows/$wf/turns" "$token" \
    "$(command jq -nc --arg p "$continue_prompt" '{prompt:$p,auto_approve_plan:true}')")
  if ! vc_is_json "$detail"; then
    fail "$label: continue returned non-JSON"; return 1
  fi

  local poll_rc=0
  detail=$(poll_workflow "$wf" "$token" "$label") || poll_rc=$?
  [ "$poll_rc" -eq 0 ] || detail=$(api GET "/api/v1/workflows/$wf/detail" "$token" || true)
  if [ -n "$detail" ]; then
    printf '%s' "$detail" >"$SANITY_RUN_DIR/workflows/sleep-wake-${dept}.json"
  fi
  [ "$poll_rc" -eq 0 ] || return 1

  head_after=$(vc_json "$detail" -r '.workflow.pvc_name // empty' 2>/dev/null || echo "")
  [ -n "$head_after" ] || head_after="$wf"
  if [ "$head_after" = "$head_before" ]; then
    ok "$label: head PVC remounted ($head_after)"
  else
    fail "$label: head PVC changed ($head_before → $head_after)"
  fi

  woke=$(vc_json_or "$detail" "0" \
    '[.graph.nodes[]? | select(.attributes.label? == "wake-on-continue" or .attributes.woke_volumes? == true)] | length')
  if [ "${woke:-0}" -ge 1 ]; then
    ok "$label: wake-on-continue recorded in graph"
  else
    # Remount may still have happened without a dedicated node — PVC identity is the hard check.
    warn "$label: no wake-on-continue graph node (PVC remount still verified)"
  fi
}

test_followup_qa() {
  local dept="$1" user="$2" pass="$3" wf="$4"
  local token detail status label q
  local last_run_before last_run_after subagents_before subagents_after
  local qa_status qa_answer qa_count start elapsed
  label="qa/$dept"
  echo ""
  echo -e "${YELLOW}━━ $label (workflow $wf) ━━${NC}"

  token=$(login "$user" "$pass") || return 1
  detail=$(api GET "/api/v1/workflows/$wf/detail" "$token")
  if ! vc_is_json "$detail"; then
    fail "$label: detail not JSON"; return 1
  fi
  last_run_before=$(vc_json_or "$detail" "" -r '.workflow.last_run_id // empty')
  subagents_before=$(count_subagents "$detail")

  q='In one or two sentences, what was the single most important takeaway from this report, and why?'
  log "$label: POST /turns mode=qa (follow-up about existing report — no new research)"
  detail=$(api POST "/api/v1/workflows/$wf/turns" "$token" \
    "$(command jq -nc --arg p "$q" '{prompt:$p,mode:"qa"}')")
  if ! vc_is_json "$detail"; then
    fail "$label: qa turn returned non-JSON"; return 1
  fi

  # A Q&A must never move the workflow back into an active/approval state.
  status=$(vc_json_or "$detail" "" -r '.workflow.status // empty')
  case "$status" in
    completed|error|dispatch_failed)
      ok "$label: workflow status stays '$status' (no new research run, no approval)" ;;
    *)
      fail "$label: workflow flipped to '$status' after a Q&A (should stay completed)" ;;
  esac

  start=$(date +%s); qa_status=""; qa_answer=""
  while true; do
    elapsed=$(( $(date +%s) - start ))
    if [ "$elapsed" -ge "$QA_TIMEOUT_SECS" ]; then
      fail "$label: Q&A answer did not complete within ${QA_TIMEOUT_SECS}s"
      break
    fi
    detail=$(api GET "/api/v1/workflows/$wf/detail" "$token")
    if ! vc_is_json "$detail"; then sleep "$POLL_SECS"; continue; fi
    qa_status=$(vc_json_or "$detail" "" -r '[.qa_messages[]?] | last | .status // empty')
    qa_answer=$(vc_json_or "$detail" "" -r '[.qa_messages[]?] | last | .answer // empty')
    printf "  [%3ds] %-28s qa=%s\n" "$elapsed" "$label" "${qa_status:-?}" >&2
    case "$qa_status" in
      ok|completed|degraded|error) break ;;
    esac
    sleep "$POLL_SECS"
  done

  [ -n "$detail" ] && printf '%s' "$detail" >"$SANITY_RUN_DIR/workflows/qa-${dept}.json"

  case "$qa_status" in
    ok)        ok "$label: answer generated (status=ok)" ;;
    completed) ok "$label: answer generated (status=completed)" ;;
    degraded)  warn "$label: answer degraded (model offline / fallback)" ;;
    *)         fail "$label: Q&A ended status='${qa_status:-none}'" ;;
  esac

  if [ -n "$qa_answer" ] && [ "${#qa_answer}" -ge 20 ]; then
    ok "$label: non-empty grounded answer (${#qa_answer} chars)"
  else
    fail "$label: empty / too-short Q&A answer"
  fi

  # Research run / DAG must stay untouched.
  last_run_after=$(vc_json_or "$detail" "" -r '.workflow.last_run_id // empty')
  if [ "$last_run_after" = "$last_run_before" ]; then
    ok "$label: last_run_id unchanged"
  else
    fail "$label: last_run_id changed by Q&A ($last_run_before → $last_run_after)"
  fi
  subagents_after=$(count_subagents "$detail")
  if [ "${subagents_after:-0}" -eq "${subagents_before:-0}" ]; then
    ok "$label: no new sub-agents (${subagents_after} unchanged)"
  else
    fail "$label: sub-agent count changed ($subagents_before → $subagents_after)"
  fi
  qa_count=$(vc_json_or "$detail" "0" '[.qa_messages[]?] | length')
  if [ "${qa_count:-0}" -ge 1 ]; then
    ok "$label: qa_messages present in detail ($qa_count)"
  else
    fail "$label: no qa_messages in detail"
  fi
}

test_delete_workflow() {
  local dept="$1" user="$2" pass="$3" wf="$4" other_user="$5" other_pass="$6"
  local ns token other_token code detail deleted label i
  local pvc_before pvc_after sb_left listed
  ns=$(dept_ns "$dept")
  label="delete/$dept"
  echo ""
  echo -e "${YELLOW}━━ $label (workflow $wf) ━━${NC}"

  token=$(login "$user" "$pass") || return 1
  pvc_before=$(workflow_pvc_count "$ns" "$wf")

  if [ -n "$other_user" ]; then
    other_token=$(login "$other_user" "$other_pass") || other_token=""
    if [ -n "$other_token" ]; then
      code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X DELETE \
        -H "Authorization: Bearer $other_token" \
        "$BASE/api/v1/workflows/$wf" || echo "000")
      case "$code" in
        403|404) ok "$label: non-owner ($other_user) delete refused (HTTP $code)" ;;
        *)       fail "$label: non-owner delete returned HTTP $code (expected 403/404)" ;;
      esac
      code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
        -H "Authorization: Bearer $token" \
        "$BASE/api/v1/workflows/$wf/detail" || echo "000")
      if [ "$code" = "200" ]; then
        ok "$label: workflow intact after refused delete"
      else
        fail "$label: owner cannot read workflow after refused delete (HTTP $code)"
      fi
    fi
  fi

  # (b) Owner deletes it — expect a JSON DeleteWorkflowResult with deleted=true.
  detail=$(api DELETE "/api/v1/workflows/$wf" "$token")
  if ! vc_is_json "$detail"; then
    fail "$label: delete returned non-JSON: $(printf '%s' "$detail" | head -c 120)"
    return 1
  fi
  [ -n "$detail" ] && printf '%s' "$detail" >"$SANITY_RUN_DIR/workflows/delete-${dept}.json"
  deleted=$(vc_json_or "$detail" "false" -r '.deleted // false')
  if [ "$deleted" = "true" ]; then
    ok "$label: owner delete accepted (pvcs=$(vc_json_or "$detail" 0 '[.pvcs[]?]|length') snapshots=$(vc_json_or "$detail" 0 '[.snapshots[]?]|length') sandboxes=$(vc_json_or "$detail" 0 '[.sandboxes[]?]|length'))"
  else
    fail "$label: delete response deleted!=true"
    return 1
  fi

  # Control rows gone: detail 404 + absent from the workflow list.
  code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer $token" "$BASE/api/v1/workflows/$wf/detail" || echo "000")
  if [ "$code" = "404" ]; then
    ok "$label: detail now 404 (control rows gone)"
  else
    fail "$label: detail returned HTTP $code after delete (expected 404)"
  fi
  listed=$(api GET /api/v1/workflows "$token")
  if vc_is_json "$listed"; then
    if printf '%s' "$listed" | command jq -e --arg w "$wf" 'any(.[]?; .workflow_id == $w)' >/dev/null 2>&1; then
      fail "$label: workflow still present in the list after delete"
    else
      ok "$label: absent from workflow list"
    fi
  fi

  # PVCs reaped (async — the CSI/PV teardown lags the API).
  if [ "${pvc_before:-0}" -ge 1 ]; then
    pvc_after=999
    for i in $(seq 1 $((PVC_REAP_SECS / 2))); do
      pvc_after=$(workflow_pvc_count "$ns" "$wf")
      [ "${pvc_after:-0}" -eq 0 ] && break
      sleep 2
    done
    if [ "${pvc_after:-0}" -eq 0 ]; then
      ok "$label: all PVCs removed (had $pvc_before)"
    else
      fail "$label: $pvc_after PVC(s) still present ~${PVC_REAP_SECS}s after delete (had $pvc_before)"
    fi
  else
    warn "$label: no PVCs present before delete (nothing to verify reaped)"
  fi

  # Agent-Sandbox CRs gone (best-effort — sleep/wake may have swept them already).
  sb_left=$(kubectl -n "$ns" get sandboxes.agents.x-k8s.io \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
    | grep -c "^${wf}" || true)
  if [ "${sb_left:-0}" -eq 0 ]; then
    ok "$label: no Agent-Sandbox CRs remain for $wf"
  else
    warn "$label: $sb_left sandbox CR(s) still present for $wf (controller finalizing?)"
  fi
}

DIAG_ISSUE_RE='traceback|error|critical|exception|failed|failure|refused|denied|timed out|timeout|too many requests|not_allowed|unavailable|crashloop'
DIAG_NOISE_RE='Using the number of endpoints|GET /healthz|HTTP/1\.1" (2|3)[0-9][0-9]|Processing request of type|POST /messages/|GET /sse'

diag_signature() {
  sed -E \
    -e 's/ for [a-z0-9][a-z0-9-]{2,} \(/ for <agent> (/g' \
    -e 's/wf-[0-9a-f]{6,}[a-z0-9-]*/wf-<id>/g' \
    -e 's/pvc-[0-9a-f]{8}[0-9a-f-]*/pvc-<id>/g' \
    -e 's/snap[a-z-]*-[0-9a-f-]{6,}/snap-<id>/g' \
    -e 's/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/<uuid>/g' \
    -e 's/[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.]+Z?/<ts>/g' \
    -e 's/[0-9]{2}\/[0-9]{2}\/[0-9]{2} [0-9:]+/<ts>/g' \
    -e 's/[[:space:]]+/ /g' \
    -e 's/^[[:space:]]*//' \
  | cut -c1-150
}

scan_log_stream() {
  # stdin: raw logs → "<count> <signature>" lines, most frequent first
  { grep -Ei "$DIAG_ISSUE_RE" || true; } \
    | { grep -Eiv "$DIAG_NOISE_RE" || true; } \
    | diag_signature \
    | sort | uniq -c | sort -rn | head -n "$DIAG_MAX_SIGNATURES"
}

report_issue_stream() {
  local source_label="$1" found=0 line count sig
  while IFS= read -r line; do
    [ -n "${line// /}" ] || continue
    found=1
    count=$(printf '%s' "$line" | awk '{print $1}')
    sig=$(printf '%s' "$line" | sed -E 's/^[[:space:]]*[0-9]+[[:space:]]+//')
    printf '%s\t%s\t%s\n' "$source_label" "$count" "$sig" >>"$SANITY_RUN_DIR/issues.txt"
    if [ "$DIAG_STRICT" = "1" ]; then
      fail "$source_label: ${count}× $sig"
    else
      warn "$source_label: ${count}× $sig"
    fi
  done
  [ "$found" = "1" ] || ok "$source_label: no errors in logs"
}

collect_cluster_issues() {
  [ "$COLLECT_DIAGNOSTICS" = "1" ] || return 0

  echo ""
  echo -e "${YELLOW}━━ diagnostics: backend + agent logs ━━${NC}"

  if ! kubectl -n "$UI_NS" get deploy "$BACKEND_DEPLOY" >/dev/null 2>&1; then
    warn "diagnostics skipped (no cluster access to $UI_NS/$BACKEND_DEPLOY)"
    return 0
  fi

  local since=$(( $(date +%s) - ${SANITY_START_EPOCH:-0} + 60 ))

  kubectl -n "$UI_NS" logs "deploy/$BACKEND_DEPLOY" --since="${since}s" \
    >"$SANITY_RUN_DIR/backend.log" 2>/dev/null || true
  scan_log_stream <"$SANITY_RUN_DIR/backend.log" | report_issue_stream "backend" || true

  local ns pod name phase unhealthy podlog
  for ns in "${DEPARTMENT_NS[@]}"; do
    kubectl get ns "$ns" >/dev/null 2>&1 || continue

    unhealthy=0
    while read -r name phase; do
      [ -n "$name" ] || continue
      case "$phase" in
        Running|Succeeded) ;;
        *) unhealthy=$((unhealthy + 1)); warn "$ns: agent pod $name is $phase" ;;
      esac
    done < <(kubectl -n "$ns" get pods -l vastclaw.vastdata.com/workflow \
      -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' 2>/dev/null || true)
    [ "$unhealthy" -eq 0 ] && ok "$ns: no unhealthy agent pods"

    while read -r pod; do
      [ -n "$pod" ] || continue
      name="${pod##pod/}"
      podlog="$SANITY_RUN_DIR/agents/${ns}__${name}.log"
      kubectl -n "$ns" logs "$pod" --since="${since}s" --all-containers \
        >"$podlog" 2>/dev/null || true
      # Keep only logs that actually contain something worth reading.
      if [ ! -s "$podlog" ]; then
        rm -f "$podlog"
        continue
      fi
      scan_log_stream <"$podlog" | report_issue_stream "$ns/$name" || true
    done < <(kubectl -n "$ns" get pods -l vastclaw.vastdata.com/workflow \
      -o name 2>/dev/null || true)
  done
}

analyze_retries() {
  local log="$SANITY_RUN_DIR/backend.log" out="$SANITY_RUN_DIR/retries.txt"
  [ -s "$log" ] || return 0

  local snap_retried snap_lost nim_retries nim_fallback os_create os_exec
  snap_retried=$(grep -Eo 'snapshot of [a-z0-9-]+ rejected by VMS' "$log" 2>/dev/null \
    | awk '{print $3}' | sort -u || true)
  snap_lost=$(grep -Eo 'VolumeSnapshot of [a-z0-9-]+ failed after' "$log" 2>/dev/null \
    | awk '{print $3}' | sort -u || true)
  nim_retries=$(grep -Ec 'NIM (chat completion|embeddings) (got HTTP|failed)' "$log" 2>/dev/null || true)
  nim_fallback=$(grep -Ec 'via NIM failed, using fallback' "$log" 2>/dev/null || true)
  os_create=$(grep -Ec 'OpenShell CreateSandbox.*failed|sandbox .* entered error phase|SandboxError|openshell sandbox .* was not ready' "$log" 2>/dev/null || true)
  os_exec=$(grep -Ec 'OpenShell exec failed|write .*\.vastclaw/config\.yaml failed|sub-agent .* spawn failed' "$log" 2>/dev/null || true)

  local n_retried n_lost n_recovered
  n_retried=$(printf '%s\n' "$snap_retried" | grep -c . || true)
  n_lost=$(printf '%s\n' "$snap_lost" | grep -c . || true)
  n_recovered=$((n_retried - n_lost))
  [ "$n_recovered" -lt 0 ] && n_recovered=0

  {
    echo "RETRY OUTCOMES"
    echo "=============="
    echo ""
    echo "VolumeSnapshot / VMS (503 SnapshotResultCode.NOT_ALLOWED)"
    echo "  volumes that hit a rejection : $n_retried"
    echo "  recovered on retry           : $n_recovered"
    echo "  lost after all retries       : $n_lost"
    if [ "$n_lost" -gt 0 ]; then
      echo "  lost volumes:"
      printf '%s\n' "$snap_lost" | sed '/^$/d; s/^/    - /'
    fi
    echo ""
    echo "NIM / NGC model calls"
    echo "  retry attempts logged        : ${nim_retries:-0}"
    echo "  synthesis fell back to local : ${nim_fallback:-0}"
    echo ""
    echo "OpenShell spawn"
    echo "  CreateSandbox / SandboxError : ${os_create:-0}"
    echo "  exec / config-write failures : ${os_exec:-0}"
    echo ""
    echo "Verdict"
    if [ "$n_lost" -eq 0 ] && [ "${nim_fallback:-0}" -eq 0 ] \
      && [ "${os_create:-0}" -eq 0 ]; then
      if [ "$n_retried" -gt 0 ] || [ "${nim_retries:-0}" -gt 0 ] || [ "${os_exec:-0}" -gt 0 ]; then
        echo "  transient failures occurred and were absorbed (or were teardown exec noise)."
      else
        echo "  no transient failures at all."
      fi
    else
      echo "  retries did NOT fully absorb the failures — see lost volumes / fallbacks / OpenShell above."
      echo "  next step: lower sandbox.max_concurrent_snapshots, or raise"
      echo "  snapshot_max_retries / model.max_retries in workflow-ui.config.yaml."
    fi
  } >"$out"

  echo ""
  echo -e "${YELLOW}━━ retry outcomes ━━${NC}"
  sed 's/^/  /' "$out" >&2

  if [ "$n_lost" -gt 0 ]; then
    warn "$n_lost checkpoint(s) lost after exhausting retries"
  elif [ "$n_retried" -gt 0 ]; then
    ok "all $n_retried rejected snapshot(s) recovered on retry"
  fi
  if [ "${nim_fallback:-0}" -gt 0 ]; then
    warn "${nim_fallback} synthesis call(s) fell back after exhausting model retries"
  elif [ "${nim_retries:-0}" -gt 0 ]; then
    ok "all ${nim_retries} NIM retry attempt(s) eventually succeeded"
  fi
  if [ "${os_create:-0}" -gt 0 ]; then
    fail "OpenShell CreateSandbox failures in backend.log (create=${os_create})"
  elif [ "${os_exec:-0}" -gt 0 ]; then
    warn "OpenShell exec/config-write noise in backend.log (exec=${os_exec}) — usually teardown race"
  fi
}

capture_k8s_state() {
  local out="$SANITY_RUN_DIR/k8s-state.txt"
  kubectl -n "$UI_NS" get deploy "$BACKEND_DEPLOY" >/dev/null 2>&1 || return 0
  {
    echo "### backend pods"
    kubectl -n "$UI_NS" get pods -o wide 2>&1 || true
    local ns
    for ns in "${DEPARTMENT_NS[@]}"; do
      kubectl get ns "$ns" >/dev/null 2>&1 || continue
      echo ""
      echo "### $ns pods"
      kubectl -n "$ns" get pods -o wide 2>&1 || true
      echo ""
      echo "### $ns openshell"
      kubectl -n "$ns" get pods,svc -l app.kubernetes.io/name=openshell -o wide 2>&1 || true
      echo ""
      echo "### $ns sandboxes (Agent-Sandbox CRs)"
      kubectl -n "$ns" get sandboxes.agents.x-k8s.io -o wide 2>&1 || true
      echo ""
      echo "### $ns pvcs"
      kubectl -n "$ns" get pvc 2>&1 || true
      echo ""
      echo "### $ns volumesnapshots"
      kubectl -n "$ns" get volumesnapshot 2>&1 || true
      echo ""
      echo "### $ns sandboxes"
      kubectl -n "$ns" get sandboxes 2>&1 || true
    done
  } >"$out" 2>&1

  # Ground truth for the retry verdict: a checkpoint only counts if it is ready.
  local total ready
  total=$(kubectl get volumesnapshot -A --no-headers 2>/dev/null | grep -c . || true)
  ready=$(kubectl get volumesnapshot -A --no-headers 2>/dev/null | grep -c ' true ' || true)
  if [ "${total:-0}" -gt 0 ]; then
    if [ "${ready:-0}" -eq "${total:-0}" ]; then
      ok "VolumeSnapshots: ${ready}/${total} ready"
    else
      warn "VolumeSnapshots: only ${ready}/${total} ready (see k8s-state.txt)"
    fi
  fi
}

write_run_summary() {
  {
    echo "vast-claw sanity — $(date)"
    echo "base:     $BASE"
    echo "duration: $(( $(date +%s) - ${SANITY_START_EPOCH:-0} ))s"
    echo "workflows_count_per_dep: $WORKFLOWS_COUNT_PER_DEP  ($TOTAL_TASKS total)"
    echo ""
    echo "passed:   $PASS_COUNT"
    echo "failed:   $FAIL_COUNT"
    echo "warnings: $WARN_COUNT"
    echo ""
    echo "workflows:"
    if [ -s "$SANITY_RUN_DIR/workflows.txt" ]; then
      sed 's/^/  /' "$SANITY_RUN_DIR/workflows.txt"
    fi
    echo ""
    echo "artifacts:"
    echo "  console.log    full run output"
    echo "  backend.log    backend pod logs for the run window"
    echo "  agents/        per agent-pod logs"
    echo "  workflows/     final detail JSON per workflow"
    echo "  issues.txt     deduped error signatures per source"
    echo "  retries.txt    whether transient failures were absorbed by retries"
    echo "  k8s-state.txt  pods / pvcs / volumesnapshots / sandboxes at end of run"
  } >"$SANITY_RUN_DIR/summary.txt"
}

openclaw_response_failed() {
  local label="$1" detail="$2" lines
  if [ -n "${SANITY_STATS_DIR:-}" ] && [ -f "$SANITY_STATS_DIR/openclaw.abort" ]; then
    log "$label: stopped — another workflow's OpenClaw call already failed"
    return 0
  fi
  lines=$(vc_json_or "$detail" "" -r '
    [.graph.nodes[]?
     | select(
         (.attributes.tool // "") == "openclaw-failed"
         or (.attributes.engine // "") == "openclaw-failed"
         or ((.attributes.data_produced // "") | contains("OpenClaw failed"))
       )
     | "\(.agent_name): \((.attributes.data_produced // "OpenClaw failed")[0:240])"
    ] | .[]
  ')
  [ -n "$lines" ] || return 1
  if [ -n "${SANITY_STATS_DIR:-}" ]; then
    printf '%s\n' "$$" > "$SANITY_STATS_DIR/openclaw.abort.pid"
    printf '%s\n' "$lines" > "$SANITY_STATS_DIR/openclaw.abort"
  fi
  fail "$label: OpenClaw response failed"
  printf '%s\n' "$lines" >&2
  return 0
}

validate_agent_health() {
  local label="$1" detail="$2"
  local degraded

  degraded=$(vc_json_or "$detail" "0" \
    '[.graph.nodes[]? | select((.status // "") == "degraded" or (.status // "") == "error")] | length')
  if [ "${degraded:-0}" -gt 0 ]; then
    warn "$label: $degraded agent node(s) degraded/error"
    vc_json "$detail" -r '
      .graph.nodes[]?
      | select((.status // "") == "degraded" or (.status // "") == "error")
      | "      ↳ \(.agent_name) [\(.status)]: \((.attributes.synthesis_error // .attributes.log // .attributes.decision // "no detail")[0:140])"
    ' 2>/dev/null >&2 || true
  else
    ok "$label: all agent nodes healthy"
  fi

  if vc_json "$detail" -e \
    '[.graph.nodes[]? | select((.attributes.synthesis_degraded // "") == "true")] | length > 0' \
    >/dev/null 2>&1; then
    warn "$label: synthesis used offline fallback (model rate-limited or unavailable)"
  fi

  # Report built from fewer workstreams than were planned.
  local coverage
  coverage=$(vc_json_or "$detail" "" -r '
    [.graph.nodes[]? | select((.attributes.coverage_complete // "") == "false")
     | "\(.attributes.coverage_contributed)/\(.attributes.coverage_planned) workstream(s), missing: \(.attributes.coverage_missing_json)"] | .[0] // empty
  ')
  if [ -n "$coverage" ]; then
    warn "$label: incomplete coverage — $coverage"
  fi
}

run_task() {
  local user="$1" pass="$2" dept="$3" title="$4" prompt="$5" min_matches="$6"
  shift 6
  local -a expected=("$@")
  local token wf detail label
  label="${dept}/${title}"

  echo ""
  echo -e "${YELLOW}━━ $label ━━${NC}"
  log "login as $user"
  token=$(login "$user" "$pass") || return 1
  me=$(api GET /api/v1/me "$token")
  got_dept=$(vc_json_or "$me" "" -r .department)
  [ "$got_dept" = "$dept" ] && ok "identity: $user → department=$dept" || {
    fail "identity: expected department=$dept, got $got_dept"; return 1; }

  log "create workflow: $title"
  detail=$(api POST /api/v1/workflows "$token" "$(command jq -nc --arg t "$title" --arg p "$prompt" \
    '{title:$t,prompt:$p,auto_approve_plan:true}')")
  if ! vc_is_json "$detail"; then
    fail "$label: create workflow returned non-JSON"
    return 1
  fi
  wf=$(vc_json "$detail" -r .workflow.workflow_id 2>/dev/null || echo "")
  [ -n "$wf" ] && [ "$wf" != "null" ] || { fail "$label: no workflow_id"; return 1; }
  ok "created workflow $wf"
  printf '%s\t%s\t%s\n' "$wf" "${SANITY_TASK_ID:-$dept}" "$label" \
    >>"$SANITY_RUN_DIR/workflows.txt"
  if [ -n "$SANITY_STATS_DIR" ] && [ "${SANITY_TASK_ID:-}" = "sales-delete" ]; then
    echo "$wf" >> "$SANITY_STATS_DIR/sales_delete_wf_ids"
  fi

  local poll_rc=0
  detail=$(poll_workflow "$wf" "$token" "$label") || poll_rc=$?
  [ "$poll_rc" -eq 0 ] || detail=$(api GET "/api/v1/workflows/$wf/detail" "$token" || true)
  if [ -n "$detail" ]; then
    printf '%s' "$detail" >"$SANITY_RUN_DIR/workflows/${SANITY_TASK_ID:-$dept}.json"
  fi
  [ "$poll_rc" -eq 0 ] || return 1
  validate_task_quality "$label" "$detail" "$min_matches" "${expected[@]}" || true
  validate_agent_health "$label" "$detail" || true
  validate_persona "$dept" "$label" "$detail" || true
  if [ "${SANITY_TASK_ID:-}" = "$MULTILINE_TASK_ID" ]; then
    validate_multiline_spawn "$label" "$detail" || true
  fi
  validate_storage "$label" "$detail" || true

  # Record workflow ids for post-run phases (ACL / sleep-wake).
  if [ -n "$SANITY_STATS_DIR" ]; then
    case "$dept" in
      sales)
        if [ "${SANITY_TASK_ID:-}" != "sales-delete" ]; then
          echo "$wf" >> "$SANITY_STATS_DIR/sales_wf_ids"
        fi
        ;;
      finance) echo "$wf" >> "$SANITY_STATS_DIR/finance_wf_ids" ;;
    esac
  fi
}

start_task() {
  local slug="$1" label="$2"
  shift 2
  (
    export TASK_LABEL="$label" SANITY_TASK_ID="$slug" SANITY_STATS_DIR
    run_task "$@"
  )
}


SALES_TASK_A_TITLE="GE industrial outlook — demand & competition"
SALES_TASK_A_PROMPT='For General Electric'\''s Q1 2020 earnings call, what did management and analysts say about
industrial orders, aviation recovery, competitive pressure, and demand outlook?
Per theme, cite the concern or signal and the single highest-impact next step
for an account team covering GE.'

# 2. Healthcare-sector cluster — Illumina / Dentsply / Affimed demand & pipeline themes
SALES_TASK_B_TITLE="Healthcare earnings — demand & pipeline signals"
SALES_TASK_B_PROMPT='Across Healthcare-sector earnings calls (especially Illumina, Dentsply Sirona, and Affimed), what demand, pipeline, competitive, or product themes appear? Per company, list the top signal already demonstrated, the biggest remaining risk, and the one sales follow-up to advance the account narrative.'

# 3. Financials — AmEx / Travelers / Santander sentiment for sales talking points
SALES_TASK_C_TITLE="Financials — AmEx vs Travelers talking points"
SALES_TASK_C_PROMPT='Compare American Express, Travelers Companies, and Banco Santander earnings commentary on customer spending, credit risk, and competitive positioning. Rank the top themes by urgency for a financial-services sales team and give the top 3 talking points for each company.'

SALES_DELETE_TITLE="American Express — spending & credit signals"
SALES_DELETE_PROMPT='From the American Express Q1 2020 earnings call, identify the strongest customer-spending, credit-risk, competitive-positioning, and demand signals. Build three concise account talking points, each grounded in cited call evidence, and recommend the next sales action.'

# 4. GE — guidance / margin / capital allocation for finance
FINANCE_TASK_A_TITLE="GE earnings — guidance & margin risk"
FINANCE_TASK_A_PROMPT='In General Electric'\''s earnings call, where did participants discuss guidance, margin, cost actions, capital allocation, or revenue timing that affect deal economics? Flag margin and revenue-timing risks and the one commercial action finance should take before engaging the account.'

# 5. Consumer Goods — pricing power / input costs (Hershey, Yeti, Pilgrim'\''s)
FINANCE_TASK_B_TITLE="Consumer Goods — pricing power & cost risk"
FINANCE_TASK_B_PROMPT='For Consumer Goods earnings calls (e.g. Hershey, Yeti Holdings, Pilgrim'\''s Pride), extract pricing, input-cost, and margin commentary. Rank companies by pricing-power risk and recommend one finance mitigation per company.'

# 6. Financial sector — credit quality / expense / capital return
FINANCE_TASK_C_TITLE="Financials — credit & expense watch-items"
FINANCE_TASK_C_PROMPT='Across Financial-sector calls (American Express, Travelers, Banco Santander, Healthcare Realty), summarize credit quality, expense discipline, and capital-return themes. Cite the highest-impact finance watch-item per company and one commercial mitigation.'

SALES_A_EXPECT=(electric industrial aviation competitive outlook "next step")
SALES_A_MIN=3
SALES_B_EXPECT=(illumina dentsply healthcare demand pipeline risk)
SALES_B_MIN=3
SALES_C_EXPECT=(express travelers santander credit competitive spending)
SALES_C_MIN=3
SALES_DELETE_EXPECT=(express spending credit competitive demand sales)
SALES_DELETE_MIN=3
FINANCE_A_EXPECT=(electric guidance margin capital revenue commercial)
FINANCE_A_MIN=3
FINANCE_B_EXPECT=(hershey yeti pricing margin cost consumer)
FINANCE_B_MIN=3
FINANCE_C_EXPECT=(express travelers credit expense capital santander)
FINANCE_C_MIN=3

# ── Main ──────────────────────────────────────────────────────────────────────

SANITY_START_EPOCH=$(date +%s)

SANITY_RUNS_ROOT="$_SCRIPT_DIR/sanity-runs"
mkdir -p "$SANITY_RUNS_ROOT"
SANITY_RUN_DIR="$SANITY_RUNS_ROOT/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$SANITY_RUN_DIR/agents" "$SANITY_RUN_DIR/workflows"
export SANITY_RUN_DIR
ln -sfn "$SANITY_RUN_DIR" "$SANITY_RUNS_ROOT/latest"
exec > >(tee >(sed -E $'s/\033\\[[0-9;]*m//g' >"$SANITY_RUN_DIR/console.log")) 2>&1

echo "🧪 vast-claw sanity — $BASE" >&2
echo "   timeout=${TIMEOUT_SECS}s  qa=${QA_TIMEOUT_SECS}s  poll=${POLL_SECS}s  mode=parallel" >&2
echo "   workflows_count_per_dep=$WORKFLOWS_COUNT_PER_DEP  →  $TOTAL_TASKS workflow(s) total" >&2
echo "   results → $SANITY_RUN_DIR" >&2
echo "   (prior runs kept under $SANITY_RUNS_ROOT/)" >&2
echo "" >&2

SANITY_STATS_DIR=""
_sanity_on_exit() {
  rm -f "$SANITY_PID_FILE"
  kill $(jobs -p) 2>/dev/null || true
  # Only the ephemeral per-run stats tmpdir — never the timestamped SANITY_RUN_DIR.
  [ -n "${SANITY_STATS_DIR:-}" ] && rm -rf "$SANITY_STATS_DIR"
  # Must end truthy: bash propagates the EXIT trap's status as the script's.
  return 0
}

if [ -f "$SANITY_PID_FILE" ]; then
  old_pid="$(cat "$SANITY_PID_FILE" 2>/dev/null || true)"
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    echo -e "${YELLOW}⚠ another sanity run is active (pid $old_pid) — stop it before cleanup/deploy: ./deploy/vast-claw-sanity.sh stop${NC}" >&2
    exit 1
  fi
fi
echo $$ >"$SANITY_PID_FILE"
trap '_sanity_on_exit' EXIT INT TERM

log "health check"
code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$BASE/" 2>/dev/null || echo "000")
if [ "$code" = "200" ]; then
  ok "GET / (frontend reachable at $BASE)"
else
  fail "GET / returned HTTP $code — check /etc/hosts for $BASE and that ingress is up"
  if [ "$code" = "000" ] && [ "$INSECURE" = "0" ]; then
    echo -e "${YELLOW}  hint: TLS verify failed? try INSECURE=1 in CONFIG, or fix cert/host${NC}"
  fi
  exit 1
fi

# ── OpenShell / NemoClaw preflight (hard fail — only spawn path) ─────────────
preflight_openshell() {
  log "OpenShell + NemoClaw preflight"
  local missing=0 ns dept ready want svc cm keys be_pod cfg_out
  local gw_svc gw_svc_map="" tmpl expect actual entry

  if ! command -v kubectl >/dev/null 2>&1; then
    fail "kubectl required for OpenShell preflight"
    return 1
  fi
  if ! kubectl cluster-info >/dev/null 2>&1; then
    fail "kubectl cannot reach the cluster (set KUBECONFIG)"
    return 1
  fi

  if kubectl get crd sandboxes.agents.x-k8s.io >/dev/null 2>&1; then
    ok "CRD sandboxes.agents.x-k8s.io present"
  else
    fail "CRD sandboxes.agents.x-k8s.io missing — run ./deploy/openshell/agent-sandbox-controller.sh"
    missing=1
  fi

  for ns in "${DEPARTMENT_NS[@]}"; do
    dept="${ns#vastclaw-}"

    if ! kubectl get ns "$ns" >/dev/null 2>&1; then
      fail "namespace $ns missing"
      missing=1
      continue
    fi

    if kubectl -n "$ns" get configmap vastclaw-nemoclaw-blueprint >/dev/null 2>&1; then
      if [ -n "$(kubectl -n "$ns" get configmap vastclaw-nemoclaw-blueprint \
        -o jsonpath='{.data.sandbox-policy\.yaml}' 2>/dev/null | head -c 1)" ]; then
        ok "$ns: NemoClaw blueprint ConfigMap (policy present)"
      else
        fail "$ns: vastclaw-nemoclaw-blueprint missing sandbox-policy.yaml — re-run ./deploy/nemoclaw/vast-claw-nemoclaw.sh"
        missing=1
      fi
    else
      fail "$ns: configmap/vastclaw-nemoclaw-blueprint missing — run ./deploy/nemoclaw/vast-claw-nemoclaw.sh"
      missing=1
    fi

    gw_svc="$(kubectl -n "$ns" get svc -l app.kubernetes.io/name=openshell \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    if [ -n "$gw_svc" ]; then
      ok "$ns: OpenShell gateway Service $gw_svc"
      gw_svc_map="$gw_svc_map $dept=$gw_svc"
    else
      fail "$ns: no Service labelled app.kubernetes.io/name=openshell — run ./deploy/openshell/vast-claw-openshell.sh"
      missing=1
    fi

    # Prefer StatefulSet/Deployment ready replicas; fall back to pod READY column.
    want="$(kubectl -n "$ns" get statefulset,deploy -l app.kubernetes.io/name=openshell \
      -o jsonpath='{range .items[*]}{.status.readyReplicas}{"/"}{.status.replicas}{"\n"}{end}' 2>/dev/null || true)"
    if [ -z "$(echo "$want" | tr -d '[:space:]/')" ]; then
      # No labeled workload — check pods directly.
      if ! kubectl -n "$ns" get pods -l app.kubernetes.io/name=openshell \
        --field-selector=status.phase=Running -o name 2>/dev/null | grep -q .; then
        fail "$ns: no Running OpenShell gateway pods — run ./deploy/openshell/vast-claw-openshell.sh"
        missing=1
      else
        ok "$ns: OpenShell gateway Running"
      fi
    else
      if echo "$want" | awk -F/ '$1+0>0 && $1==$2 {ok=1} END{exit !ok}'; then
        ok "$ns: OpenShell gateway Ready ($want)"
      else
        fail "$ns: OpenShell gateway not Ready ($want)"
        kubectl -n "$ns" get pods -l app.kubernetes.io/name=openshell -o wide >&2 || true
        missing=1
      fi
    fi
  done

  be_pod="$(kubectl -n "$UI_NS" get pods -l app.kubernetes.io/name="$BACKEND_DEPLOY" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [ -z "$be_pod" ]; then
    fail "backend pod not found in $UI_NS (deploy/rollout $BACKEND_DEPLOY)"
    missing=1
  else
    if kubectl -n "$UI_NS" exec "$be_pod" -- \
      python -c "import openshell; print(openshell.__version__)" >/dev/null 2>&1; then
      ok "backend has openshell SDK ($be_pod)"
    else
      fail "backend missing openshell package — rebuild/push vc-backend and rollout restart"
      missing=1
    fi
    # -i is required: without it kubectl gives the remote python an empty stdin.
    cfg_out="$(kubectl -n "$UI_NS" exec -i "$be_pod" -- python - <<'PY' 2>&1 || true
from vastclaw_ui.config import Settings
s = Settings.from_yaml()
print("spawn_mode=%s" % getattr(s.agent_sandbox, "spawn_mode", "<removed>"))
print("gateway=%s" % s.agent_sandbox.gateway_endpoint_template)
PY
)"
    if printf '%s' "$cfg_out" | grep -Eqi 'spawn_mode=[[:space:]]*direct'; then
      fail "agent_sandbox.spawn_mode=direct is disabled — remove it from workflow-ui.config.yaml and re-apply the secret"
      missing=1
    elif printf '%s' "$cfg_out" | grep -q 'gateway='; then
      ok "backend agent_sandbox gateway config readable"
      tmpl="$(printf '%s\n' "$cfg_out" | sed -n 's/^gateway=//p' | head -1)"
      for entry in $gw_svc_map; do
        dept="${entry%%=*}"
        actual="${entry#*=}"
        expect="$(printf '%s' "$tmpl" \
          | sed -e "s/{department}/$dept/g" -e "s/{ns}/vastclaw-$dept/g")"
        case "$expect" in
          "$actual".vastclaw-"$dept".svc.cluster.local:*) ;;
          *)
            fail "gateway_endpoint_template renders '$expect' but the Service is '$actual' in vastclaw-$dept"
            missing=1
            ;;
        esac
      done
    else
      warn "could not verify agent_sandbox config in backend: $cfg_out"
    fi
  fi

  if [ "$missing" -ne 0 ]; then
    echo "" >&2
    echo -e "${RED}OpenShell preflight failed — fix the items above before sanity can exercise the real spawn path.${NC}" >&2
    echo -e "${YELLOW}  ./deploy/openshell/agent-sandbox-controller.sh${NC}" >&2
    echo -e "${YELLOW}  ./deploy/nemoclaw/vast-claw-nemoclaw.sh${NC}" >&2
    echo -e "${YELLOW}  ./deploy/openshell/vast-claw-openshell.sh${NC}" >&2
    echo -e "${YELLOW}  rebuild vc-backend (openshell dep) + refresh config secret + rollout backend${NC}" >&2
    exit 1
  fi
  echo "" >&2
}

preflight_openshell

# SANITY_PREFLIGHT_ONLY=1 checks the OpenShell wiring without burning a full run.
if [ "${SANITY_PREFLIGHT_ONLY:-0}" = "1" ]; then
  echo -e "${GREEN}✓ preflight only — OpenShell wiring verified, skipping workflows${NC}"
  exit 0
fi

SANITY_STATS_DIR=$(mktemp -d)

echo ""
log "starting $TOTAL_TASKS workflow(s) in parallel ($WORKFLOWS_COUNT_PER_DEP per dept + dedicated sales delete target)"
declare -a pids=()

if [ "$WORKFLOWS_COUNT_PER_DEP" -ge 1 ]; then
  start_task sales-a "sales/$SALES_TASK_A_TITLE" \
    "$SALES_USER" "$SALES_PASS" sales "$SALES_TASK_A_TITLE" "$SALES_TASK_A_PROMPT" \
    "$SALES_A_MIN" "${SALES_A_EXPECT[@]}" &
  pids+=($!)
  start_task finance-a "finance/$FINANCE_TASK_A_TITLE" \
    "$FINANCE_USER" "$FINANCE_PASS" finance "$FINANCE_TASK_A_TITLE" "$FINANCE_TASK_A_PROMPT" \
    "$FINANCE_A_MIN" "${FINANCE_A_EXPECT[@]}" &
  pids+=($!)
fi
if [ "$WORKFLOWS_COUNT_PER_DEP" -ge 2 ]; then
  start_task sales-b "sales/$SALES_TASK_B_TITLE" \
    "$SALES_USER" "$SALES_PASS" sales "$SALES_TASK_B_TITLE" "$SALES_TASK_B_PROMPT" \
    "$SALES_B_MIN" "${SALES_B_EXPECT[@]}" &
  pids+=($!)
  start_task finance-b "finance/$FINANCE_TASK_B_TITLE" \
    "$FINANCE_USER" "$FINANCE_PASS" finance "$FINANCE_TASK_B_TITLE" "$FINANCE_TASK_B_PROMPT" \
    "$FINANCE_B_MIN" "${FINANCE_B_EXPECT[@]}" &
  pids+=($!)
fi
if [ "$WORKFLOWS_COUNT_PER_DEP" -ge 3 ]; then
  start_task sales-c "sales/$SALES_TASK_C_TITLE" \
    "$SALES_USER" "$SALES_PASS" sales "$SALES_TASK_C_TITLE" "$SALES_TASK_C_PROMPT" \
    "$SALES_C_MIN" "${SALES_C_EXPECT[@]}" &
  pids+=($!)
  start_task finance-c "finance/$FINANCE_TASK_C_TITLE" \
    "$FINANCE_USER" "$FINANCE_PASS" finance "$FINANCE_TASK_C_TITLE" "$FINANCE_TASK_C_PROMPT" \
    "$FINANCE_C_MIN" "${FINANCE_C_EXPECT[@]}" &
  pids+=($!)
fi

start_task sales-delete "sales/$SALES_DELETE_TITLE" \
  "$SALES_USER" "$SALES_PASS" sales "$SALES_DELETE_TITLE" "$SALES_DELETE_PROMPT" \
  "$SALES_DELETE_MIN" "${SALES_DELETE_EXPECT[@]}" &
pids+=($!)

task_failures=0
while true; do
  if [ -f "$SANITY_STATS_DIR/openclaw.abort.pid" ]; then
    _keep_pid=$(cat "$SANITY_STATS_DIR/openclaw.abort.pid" 2>/dev/null || true)
    for pid in "${pids[@]}"; do
      [ "$pid" = "${_keep_pid:-}" ] && continue
      kill "$pid" 2>/dev/null || true
    done
    break
  fi
  _alive=0
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      _alive=1
      break
    fi
  done
  [ "$_alive" -eq 0 ] && break
  sleep 2
done
for pid in "${pids[@]}"; do
  wait "$pid" || task_failures=$((task_failures + 1))
done

aggregate_parallel_stats

if [ -f "$SANITY_STATS_DIR/openclaw.abort" ]; then
  echo ""
  echo -e "${RED}✗ OpenClaw response failed — stopping before ACL, sleep/wake, Q&A, and delete${NC}"
  sed 's/^/  /' "$SANITY_STATS_DIR/openclaw.abort" >&2
  write_run_summary || true
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo -e "${RED}✗ sanity failed${NC} — ${FAIL_COUNT} failed, ${PASS_COUNT} passed, ${WARN_COUNT} warning(s)"
  echo "  results: $SANITY_RUN_DIR  (also: deploy/sanity-runs/latest)"
  exit 1
fi

LAST_SALES_WF=""
LAST_FINANCE_WF=""
if [ -f "$SANITY_STATS_DIR/sales_wf_ids" ]; then
  LAST_SALES_WF=$(head -1 "$SANITY_STATS_DIR/sales_wf_ids")
fi
if [ -f "$SANITY_STATS_DIR/finance_wf_ids" ]; then
  LAST_FINANCE_WF=$(head -1 "$SANITY_STATS_DIR/finance_wf_ids")
fi

if [ "$SKIP_ACL" != "1" ] && [ -n "$LAST_SALES_WF" ]; then
  echo ""
  echo -e "${YELLOW}━━ ACL cross-check ━━${NC}"
  fin_token=$(login "$FINANCE_USER" "$FINANCE_PASS") || true
  if [ -n "${fin_token:-}" ]; then
    code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' \
      -H "Authorization: Bearer $fin_token" \
      "$BASE/api/v1/workflows/$LAST_SALES_WF/detail" || echo "000")
    if [ "$code" = "404" ]; then
      ok "finance cannot read sales workflow $LAST_SALES_WF (HTTP 404)"
    else
      fail "ACL leak? finance got HTTP $code for sales workflow $LAST_SALES_WF (expected 404)"
    fi
  fi
fi

# Sleep/wake: force-delete sandboxes, continue → remount same head PVC.
if [ "$SKIP_SLEEP_WAKE" != "1" ] && [ -n "$LAST_SALES_WF" ]; then
  echo ""
  echo -e "${YELLOW}━━ Sleep / wake (continue remounts PVC) ━━${NC}"
  test_sleep_wake sales "$SALES_USER" "$SALES_PASS" "$LAST_SALES_WF" || true
elif [ "$SKIP_SLEEP_WAKE" = "1" ]; then
  log "sleep/wake check skipped (SKIP_SLEEP_WAKE=1)"
else
  warn "sleep/wake check skipped (no completed sales workflow id)"
fi

# Quick Q&A: ask a question about a finished report (finance workflow preferred).
QA_WF="$LAST_FINANCE_WF"
QA_USER="$FINANCE_USER"
QA_PASS="$FINANCE_PASS"
if [ -z "$QA_WF" ]; then
  QA_WF="$LAST_SALES_WF"; QA_USER="$SALES_USER"; QA_PASS="$SALES_PASS"
fi
if [ "$SKIP_QA" != "1" ] && [ -n "$QA_WF" ]; then
  echo ""
  echo -e "${YELLOW}━━ Quick Q&A ━━${NC}"
  test_followup_qa "${QA_USER%%-*}" "$QA_USER" "$QA_PASS" "$QA_WF" || true
elif [ "$SKIP_QA" = "1" ]; then
  log "Quick Q&A check skipped (SKIP_QA=1)"
else
  warn "Quick Q&A check skipped (no completed workflow id)"
fi

collect_cluster_issues || true
capture_k8s_state || true
analyze_retries || true

DELETE_SALES_WF=""
if [ -f "$SANITY_STATS_DIR/sales_delete_wf_ids" ]; then
  DELETE_SALES_WF=$(head -1 "$SANITY_STATS_DIR/sales_delete_wf_ids")
fi
if [ "$SKIP_DELETE" != "1" ] && [ -n "$DELETE_SALES_WF" ]; then
  echo ""
  echo -e "${YELLOW}━━ Delete workflow (owner ACL + cascade) ━━${NC}"
  test_delete_workflow sales "$SALES_USER" "$SALES_PASS" "$DELETE_SALES_WF" \
    "$FINANCE_USER" "$FINANCE_PASS" || true
elif [ "$SKIP_DELETE" = "1" ]; then
  log "delete check skipped (SKIP_DELETE=1)"
else
  fail "delete check skipped (dedicated sales workflow id was not recorded)"
fi

if [ "$task_failures" -gt 0 ] && [ "$FAIL_COUNT" -eq 0 ]; then
  FAIL_COUNT=$task_failures
fi

write_run_summary || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAIL_COUNT" -eq 0 ]; then
  echo -e "${GREEN}✓ sanity complete${NC} — ${PASS_COUNT} passed, ${WARN_COUNT} warning(s)"
  [ "$WARN_COUNT" -gt 0 ] && echo "  (warnings = weak grounding, degraded agents, or errors found in backend/agent logs)"
else
  echo -e "${RED}✗ sanity failed${NC} — ${FAIL_COUNT} failed, ${PASS_COUNT} passed, ${WARN_COUNT} warning(s)"
fi
echo "  results: $SANITY_RUN_DIR  (also: deploy/sanity-runs/latest)"
echo "  prior runs kept under: $SANITY_RUNS_ROOT/"
[ "$FAIL_COUNT" -eq 0 ] && exit 0 || exit 1
