from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.config import Settings
from app.models import LLMCall


@dataclass(frozen=True)
class LLMJsonResponse:
    payload: dict[str, Any]
    call: LLMCall


class LLMService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: OpenAI | None = None
        if settings.llm_enabled:
            self._client = OpenAI(api_key=settings.api_key, base_url=settings.model_base_url)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
    ) -> LLMJsonResponse:
        if not self._client:
            return LLMJsonResponse(
                payload=fallback,
                call=self._build_call_trace(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    latency_ms=0,
                    used_fallback=True,
                    error="llm_disabled",
                ),
            )

        started_at = time.perf_counter()
        try:
            response = self._client.chat.completions.create(
                model=self.settings.model_name,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            content = response.choices[0].message.content or ""
            parsed = self._extract_json(content)
            usage = getattr(response, "usage", None)
            call = self._build_call_trace(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                latency_ms=latency_ms,
                prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
                used_fallback=parsed is None,
                error=None if parsed is not None else "json_parse_failed",
            )
            return LLMJsonResponse(payload=parsed or fallback, call=call)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            return LLMJsonResponse(
                payload=fallback,
                call=self._build_call_trace(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    latency_ms=latency_ms,
                    used_fallback=True,
                    error=f"{type(exc).__name__}: {exc}",
                ),
            )

    def _build_call_trace(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        latency_ms: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        used_fallback: bool = False,
        error: str | None = None,
    ) -> LLMCall:
        return LLMCall(
            provider="dashscope_openai_compatible",
            model_name=self.settings.model_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            used_fallback=used_fallback,
            error=error,
        )

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        text = content.strip()
        if text.startswith("```"):
            parts = text.split("```")
            for part in parts:
                candidate = part.strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].strip()
                if candidate.startswith("{") and candidate.endswith("}"):
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
        if text.startswith("{") and text.endswith("}"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and start < end:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
