from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from finage.settings import Settings


class LlmProvider(Protocol):
    name: str
    model: str

    async def generate(self, prompt: str) -> str:
        ...


@dataclass
class GeminiProvider:
    api_key: str
    model: str
    max_output_tokens: int = 10000
    name: str = "gemini"

    async def generate(self, prompt: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = await client.aio.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=self.max_output_tokens,
                system_instruction=(
                    "You are a concise market intelligence assistant. "
                    "Summarize social-media evidence objectively, avoid hype, "
                    "and never present the output as financial advice."
                ),
            ),
        )
        text = (response.text or "").strip()
        if not text:
            raise RuntimeError("Gemini returned an empty digest")
        return text


def create_llm_provider(settings: Settings) -> LlmProvider:
    if settings.llm_provider == "gemini":
        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)

    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
