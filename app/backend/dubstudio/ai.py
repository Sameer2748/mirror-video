"""Claude client. Two providers, same OpenAI-compatible calls, chosen by what is in <app>/.env:

  AI_PROVIDER=gateway      the owner's Pocket FM "madeye" gateway (VPN only)
    AI_BASE   gateway URL, e.g. https://madeye.internal.pocketfm.org
    AI_KEY    the owner's key (Bearer token)
    AI_EMAIL  his work email; the gateway wants it in metadata.user_email on every call
    models: claude-sonnet-5-5, claude-haiku-5-5, claude-sonnet-4-6, gemini-3.5-flash
    budget:  GET /key/info -> info.spend, info.max_budget

  AI_PROVIDER=openrouter   OpenRouter (public internet: works from a deployed server, no VPN)
    OPENROUTER_API_KEY, optional OPENROUTER_BASE, OPENROUTER_CLAUDE_MODEL, OPENROUTER_FAST_MODEL
    models: anthropic/claude-sonnet-5.5 (text+image), anthropic/claude-haiku-5.5, …
    budget:  GET /key and GET /credits -> what is spent of the account's credits

With no AI_PROVIDER the choice is made for you: OpenRouter when its key is there, else the gateway. The step code
never knows the difference -- it calls `chat()` with our own model names, which are mapped per provider.
Claude models are served through LiteLLM -> Vertex on the gateway; do NOT send `temperature` to them (HTTP 400).
"""
import json, os, re, time, urllib.error, urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]          # the app folder (holds .env)
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_CACHE_STATS = dict(calls=0, prompt_tokens=0, cached_tokens=0, cost=0.0)   # what prompt caching saved
# our names (used all over the pipeline steps) -> the same model on OpenRouter
OPENROUTER_MODELS = {
    "claude-sonnet-5-5": "anthropic/claude-sonnet-5.5",
    "claude-haiku-5-5": "anthropic/claude-haiku-5.5",
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-opus-5": "anthropic/claude-opus-5",
    "gemini-3.5-flash": "google/gemini-2.5-flash",
}


def provider():
    p = (_env("AI_PROVIDER") or "").strip().lower()
    if p in ("gateway", "openrouter"):
        return p
    return "openrouter" if _env("OPENROUTER_API_KEY") else "gateway"


def _env(key, default=None):
    """AI_* settings: the environment first, then app/.env (`ROOT` is moved in the tests)."""
    if os.environ.get(key):
        return os.environ[key]
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            m = re.match(rf"\s*(?:export\s+)?{key}\s*=\s*(.*)\s*$", line)
            if m:
                v = m.group(1).strip().strip('"').strip("'")
                if v:
                    return v
    return default


def settings():
    """{AI_BASE, AI_KEY, AI_EMAIL, AI_MODEL, AI_MODEL_FAST, AI_PROVIDER} for whichever provider is in use."""
    s = {k: _env(k) for k in ("AI_BASE", "AI_KEY", "AI_EMAIL", "AI_MODEL", "AI_MODEL_FAST")}
    s = {k: v for k, v in s.items() if v}
    s["AI_PROVIDER"] = provider()
    if s["AI_PROVIDER"] == "openrouter":
        s["AI_BASE"] = (_env("OPENROUTER_BASE") or OPENROUTER_BASE).rstrip("/")
        s["AI_KEY"] = _env("OPENROUTER_API_KEY")
        s["AI_MODEL"] = (_env("OPENROUTER_CLAUDE_MODEL") or "anthropic/claude-sonnet-5.5").lstrip("~")
        s["AI_MODEL_FAST"] = (_env("OPENROUTER_FAST_MODEL") or "anthropic/claude-haiku-5.5").lstrip("~")
    else:
        s.setdefault("AI_MODEL", "claude-sonnet-5-5"); s.setdefault("AI_MODEL_FAST", "claude-haiku-5-5")
    return s


def model_id(name):
    """A model name from the steps, in the words of the provider in use."""
    if provider() != "openrouter" or not name:
        return name
    return OPENROUTER_MODELS.get(name, name if "/" in name else f"anthropic/{name}")


def _url(kind):
    s = settings(); base = (s.get("AI_BASE") or "").rstrip("/")
    if s["AI_PROVIDER"] == "openrouter":
        return base + {"chat": "/chat/completions", "key": "/key", "credits": "/credits", "models": "/models"}[kind]
    return base + {"chat": "/v1/chat/completions", "key": "/key/info", "credits": "/key/info", "models": "/v1/models"}[kind]


def available():
    s = settings()
    if s["AI_PROVIDER"] == "openrouter":
        return bool(s.get("AI_BASE") and s.get("AI_KEY"))
    return bool(s.get("AI_BASE") and s.get("AI_KEY") and s.get("AI_EMAIL"))


