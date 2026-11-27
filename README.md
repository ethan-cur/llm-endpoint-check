# llm-endpoint-check

**Test any OpenAI-compatible or Anthropic API Base URL from your terminal: status, auth and latency (TTFB).**
Zero dependencies: one Python 3 file (standard library only) plus a curl-only bash script.

Page: https://ethan-cur.github.io/llm-endpoint-check/

Use it when Claude Code, Codex CLI, Cursor, Cline or an OpenAI SDK says "404", "401" or just hangs after you point it at an API relay, LLM gateway or self-hosted proxy (LiteLLM, one-api, new-api, vLLM, Ollama...). It tells you in one run whether the problem is the **path prefix**, the **key**, a **redirect**, an **HTML page instead of JSON**, or the network.

```
$ export LLM_API_KEY=...        # read from the environment, never printed  (illustrative output)
$ python3 llm_endpoint_check.py --openai https://gateway.example.com/v1 \
                                --anthropic https://gateway.example.com --responses

✓ openai GET /models     200     312 ms  20 models
✓ openai POST /chat      200     905 ms  OK
✓ openai POST /responses 200    1104 ms  OK
✗ anthropic POST /v1/messages 404 280 ms  not found: wrong path prefix (missing or doubled /v1?) or unknown model
```

## What it checks

| Check | Request | Used by |
|---|---|---|
| `--openai URL` | `GET {URL}/models`, `POST {URL}/chat/completions` | OpenAI SDKs, Cursor, Cline, most clients |
| `--responses [URL]` | `POST {URL}/responses` | Codex CLI (`wire_api = "responses"`) |
| `--anthropic URL` | `POST {URL}/v1/messages` | Claude Code (`ANTHROPIC_BASE_URL`), Claude Agent SDK |

For each request it reports HTTP status, content type, connect time, time to first byte, and a plain-English verdict. Redirects are **not** followed but reported, because many clients drop the POST body on a redirect.

## Install

```bash
curl -fsSLO https://raw.githubusercontent.com/ethan-cur/llm-endpoint-check/main/llm_endpoint_check.py
python3 llm_endpoint_check.py --help
```

or clone the repo. Python 3.8+ on macOS, Linux or Windows. No `pip install`.

## Usage

```bash
# 1. Without a key: does the endpoint exist? (a good API answers 401 JSON)
python3 llm_endpoint_check.py --no-key --openai https://gateway.example.com/v1

# 2. Full check with a key from $LLM_API_KEY (or --key-env MY_VAR)
python3 llm_endpoint_check.py --openai https://gateway.example.com/v1 --model <model-id>

# 3. Claude Code style (Bearer, like ANTHROPIC_AUTH_TOKEN) or x-api-key (like ANTHROPIC_API_KEY)
python3 llm_endpoint_check.py --anthropic https://gateway.example.com --anthropic-model <claude-model-id>
python3 llm_endpoint_check.py --anthropic https://gateway.example.com --auth x-api-key

# 4. Codex CLI base_url (Codex appends /responses)
python3 llm_endpoint_check.py --responses https://gateway.example.com/v1

# 5. Latency: 5 samples each, median TTFB; JSON for CI or monitoring
python3 llm_endpoint_check.py --openai https://gateway.example.com/v1 --repeat 5 --json
```

Exit code is `0` when every check passes and `1` otherwise, so it works in cron or CI.

Model IDs: if you don't pass `--model`, the script uses the first ID returned by `/models`; with no key it sends a placeholder, which is fine for reachability tests.

### curl-only version

```bash
export LLM_API_KEY=...
./check.sh openai    https://gateway.example.com/v1  [model]
./check.sh anthropic https://gateway.example.com     [model]
./check.sh codex     https://gateway.example.com/v1  [model]
```

## Reading the results

| Result | Meaning | Usual fix |
|---|---|---|
| 200 JSON | Works | – |
| 400 / 422 JSON | Path and key are fine; body or model rejected | Use a model ID the endpoint serves |
| 401 JSON | Key missing or rejected (without a key: the route exists) | Check the key, header type (`Bearer` vs `x-api-key`) |
| 402 / 429 | Quota exhausted / rate limited | Wait or top up |
| 404 | Wrong path prefix | Add or remove `/v1`; Claude Code wants the base **without** `/v1` |
| 301 / 302 | Redirect | Use the final URL (`https` vs `http`, trailing slash) |
| 200 **HTML** | Not an API route, or the gateway shows a landing page when no key is sent | Fix the prefix / send the key |
| unreachable | DNS, TLS, firewall or timeout | Try another network or region endpoint |

## Example: qcode.cc

qcode.cc uses separate prefixes for the two protocols (`/api` for Anthropic, `/openai` for OpenAI) and three hostnames (`api.`, `asia.`, `us.`):

```bash
python3 llm_endpoint_check.py --openai https://api.qcode.cc/openai/v1 \
  --responses https://api.qcode.cc/openai --anthropic https://api.qcode.cc/api
```

Without a key, `/openai/v1/models` returns a JSON 401, and the POST routes return an HTML landing page (by design, per the [qcode docs](https://docs.qcode.cc/docs/getting-started/endpoints-and-api-paths)); with a key every check should be 200/400.

## Privacy and safety

- The key is read from an environment variable, so it doesn't land in shell history.
- It is sent **only** to the Base URLs you pass, and never printed (also redacted from error snippets).
- Each run makes at most a few tiny requests (`max_tokens: 16`).

## Related

- [claude-code-config-generator](https://ethan-cur.github.io/claude-code-config-generator/): generate `ANTHROPIC_BASE_URL`, `settings.json` and Codex `config.toml` for any endpoint.
- [claude-code-custom-base-url](https://ethan-cur.github.io/claude-code-custom-base-url/): guide and troubleshooting table in 5 languages.

## Disclosure

Maintained by the team that operates [qcode.cc](https://qcode.cc), an API relay for Claude Code and Codex CLI. The tool is vendor-neutral and works with any endpoint. Contact: hi@qcode.cc

## License

MIT
