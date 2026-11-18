"""One way to ask Claude for structured JSON, used by ai_thumbnail / ai_metadata / merge_upload.

Owner 2026-11-19: the Anthropic API key has no credit (the $100 credit came with Max, which the
owner is not buying this month), so the cloud uses the owner's $20 Claude subscription through the
Claude Code CLI: GitHub secret CLAUDE_CODE_OAUTH_TOKEN (made once with `claude setup-token`).

Order: subscription (CLAUDE_CODE_OAUTH_TOKEN + `claude` CLI) -> API key (ANTHROPIC_API_KEY) -> raise.
Callers catch the error and fall back to a no-Claude path, so an upload is never blocked.
Owner 2026-11-19: same models as on the API (Opus for plans/checks/picks, Sonnet for titles), no
quality cuts, and NO upload without Claude: callers raise when this raises.
"""
import json
import logging
import os
import shutil
import subprocess
import tempfile

log = logging.getLogger(__name__)


def _usage_line(via, model, inp, cache_create, cache_read, out, cost):
    """One machine-readable line per Claude call, so Friday can add up how much of the owner's
    Claude limit this workflow used (jp_status.py --usage). cost_usd = Claude Code's own estimate of
    what the call would cost on the API (the subscription itself is a flat $20)."""
    rec = {"via": via, "model": model, "channel": os.environ.get("DB_PAGE_ID", ""),
           "in": inp or 0, "cache_create": cache_create or 0, "cache_read": cache_read or 0, "out": out or 0,
           "cost_usd": round(cost, 4) if isinstance(cost, (int, float)) else None}
    log.info("claude (%s, %s): %s in + %s cache-write + %s cache-read / %s out tokens", via, model, rec["in"],
             rec["cache_create"], rec["cache_read"], rec["out"])
    log.info("CLAUDE_USAGE %s", json.dumps(rec))


def _cli():
    return shutil.which("claude")


def _via_subscription(system, content, schema, model, timeout, effort="medium"):
    msg = {"type": "user", "message": {"role": "user", "content": content}}
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    with tempfile.TemporaryDirectory(prefix="claude_") as tmp:      # empty cwd: no project settings
        cmd = [_cli(), "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--json-schema", json.dumps(schema), "--model", model, "--effort", effort, "--system-prompt", system,
               "--tools", "", "--max-turns", "4", "--no-session-persistence", "--strict-mcp-config"]
        r = subprocess.run(cmd, input=json.dumps(msg) + "\n", capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, cwd=tmp, env=env)
    result = None
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "result":
            result = ev
    if not result:
        raise RuntimeError(f"claude cli gave no result (exit {r.returncode}): {(r.stderr or r.stdout)[-300:]}")
    if result.get("is_error") or "structured_output" not in result:
        raise RuntimeError(f"claude cli error: {str(result.get('result') or result.get('subtype'))[:300]}")
    u = result.get("usage") or {}
    _usage_line("subscription", model, u.get("input_tokens"), u.get("cache_creation_input_tokens"),
                u.get("cache_read_input_tokens"), u.get("output_tokens"), result.get("total_cost_usd"))
    return result["structured_output"]


def _via_api(system, content, schema, model, max_tokens, effort):
    import anthropic
    resp = anthropic.Anthropic().messages.create(
        model=model, max_tokens=max_tokens, system=system,
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}])
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined")
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={resp.stop_reason}")
    u = resp.usage
    _usage_line("api", model, u.input_tokens, getattr(u, "cache_creation_input_tokens", 0),
                getattr(u, "cache_read_input_tokens", 0), u.output_tokens, None)
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def structured(system, content, schema, model="claude-opus-5-5", max_tokens=8000, effort="medium",
               timeout=420):
    """content = list of Messages-API content blocks (text / base64 image). Returns the parsed dict."""
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    errors = []
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and _cli():
        try:
            return _via_subscription(system or "", content, schema, model, timeout, effort)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"subscription: {exc}")
            log.warning("claude subscription failed: %s", str(exc)[:300])
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _via_api(system, content, schema, model, max_tokens, effort)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"api: {exc}")
            log.warning("claude api failed: %s", str(exc)[:300])
    raise RuntimeError("no working Claude access: " + (" | ".join(errors) or "no token or key set"))
