"""AI gateway client (OpenAI-compatible chat completions, Pocket FM "madeye" gateway).

Settings come from environment variables, else from <app>/.env (never committed):
  AI_BASE   gateway URL, e.g. https://madeye.internal.pocketfm.org
  AI_KEY    the owner's key (Bearer token)
  AI_EMAIL  the owner's work email; the gateway wants it in metadata.user_email on every call
  AI_MODEL  default model (claude-sonnet-5-5); AI_MODEL_FAST (claude-haiku-5-5)
Models on this key (GET /v1/models): claude-sonnet-5-5, claude-haiku-5-5, claude-sonnet-4-6, gemini-3.5-flash.
Budget/spend: GET /key/info -> info.spend, info.max_budget.
Claude models are served through LiteLLM -> Vertex; do NOT send `temperature` to them (HTTP 400).
"""
import json, os, re, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # the app folder (holds .env)


def settings():
    s = {}
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            m = re.match(r"\s*(?:export\s+)?(AI_\w+)\s*=\s*(.*)\s*$", line)
            if m:
                s[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    for k in ("AI_BASE", "AI_KEY", "AI_EMAIL", "AI_MODEL", "AI_MODEL_FAST"):
        if os.environ.get(k):
            s[k] = os.environ[k]
    s.setdefault("AI_MODEL", "claude-sonnet-5-5"); s.setdefault("AI_MODEL_FAST", "claude-haiku-5-5")
    return s


def available():
    s = settings()
    return bool(s.get("AI_BASE") and s.get("AI_KEY") and s.get("AI_EMAIL"))


def _req(path, body=None, timeout=120):
    s = settings()
    if not available():
        raise RuntimeError("AI gateway not configured: set AI_BASE, AI_KEY, AI_EMAIL in app/.env")
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(s["AI_BASE"].rstrip("/") + path, data=data,
                               headers={"Authorization": "Bearer " + s["AI_KEY"], "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def chat(prompt, model=None, max_tokens=4000, system=None, retries=4, temperature=None):
    """One call. `prompt` is a string or an OpenAI-style content list. Returns (text, usage)."""
    s = settings(); model = model or s["AI_MODEL"]
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = dict(model=model, messages=msgs, max_tokens=max_tokens, metadata={"user_email": s.get("AI_EMAIL", "")})
    if temperature is not None:          # the Claude 5.5 models refuse `temperature` (deprecated); leave it out
        body["temperature"] = temperature
    for a in range(retries + 1):
        try:
            d = _req("/v1/chat/completions", body, timeout=300)
            return d["choices"][0]["message"]["content"] or "", d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504, 529) and a < retries:
                time.sleep(2 * 2 ** a); continue
            raise RuntimeError(f"AI gateway HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError):
            if a < retries:
                time.sleep(2 * 2 ** a); continue
            raise


def extract_json(text, key=None):
    """Claude sometimes writes prose (which may contain things like "[13]") before the JSON. Return the first JSON
    object found; with `key`, the first object that has that key."""
    text = re.sub(r"```(?:json)?", "", text)
    first = None
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                v = json.JSONDecoder().raw_decode(text[i:])[0]
            except ValueError:
                continue
            if key is None and isinstance(v, (dict, list)) and ch == "{":
                return v
            if key is not None and isinstance(v, dict) and key in v:
                return v
            first = first if first is not None else v
    if key is None and first is not None:
        return first
    raise ValueError("no JSON in model reply: " + text[:200])


def key_info():
    i = _req("/key/info", timeout=30); i = i.get("info", i)
    return {"spend": i.get("spend"), "max_budget": i.get("max_budget"), "models": i.get("models")}


def models():
    return [m["id"] for m in _req("/v1/models", timeout=30).get("data", [])]
