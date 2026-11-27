#!/usr/bin/env bash
# llm-endpoint-check (curl edition): quick status + latency for any LLM Base URL.
# Needs only bash and curl. MIT License. https://github.com/ethan-cur/llm-endpoint-check
#
# Usage:
#   export LLM_API_KEY=...            # optional; never pass keys as arguments
#   ./check.sh openai    https://gateway.example.com/v1   [model]
#   ./check.sh anthropic https://gateway.example.com      [model]
#   ./check.sh codex     https://gateway.example.com/v1   [model]   # POST /responses
set -u
kind="${1:-}"; base="${2:-}"; model="${3:-MODEL_ID}"
if [ -z "$kind" ] || [ -z "$base" ]; then
  sed -n '5,9p' "$0"; exit 2
fi
base="${base%/}"
key="${LLM_API_KEY:-}"
fmt='%{http_code} %{content_type} connect=%{time_connect}s ttfb=%{time_starttransfer}s total=%{time_total}s'

hit() { # method url body [extra header...]
  local method="$1" url="$2" body="$3"; shift 3
  local args=(-sS -o /tmp/llm-endpoint-check.$$ -w "$fmt" -X "$method" -A "llm-endpoint-check-sh/0.1.0" --max-time 60)
  [ -n "$body" ] && args+=(-H "content-type: application/json" --data "$body")
  for h in "$@"; do args+=(-H "$h"); done
  printf '%-6s %s\n       ' "$method" "$url"
  curl "${args[@]}" "$url"; echo
  local snippet; snippet="$(head -c 300 /tmp/llm-endpoint-check.$$ | tr '\n' ' ')"
  [ -n "$key" ] && snippet="${snippet//$key/***}"
  printf '       %s\n\n' "$snippet"
  rm -f /tmp/llm-endpoint-check.$$
}

auth=(); [ -n "$key" ] && auth=("Authorization: Bearer $key")
case "$kind" in
  openai)
    hit GET  "$base/models" "" ${auth[@]+"${auth[@]}"}
    hit POST "$base/chat/completions" "{\"model\":\"$model\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with: pong\"}]}" ${auth[@]+"${auth[@]}"} ;;
  codex)
    hit POST "$base/responses" "{\"model\":\"$model\",\"input\":\"Reply with: pong\",\"max_output_tokens\":16}" ${auth[@]+"${auth[@]}"} ;;
  anthropic)
    hit POST "$base/v1/messages" "{\"model\":\"$model\",\"max_tokens\":16,\"messages\":[{\"role\":\"user\",\"content\":\"Reply with: pong\"}]}" "anthropic-version: 2023-06-01" ${auth[@]+"${auth[@]}"} ;;
  *) echo "kind must be openai, anthropic or codex"; exit 2 ;;
esac
echo "200 = works · 400 = reached API (body/model rejected) · 401 = key missing/invalid · 404 = wrong prefix · HTML = wrong path or no key"
