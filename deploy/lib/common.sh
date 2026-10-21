#!/bin/bash

if [ -n "${VASTCLAW_DEPLOY_LIB_LOADED:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
VASTCLAW_DEPLOY_LIB_LOADED=1

vc_kubectl_is_binary() {
  local bin="$1" kind
  [ -n "$bin" ] && [ -x "$bin" ] || return 1
  kind="$(file -b "$bin" 2>/dev/null || true)"
  case "$kind" in
    *"Mach-O"*|*"ELF"*) return 0 ;;
    *script*|*"text"*) return 1 ;;
  esac
  [ ! -s "$bin" ] && return 1
  head -1 "$bin" 2>/dev/null | grep -q '^#!' && return 1
  return 0
}

vc_resolve_kubectl() {
  local candidate path

  if [ -n "${KUBECTL_BIN:-}" ] && vc_kubectl_is_binary "$KUBECTL_BIN"; then
    echo "$KUBECTL_BIN"
    return 0
  fi

  for candidate in \
    /opt/homebrew/bin/kubectl \
    /usr/local/bin/kubectl; do
    if vc_kubectl_is_binary "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  for candidate in /opt/homebrew/Cellar/kubernetes-cli/*/bin/kubectl \
                   /usr/local/Cellar/kubernetes-cli/*/bin/kubectl; do
    if vc_kubectl_is_binary "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  IFS=':' read -r -a _vc_path_parts <<< "${PATH:-/usr/bin:/bin}"
  for path in "${_vc_path_parts[@]}"; do
    candidate="${path%/}/kubectl"
    if vc_kubectl_is_binary "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  command -v kubectl 2>/dev/null || echo kubectl
}

unalias kubectl 2>/dev/null || true
if declare -F kubectl >/dev/null 2>&1; then
  unset -f kubectl 2>/dev/null || true
fi

KUBECTL_BIN="$(vc_resolve_kubectl)"
kubectl() { "$KUBECTL_BIN" "$@"; }
export KUBECTL_BIN

unset KUBECTL_EXTERNAL_DIFF 2>/dev/null || true

# Value of one key inside a top-level YAML block. Keys we read are unique in that block.
vc_yaml_block_value() {
  awk -v block="$2:" -v k="$3:" '
    /^[A-Za-z_]/ { inb = ($0 ~ ("^" block)) }
    inb && $1 == k { v=$2; gsub(/"/, "", v); print v; exit }
  ' "$1"
}

# "http(s)://Host[:port]/path" -> lowercase host. A bare host is returned as-is.
vc_url_host() {
  local url="$1" rest
  url="${url%\"}"; url="${url#\"}"
  rest="${url#*://}"
  rest="${rest%%/*}"
  rest="${rest%%:*}"
  printf '%s' "$rest" | tr '[:upper:]' '[:lower:]'
}
