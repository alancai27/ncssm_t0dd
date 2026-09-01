"""OpenAI-compatible client, stdlib only.

Deliberately no SDK: every backend in config.py speaks this wire format, so
one ~60-line client covers hosted OpenRouter, self-hosted vLLM, and local
Ollama with zero dependency surface and zero install friction.
"""
import json
import urllib.error
import urllib.request

from .config import PROVIDER, Provider


class Usage:
    """Running token + cost meter, so cost is observable during the pilot."""
    def __init__(self):
        self.tok_in = self.tok_out = 0
        self.usd = 0.0

    def add(self, p: Provider, tin: int, tout: int):
        self.tok_in += tin
        self.tok_out += tout
        self.usd += tin / 1e6 * p.usd_per_m_in + tout / 1e6 * p.usd_per_m_out

    def __str__(self):
        return f"{self.tok_in:,} in / {self.tok_out:,} out / ${self.usd:.4f}"


USAGE = Usage()


def chat(messages, provider: Provider = None, model: str = None,
         temperature: float = 0.3, max_tokens: int = 800, timeout: int = 90) -> str:
    p = provider or PROVIDER
    body = json.dumps({
        "model": model or p.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()

    headers = {"Content-Type": "application/json"}
    if p.api_key:
        headers["Authorization"] = f"Bearer {p.api_key}"
    if p.name.startswith("openrouter"):
        # OpenRouter asks for these for attribution; harmless elsewhere.
        headers["HTTP-Referer"] = "https://ncssm.edu"
        headers["X-Title"] = "NCSSM CS Tutor"

    req = urllib.request.Request(f"{p.base_url}/chat/completions", data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{p.name} HTTP {e.code}: {e.read().decode()[:400]}") from None

    u = data.get("usage") or {}
    USAGE.add(p, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    return data["choices"][0]["message"]["content"]
