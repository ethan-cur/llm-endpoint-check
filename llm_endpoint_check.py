#!/usr/bin/env python3
"""llm-endpoint-check: test any OpenAI-compatible or Anthropic-compatible Base URL.

Zero dependencies (Python 3.8+ standard library only).

Checks (each one is optional and depends on the Base URLs you pass):
  OpenAI   GET  {openai_base}/models
  OpenAI   POST {openai_base}/chat/completions
  OpenAI   POST {openai_base}/responses            (Codex CLI uses this)
  Anthropic POST {anthropic_base}/v1/messages      (Claude Code uses this)

The API key is read from an environment variable (default LLM_API_KEY) so it
does not end up in your shell history. It is sent only to the Base URLs you
pass on the command line and is never printed.

MIT License. https://github.com/ethan-cur/llm-endpoint-check
"""
import argparse
import http.client
import json
import os
import socket
import ssl
import statistics
import sys
import time
from urllib.parse import urlsplit

__version__ = "0.1.0"
UA = "llm-endpoint-check/" + __version__

HINTS = {
    200: "OK",
    201: "OK",
    400: "reached the API (path and auth OK); request body or model rejected",
    401: "key missing or rejected",
    402: "reached the API; quota or balance exhausted",
    403: "forbidden: key lacks access, or IP/region blocked",
    404: "not found: wrong path prefix (missing or doubled /v1?) or unknown model",
    405: "method not allowed: wrong path",
    408: "timeout on the server side",
    413: "request too large",
    422: "reached the API; request body rejected",
    429: "reached the API; rate limited",
    500: "server error",
    502: "bad gateway (upstream down)",
    503: "service unavailable / overloaded",
    504: "gateway timeout",
    529: "upstream overloaded",
}


