from __future__ import annotations

from dataclasses import dataclass

from app.models import ExecutionProfile, PromptProfileRef


@dataclass(frozen=True)
class ModelOption:
    model_name: str
    label: str
    description: str


@dataclass(frozen=True)
class PromptProfileDefinition:
    profile_id: str
    name: str
    version: str
    description: str
    analyst_instruction: str
    content_instruction: str
    reviewer_instruction: str


AVAILABLE_MODELS = (
    ModelOption(
        model_name="qwen3-max",
        label="Qwen3 Max",
        description="质量优先，适合复杂分析、审核和较长输出。",
    ),
    ModelOption(
        model_name="qwen-plus",
        label="Qwen Plus",
        description="平衡成本与质量，适合大多数业务工作流。",
    ),
    ModelOption(
        model_name="qwen-turbo",
        label="Qwen Turbo",
        description="速度优先，适合轻量内容生成和高频调用。",
    ),
)


PROMPT_PROFILES = (
    PromptProfileDefinition(
        profile_id="balanced-v1",
        name="平衡版",
        version="v1",
        description="默认方案，兼顾分析深度、可执行性和输出稳定性。",
        analyst_instruction="先给结论，再补关键洞察和动作建议，避免空泛复述原始数据。",
        content_instruction="输出要能被业务同学直接复制使用，兼顾信息完整度和易读性。",
        reviewer_instruction="以稳健为主，只在结果足够完整且风险可控时允许自动流转。",
    ),
    PromptProfileDefinition(
        profile_id="ops-deep-v1",
        name="运营深挖版",
        version="v1",
        description="更强调问题拆解、异常原因定位和后续动作设计。",
        analyst_instruction="突出异常定位、瓶颈拆解和优先级排序，用更强的运营分析视角输出。",
        content_instruction="补充更具体的行动项、负责人建议和执行节奏，减少泛化表达。",
        reviewer_instruction="对信息缺口和潜在执行风险更敏感，宁可人工复核也不要过度自动放行。",
    ),
    PromptProfileDefinition(
        profile_id="exec-brief-v2",
        name="管理摘要版",
        version="v2",
        description="强调结论先行、简洁表达和适合管理层快速阅读的产出。",
        analyst_instruction="优先输出高层结论、核心风险和最重要的 2 到 3 个动作，不展开冗长细节。",
        content_instruction="内容偏摘要和汇报风格，适合周报、管理看板和对上同步材料。",
        reviewer_instruction="重点检查结论是否明确、格式是否可汇报、是否适合直接对外或对上流转。",
    ),
)


DEFAULT_PROMPT_PROFILE_ID = PROMPT_PROFILES[0].profile_id

_MODEL_INDEX = {item.model_name: item for item in AVAILABLE_MODELS}
_PROMPT_INDEX = {item.profile_id: item for item in PROMPT_PROFILES}


def list_model_options() -> list[dict[str, str]]:
    return [
        {
            "model_name": item.model_name,
            "label": item.label,
            "description": item.description,
        }
        for item in AVAILABLE_MODELS
    ]


def list_prompt_profiles() -> list[dict[str, str]]:
    return [
        {
            "profile_id": item.profile_id,
            "name": item.name,
            "version": item.version,
            "description": item.description,
        }
        for item in PROMPT_PROFILES
    ]


def resolve_execution_profile(
    *,
    default_model_name: str,
    model_name_override: str | None = None,
    prompt_profile_id: str | None = None,
) -> ExecutionProfile:
    model_name = model_name_override if model_name_override in _MODEL_INDEX else default_model_name
    model_option = _MODEL_INDEX.get(model_name) or _MODEL_INDEX[default_model_name]
    prompt_definition = _PROMPT_INDEX.get(prompt_profile_id or DEFAULT_PROMPT_PROFILE_ID) or _PROMPT_INDEX[DEFAULT_PROMPT_PROFILE_ID]
    return ExecutionProfile(
        model_name=model_option.model_name,
        model_label=model_option.label,
        prompt_profile=PromptProfileRef(
            profile_id=prompt_definition.profile_id,
            name=prompt_definition.name,
            version=prompt_definition.version,
            description=prompt_definition.description,
        ),
    )


def get_prompt_definition(profile_id: str | None = None) -> PromptProfileDefinition:
    return _PROMPT_INDEX.get(profile_id or DEFAULT_PROMPT_PROFILE_ID) or _PROMPT_INDEX[DEFAULT_PROMPT_PROFILE_ID]
