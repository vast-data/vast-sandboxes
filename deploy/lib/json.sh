#!/bin/bash
# JSON helpers for vast-claw-sanity.sh only (not used by deploy/cleanup).

vc_is_json() {
  [ -n "${1:-}" ] && printf '%s' "$1" | command jq -e . >/dev/null 2>&1
}

vc_json() {
  local blob="$1"
  shift
  vc_is_json "$blob" || return 1
  printf '%s' "$blob" | command jq "$@"
}

vc_json_or() {
  local blob="$1" fallback="$2"
  shift 2
  if vc_is_json "$blob"; then
    printf '%s' "$blob" | command jq "$@"
  else
    printf '%s' "$fallback"
  fi
}