def _req(url, body=None, timeout=120):
    s = settings()
    if not available():
        raise RuntimeError("no AI provider configured: set OPENROUTER_API_KEY, or AI_BASE/AI_KEY/AI_EMAIL, in app/.env")
    data = json.dumps(body).encode() if body is not None else None
    head = {"Authorization": "Bearer " + s["AI_KEY"], "Content-Type": "application/json"}
    if s["AI_PROVIDER"] == "openrouter":                  # OpenRouter shows these on the account's activity page
        head["HTTP-Referer"] = "https://dubstudio.local"; head["X-Title"] = "Mirror Video"
    r = urllib.request.Request(url, data=data, headers=head)
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def chat(prompt, model=None, max_tokens=4000, system=None, retries=4, temperature=None, reasoning=None):
    """One call. `prompt` is a string or an OpenAI-style content list. Returns (text, usage).
    `reasoning` is forwarded as-is (e.g. {"effort": "low"} on OpenRouter Claude 5.5)."""
    s = settings(); model = model_id(model or s["AI_MODEL"])
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = dict(model=model, messages=msgs, max_tokens=max_tokens)
    if s["AI_PROVIDER"] != "openrouter":
        body["metadata"] = {"user_email": s.get("AI_EMAIL", "")}
    if temperature is not None:          # the Claude 5.5 models refuse `temperature` (deprecated); leave it out
        body["temperature"] = temperature
    if reasoning:
        body["reasoning"] = reasoning
    for a in range(retries + 1):
        try:
            d = _req(_url("chat"), body, timeout=300)
            ch = d["choices"][0]; txt = ch["message"].get("content") or ""
            u = d.get("usage") or {}                       # what the prompt cache saved on this call
            _CACHE_STATS["calls"] += 1
            _CACHE_STATS["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
            _CACHE_STATS["cached_tokens"] += int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
            _CACHE_STATS["cost"] += float(u.get("cost") or 0)
            if not txt.strip():                  # say why a reply is empty (e.g. finish_reason=length: max_tokens used up)
                import sys
                print(f"AI gateway: empty reply, finish_reason={ch.get('finish_reason')}, usage={d.get('usage')}", file=sys.stderr, flush=True)
            return txt, d.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504, 529) and a < retries:
                time.sleep(2 * 2 ** a); continue
            raise RuntimeError(f"AI gateway HTTP {e.code}: {e.read()[:300]!r}") from None
        except (urllib.error.URLError, TimeoutError):
            if a < retries:
                time.sleep(2 * 2 ** a); continue
            raise


def _close_truncated_json(text):
    """A reply cut off at max_tokens, e.g. `{"locations": {"A": "study", "Night forest, then Albe`.
    Drop the unfinished last field and close open braces. -> object or None."""
    i = next((k for k, c in enumerate(text) if c in "{["), None)
    if i is None:
        return None
    s = text[i:]
    if s.count('"') % 2 == 1:                          # unfinished string: drop back to the last complete field
        last_q = s.rfind('"')
        cut = max(s.rfind(",", 0, last_q), s.rfind("{", 0, last_q), s.rfind("[", 0, last_q))
        if cut <= 0:
            return None
        s = s[:cut]
    s = s.rstrip().rstrip(",")
    # counts ignore quotes, good enough for the location/scene maps this step asks for
    s += "]" * max(s.count("[") - s.count("]"), 0)
    s += "}" * max(s.count("{") - s.count("}"), 0)
    try:
        return json.loads(s)
    except ValueError:
        return None


def extract_json(text, key=None):
    """Claude sometimes writes prose (which may contain things like "[13]") before the JSON. Return the first JSON
    object found; with `key`, the first object that has that key. A reply cut off mid-object is closed and used."""
    text = re.sub(r"```(?:json)?", "", text or "")
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
    repaired = _close_truncated_json(text)
    if repaired is not None and (key is None or (isinstance(repaired, dict) and key in repaired)):
        return repaired
    raise ValueError("no JSON in model reply: " + text[:200])


def key_info():
    """{spend, max_budget}: what the badge and the budget gate read, from whichever provider is in use."""
    if provider() == "openrouter":
        k = (_req(_url("key"), timeout=30) or {}).get("data", {})
        # two ceilings: the key's own budget (often monthly) and the money on the account. Show whichever runs out
        # first -- a $500 key on an account with $87 of credits can only spend $87.
        spend, budget = k.get("usage") or 0, k.get("limit")
        try:
            c = (_req(_url("credits"), timeout=30) or {}).get("data", {})
            if c.get("total_credits"):
                acc_spend, acc_budget = c.get("total_usage") or 0, c["total_credits"]
                if budget is None or (acc_budget - acc_spend) < (budget - spend):
                    spend, budget = acc_spend, acc_budget
        except Exception:
            pass
        return {"spend": spend, "max_budget": budget, "models": None,
                "resets": (k.get("effective_budget") or {}).get("resets_at") or k.get("limit_reset")}
    i = _req(_url("key"), timeout=30); i = i.get("info", i)
    return {"spend": i.get("spend"), "max_budget": i.get("max_budget"), "models": i.get("models")}


def models():
    ms = [m["id"] for m in _req(_url("models"), timeout=30).get("data", [])]
    if provider() == "openrouter":             # the full list is thousands of models: ours are the Claude ones
        ms = [m for m in ms if m.startswith("anthropic/")]
    return ms


def cache_stats():
    """How much of what we sent came out of the provider's prompt cache (and what the calls cost)."""
    s = dict(_CACHE_STATS)
    s["cached_share"] = (s["cached_tokens"] / s["prompt_tokens"]) if s["prompt_tokens"] else 0
    return s
