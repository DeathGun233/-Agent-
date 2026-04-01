from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models import ExecutionProfile, LLMCall
from app.prompt_catalog import get_model_option


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
        route_target: str,
        system_prompt: str,
        user_prompt: str,
        fallback: dict[str, Any],
        execution_profile: ExecutionProfile,
        response_model: type[BaseModel] | None = None,
        max_retries: int = 2,
    ) -> LLMJsonResponse:
        model_name = execution_profile.model_routes.get(route_target, execution_profile.primary_model_name)
        if not self._client:
            return LLMJsonResponse(
                payload=fallback,
                call=self._build_call_trace(
                    route_target=route_target,
                    model_name=model_name,
                    execution_profile=execution_profile,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    latency_ms=0,
                    retry_count=0,
                    used_fallback=True,
                    error="llm_disabled",
                ),
            )

        validation_error: str | None = None
        total_latency_ms = 0
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_total_tokens = 0
        final_error: str | None = None

        for attempt in range(max_retries + 1):
            started_at = time.perf_counter()
            try:
                effective_user_prompt = user_prompt
                if attempt > 0:
                    effective_user_prompt = (
                        f"{user_prompt}\n\n上一次返回未通过结构化校验，请严格只返回合法 JSON，"
                        f"并补全必填字段。校验错误：{validation_error or 'unknown'}"
                    )
                response = self._client.chat.completions.create(
                    model=model_name,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": effective_user_prompt},
                    ],
                )
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                total_latency_ms += latency_ms
                content = response.choices[0].message.content or ""
                parsed = self._extract_json(content)
                usage = getattr(response, "usage", None)
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
                total_prompt_tokens += prompt_tokens
                total_completion_tokens += completion_tokens
                total_total_tokens += total_tokens
                if parsed is None:
                    validation_error = "json_parse_failed"
                    final_error = validation_error
                    continue
                if response_model is not None:
                    try:
                        parsed = response_model.model_validate(parsed).model_dump(mode="json")
                    except ValidationError as exc:
                        validation_error = str(exc)
                        final_error = "schema_validation_failed"
                        continue
                validation_error = None
                call = self._build_call_trace(
                    route_target=route_target,
                    model_name=model_name,
                    execution_profile=execution_profile,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    latency_ms=total_latency_ms,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_total_tokens,
                    retry_count=attempt,
                    used_fallback=False,
                    validation_error=validation_error,
                    error=None,
                )
                return LLMJsonResponse(payload=parsed, call=call)
            except Exception as exc:
                latency_ms = int((time.perf_counter() - started_at) * 1000)
                total_latency_ms += latency_ms
                final_error = f"{type(exc).__name__}: {exc}"
                validation_error = None

        return LLMJsonResponse(
            payload=fallback,
            call=self._build_call_trace(
                route_target=route_target,
                model_name=model_name,
                execution_profile=execution_profile,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                latency_ms=total_latency_ms,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                total_tokens=total_total_tokens,
                retry_count=max_retries,
                used_fallback=True,
                error=final_error,
                validation_error=validation_error,
            ),
        )

    def _build_call_trace(
        self,
        *,
        route_target: str,
        model_name: str,
        execution_profile: ExecutionProfile,
        system_prompt: str,
        user_prompt: str,
        latency_ms: int,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        retry_count: int = 0,
        used_fallback: bool = False,
        error: str | None = None,
        validation_error: str | None = None,
    ) -> LLMCall:
        pricing = get_model_option(model_name)
        estimated_cost = round(
            (prompt_tokens / 1000.0) * pricing.input_cost_per_1k_tokens
            + (completion_tokens / 1000.0) * pricing.output_cost_per_1k_tokens,
            6,
        )
        return LLMCall(
            provider="dashscope_openai_compatible",
            model_name=model_name,
            route_target=route_target,
            prompt_profile_id=execution_profile.prompt_profile.profile_id,
            prompt_profile_name=execution_profile.prompt_profile.name,
            prompt_profile_version=execution_profile.prompt_profile.version,
            routing_policy_id=execution_profile.routing_policy.policy_id,
            routing_policy_name=execution_profile.routing_policy.name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost,
            retry_count=retry_count,
            used_fallback=used_fallback,
            error=error,
            validation_error=validation_error,
        )

    @staticmethod
    def _extract_json(content: str) -> dict[str, Any] | None:
        text = content.strip()
        if text.startswith("```"):
            for part in text.split("```"):
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
