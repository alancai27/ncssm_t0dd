"""Provider config: the single place the hosting decision lives.

Every candidate backend speaks the OpenAI-compatible chat-completions API,
so switching between a hosted endpoint, a self-hosted vLLM box, or a local
Ollama model is a base_url + model swap. Nothing downstream of this file
knows or cares which one is active.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    model: str
    api_key_env: str
    # Rough $/1M tokens, for the cost meter. Verify before quoting these.
    usd_per_m_in: float = 0.0
    usd_per_m_out: float = 0.0

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")


# Swap PROVIDER to move the whole app between backends.
PROVIDERS = {
    # --- Gemini via Google's OpenAI-compatibility endpoint ---
    # Note: NCSSM already runs on Google Workspace (sign-in is @ncssm.edu
    # Google accounts), so Google is likely already a data processor for the
    # school. That is a different privacy posture than adding a new vendor.
    "gemini": Provider(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model=os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
        api_key_env="GEMINI_API_KEY",
        # Rates left at 0: verify current Gemini pricing before trusting the
        # cost meter. Flash tiers also have a free quota via AI Studio.
        usd_per_m_in=0.0, usd_per_m_out=0.0,
    ),

    # --- OpenRouter: one key, every model; good for dev + the pilot ---
    "openrouter": Provider(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model=os.environ.get("OR_MODEL", "qwen/qwen3-30b-a3b-instruct"),
        api_key_env="OPENROUTER_API_KEY",
        usd_per_m_in=0.20, usd_per_m_out=0.60,
    ),
    # Free tier, for wiring/plumbing work only.
    # NOTE: free endpoints commonly log prompts for training. Synthetic data
    # ONLY -- never point this at a real student conversation.
    "openrouter-free": Provider(
        name="openrouter-free",
        base_url="https://openrouter.ai/api/v1",
        # Qwen's free tier ended Aug 2026; Nemotron-nano is the closest
        # architectural stand-in (30B MoE, ~3B active) still at $0.
        model=os.environ.get("OR_FREE_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free"),
        api_key_env="OPENROUTER_API_KEY",
        usd_per_m_in=0.0, usd_per_m_out=0.0,
    ),
    # --- Hosted: for development and the pilot ---
    "together": Provider(
        name="together",
        base_url="https://api.together.xyz/v1",
        model="Qwen/Qwen3-30B-A3B-Instruct",
        api_key_env="TOGETHER_API_KEY",
        usd_per_m_in=0.20, usd_per_m_out=0.60,
    ),
    "deepinfra": Provider(
        name="deepinfra",
        base_url="https://api.deepinfra.com/v1/openai",
        model="Qwen/Qwen3-30B-A3B-Instruct",
        api_key_env="DEEPINFRA_API_KEY",
        usd_per_m_in=0.20, usd_per_m_out=0.60,
    ),
    # --- Self-hosted: drop-in for deployment, no code changes ---
    "vllm": Provider(
        name="vllm",
        base_url=os.environ.get("VLLM_URL", "http://localhost:8000/v1"),
        model="Qwen/Qwen3-30B-A3B-Instruct",
        api_key_env="VLLM_API_KEY",
    ),
    "ollama": Provider(
        name="ollama",
        base_url="http://localhost:11434/v1",
        model=os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
        api_key_env="OLLAMA_API_KEY",
    ),
}

PROVIDER = PROVIDERS[os.environ.get("TUTOR_PROVIDER", "gemini")]

# The classifier is a cheap, high-volume call; it does not need the big model.
# Point it at a small model on the same backend to cut cost ~10x.
CLASSIFIER_MODEL = os.environ.get("TUTOR_CLASSIFIER_MODEL", PROVIDER.model)