def request(method, url, headers=None, body=None, timeout=30.0):
    """One HTTP request without redirects. Returns a dict with timings in ms."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://: " + url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    port = parts.port
    if parts.scheme == "https":
        conn = http.client.HTTPSConnection(parts.hostname, port, timeout=timeout,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    res = {"url": url, "method": method}
    t0 = time.perf_counter()
    try:
        conn.connect()
        t1 = time.perf_counter()
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        t2 = time.perf_counter()
        raw = r.read(256 * 1024)
        t3 = time.perf_counter()
        res.update(
            status=r.status,
            reason=r.reason,
            content_type=(r.getheader("Content-Type") or "").split(";")[0].strip(),
            location=r.getheader("Location"),
            connect_ms=(t1 - t0) * 1000,
            ttfb_ms=(t2 - t0) * 1000,
            total_ms=(t3 - t0) * 1000,
            body=raw,
        )
    except (socket.timeout, TimeoutError):
        res.update(status=None, error="timeout after %.0fs" % timeout)
    except ssl.SSLError as e:
        res.update(status=None, error="TLS error: %s" % e)
    except (OSError, http.client.HTTPException) as e:
        res.update(status=None, error="%s: %s" % (type(e).__name__, e))
    finally:
        conn.close()
    return res


def parse_json(res):
    try:
        return json.loads(res.get("body") or b"")
    except (ValueError, UnicodeDecodeError):
        return None


def error_message(obj):
    if not isinstance(obj, dict):
        return None
    err = obj.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("code") or err.get("type")
    if isinstance(err, str):
        return err
    return obj.get("message") or obj.get("detail")


def redact(text, key):
    if key and text:
        text = text.replace(key, "***")
    return text


def interpret(res, kind, key):
    """Return (ok, verdict, detail)."""
    if res.get("status") is None:
        return False, "unreachable", res.get("error")
    st = res["status"]
    ct = res.get("content_type", "")
    obj = parse_json(res)
    if 300 <= st < 400:
        return False, "redirect", "HTTP %d -> %s (clients drop POST bodies on redirects; use the final URL)" % (st, res.get("location"))
    if obj is None and "html" in ct:
        return False, "HTML, not JSON", "HTTP %d returned an HTML page: probably not an API route (wrong prefix) or the key was not sent" % st
    detail = redact(error_message(obj), key)
    if st == 200 and obj is not None:
        if kind == "models":
            data = obj.get("data") if isinstance(obj, dict) else None
            if isinstance(data, list):
                ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
                more = ", ..." if len(ids) > 5 else ""
                return True, "%d models" % len(ids), ", ".join(ids[:5]) + more
            return True, "OK", "200 JSON, but no OpenAI-style data[] list"
        if kind == "chat":
            try:
                return True, "OK", "reply: %r" % (obj["choices"][0]["message"]["content"] or "")[:60]
            except (KeyError, IndexError, TypeError):
                return True, "OK", "200 JSON"
        if kind == "responses":
            return True, "OK", "status: %s" % obj.get("status", "?")
        if kind == "messages":
            try:
                txt = "".join(b.get("text", "") for b in obj["content"] if b.get("type") == "text")
                return True, "OK", "reply: %r" % txt[:60]
            except (KeyError, TypeError):
                return True, "OK", "200 JSON"
    hint = HINTS.get(st, "HTTP %d" % st)
    if st == 401 and not key:
        hint = "needs a key (expected without one; endpoint exists)"
        return True, "401 auth required", detail or hint
    if st == 401 and kind == "models" and obj is not None:
        return False, hint, detail
    reached = st in (400, 402, 422, 429)
    return reached, hint, detail


def fmt_ms(v):
    return "%6.0f ms" % v if v is not None else "     -   "


def run_check(name, kind, method, url, headers, body, key, args):
    results = []
    for _ in range(max(1, args.repeat)):
        results.append(request(method, url, headers, body, args.timeout))
    last = results[-1]
    ok, verdict, detail = interpret(last, kind, key)
    ttfbs = [r["ttfb_ms"] for r in results if r.get("ttfb_ms") is not None]
    row = {
        "check": name,
        "method": method,
        "url": url,
        "status": last.get("status"),
        "content_type": last.get("content_type"),
        "ok": ok,
        "verdict": verdict,
        "detail": detail,
        "connect_ms": round(last["connect_ms"], 1) if last.get("connect_ms") is not None else None,
        "ttfb_ms": round(statistics.median(ttfbs), 1) if ttfbs else None,
        "ttfb_min_ms": round(min(ttfbs), 1) if ttfbs else None,
        "ttfb_max_ms": round(max(ttfbs), 1) if ttfbs else None,
        "samples": len(ttfbs),
    }
    if not args.json:
        mark = "\u2713" if ok else "\u2717"
        st = str(row["status"]) if row["status"] is not None else "---"
        print("%s %-22s %s  %s  %s" % (mark, name, st.rjust(3), fmt_ms(row["ttfb_ms"]), verdict))
        print("    %s %s" % (method, url))
        if detail:
            print("    " + str(detail)[:200])
        if args.repeat > 1 and ttfbs:
            print("    TTFB median %.0f ms (min %.0f, max %.0f, n=%d)" % (row["ttfb_ms"], row["ttfb_min_ms"], row["ttfb_max_ms"], len(ttfbs)))
    return row, last


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="llm-endpoint-check",
        description="Test an OpenAI-compatible and/or Anthropic-compatible Base URL: status, auth and latency.",
        epilog="Example: LLM_API_KEY=sk-... llm_endpoint_check.py --openai https://gateway.example.com/v1 --anthropic https://gateway.example.com",
    )
    p.add_argument("--openai", metavar="URL", help="OpenAI-compatible Base URL, e.g. https://host/v1 (tests /models, /chat/completions)")
    p.add_argument("--anthropic", metavar="URL", help="Anthropic Base URL as used in ANTHROPIC_BASE_URL, e.g. https://host (tests /v1/messages)")
    p.add_argument("--responses", metavar="URL", nargs="?", const="", help="also test POST /responses (Codex CLI). Optional URL = Codex base_url; defaults to --openai")
    p.add_argument("--model", metavar="ID", help="model for /chat/completions and /responses (default: first id from /models)")
    p.add_argument("--anthropic-model", metavar="ID", help="model for /v1/messages (default: first claude-* id from /models, else a placeholder)")
    p.add_argument("--auth", choices=["bearer", "x-api-key"], default="bearer",
                   help="Anthropic auth header: bearer (= ANTHROPIC_AUTH_TOKEN, default) or x-api-key (= ANTHROPIC_API_KEY)")
    p.add_argument("--key-env", metavar="NAME", default="LLM_API_KEY", help="environment variable holding the key (default LLM_API_KEY)")
    p.add_argument("--no-key", action="store_true", help="send no key (checks that the endpoint exists and answers 401 JSON)")
    p.add_argument("--repeat", type=int, default=1, metavar="N", help="repeat each request N times and report median TTFB")
    p.add_argument("--timeout", type=float, default=30.0, metavar="SEC", help="per-request timeout (default 30)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    p.add_argument("--version", action="version", version="%(prog)s " + __version__)
    args = p.parse_args(argv)

    if not (args.openai or args.anthropic or args.responses):
        p.error("pass at least one of --openai, --anthropic or --responses")

    key = None if args.no_key else (os.environ.get(args.key_env) or None)
    if not args.json:
        print("llm-endpoint-check %s  (key: %s)" % (__version__, "from $" + args.key_env if key else "none"))
        print()

    rows = []
    models = []
    if args.openai:
        base = args.openai.rstrip("/")
        h = {"Authorization": "Bearer " + key} if key else {}
        row, res = run_check("openai GET /models", "models", "GET", base + "/models", h, None, key, args)
        rows.append(row)
        obj = parse_json(res)
        if isinstance(obj, dict) and isinstance(obj.get("data"), list):
            models = [m.get("id") for m in obj["data"] if isinstance(m, dict) and m.get("id")]
        model = args.model or next((m for m in models if not str(m).startswith("claude")), None) or "MODEL_ID"
        body = {"model": model, "messages": [{"role": "user", "content": "Reply with: pong"}], "max_tokens": 16}
        row, _ = run_check("openai POST /chat", "chat", "POST", base + "/chat/completions", h, body, key, args)
        rows.append(row)
    if args.responses is not None:
        rbase = (args.responses or args.openai or "").rstrip("/")
        if not rbase:
            p.error("--responses needs a URL when --openai is not given")
        h = {"Authorization": "Bearer " + key} if key else {}
        model = args.model or next((m for m in models if str(m).startswith("gpt")), None) or "MODEL_ID"
        body = {"model": model, "input": "Reply with: pong", "max_output_tokens": 16}
        row, _ = run_check("openai POST /responses", "responses", "POST", rbase + "/responses", h, body, key, args)
        rows.append(row)
    if args.anthropic:
        abase = args.anthropic.rstrip("/")
        h = {"anthropic-version": "2023-06-01"}
        if key:
            h.update({"Authorization": "Bearer " + key} if args.auth == "bearer" else {"x-api-key": key})
        model = args.anthropic_model or next((m for m in models if str(m).startswith("claude")), None) or "claude-model-id"
        body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": "Reply with: pong"}]}
        row, _ = run_check("anthropic POST /v1/messages", "messages", "POST", abase + "/v1/messages", h, body, key, args)
        rows.append(row)

    if args.json:
        print(json.dumps({"version": __version__, "key_sent": bool(key), "checks": rows}, indent=2))
    else:
        print()
        if not key:
            print("No key sent. 401 means the route exists. Set $%s to run the full checks." % args.key_env)
        if any(r["verdict"] == "HTML, not JSON" for r in rows):
            print("Tip: an HTML page usually means the path prefix is wrong or the key was not attached.")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
