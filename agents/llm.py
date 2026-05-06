from __future__ import annotations

import os

from crewai import LLM


def build_groq_llm(model: str | None = None, temperature: float = 0.2) -> LLM:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is required")
    selected_model = model or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    return LLM(
        # Use native OpenAI-compatible provider path with Groq base URL.
        model=f"openai/{selected_model}",
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1",
        temperature=temperature,
    )
