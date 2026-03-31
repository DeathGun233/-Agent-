from __future__ import annotations

from dataclasses import dataclass

from app.models import ExecutionProfile, PromptProfile, RoutingPolicyRef


@dataclass(frozen=True)
class ModelOption:
    model_name: str
    label: str
    description: str


@dataclass(frozen=True)
class RoutingPolicyDefinition:
    policy_id: str
    name: str
    description: str
    route_templates: dict[str, str]

    def resolve_routes(self, primary_model_name: str) -> dict[str, str]:
        return {
            agent: (primary_model_name if template == "{primary}" else template)
            for agent, template in self.route_templates.items()
        }


@dataclass(frozen=True)
class EvaluationCaseDefinition:
    case_id: str
    title: str
    workflow_type: str
    input_payload: dict
    expected_status: str
    expected_keywords: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationDatasetDefinition:
    dataset_id: str
    name: str
    description: str
    cases: tuple[EvaluationCaseDefinition, ...]


AVAILABLE_MODELS = (
    ModelOption(
        model_name="qwen3-max",
        label="Qwen3 Max",
        description="质量优先，适合复杂分析、严格审核和高要求输出。",
    ),
    ModelOption(
        model_name="qwen-plus",
        label="Qwen Plus",
        description="均衡成本与质量，适合大多数业务工作流。",
    ),
    ModelOption(
        model_name="qwen-turbo",
        label="Qwen Turbo",
        description="速度优先，适合轻量内容生成和高频调用。",
    ),
)


ROUTING_POLICIES = (
    RoutingPolicyDefinition(
        policy_id="single-model-v1",
        name="单模型直连",
        description="所有 Agent 共用同一个主模型，适合做基础对比。",
        route_templates={
            "planner": "{primary}",
            "analyst": "{primary}",
            "content": "{primary}",
            "reviewer": "{primary}",
        },
    ),
    RoutingPolicyDefinition(
        policy_id="balanced-router-v1",
        name="均衡路由",
        description="分析使用主模型，内容生成走更快模型，审核固定走高质量模型。",
        route_templates={
            "planner": "{primary}",
            "analyst": "{primary}",
            "content": "qwen-turbo",
            "reviewer": "qwen3-max",
        },
    ),
    RoutingPolicyDefinition(
        policy_id="speed-router-v1",
        name="速度优先路由",
        description="分析和内容生成都走更快模型，只把审核保留给主模型。",
        route_templates={
            "planner": "{primary}",
            "analyst": "qwen-turbo",
            "content": "qwen-turbo",
            "reviewer": "{primary}",
        },
    ),
    RoutingPolicyDefinition(
        policy_id="strict-review-v1",
        name="严格审核路由",
        description="分析和内容都走主模型，审核固定使用最高质量模型。",
        route_templates={
            "planner": "{primary}",
            "analyst": "{primary}",
            "content": "{primary}",
            "reviewer": "qwen3-max",
        },
    ),
)


BUILTIN_PROMPT_PROFILES = (
    PromptProfile(
        profile_id="balanced-v1",
        name="平衡版",
        version="v1",
        description="默认方案，兼顾分析深度、可执行性和输出稳定性。",
        analyst_instruction="先给结论，再补关键洞察和动作建议，避免空泛复述原始数据。",
        content_instruction="输出要能被业务同学直接复制使用，兼顾信息完整度和易读性。",
        reviewer_instruction="以稳健为主，只在结果足够完整且风险可控时允许自动流转。",
        is_builtin=True,
    ),
    PromptProfile(
        profile_id="ops-deep-v1",
        name="运营深挖版",
        version="v1",
        description="更强调问题拆解、异常原因定位和后续动作设计。",
        analyst_instruction="突出异常定位、瓶颈拆解和优先级排序，用更强的运营分析视角输出。",
        content_instruction="补充更具体的行动项、负责人建议和执行节奏，减少泛化表达。",
        reviewer_instruction="对信息缺口和潜在执行风险更敏感，宁可人工复核也不要过度自动放行。",
        is_builtin=True,
    ),
    PromptProfile(
        profile_id="exec-brief-v2",
        name="管理摘要版",
        version="v2",
        description="强调结论先行、简洁表达和适合管理层快速阅读的产出。",
        analyst_instruction="优先输出高层结论、核心风险和最重要的 2 到 3 个动作，不展开冗长细节。",
        content_instruction="内容偏摘要和汇报风格，适合周报、管理看板和对上同步材料。",
        reviewer_instruction="重点检查结论是否明确、格式是否可汇报、是否适合直接对上流转。",
        is_builtin=True,
    ),
)


EVALUATION_DATASETS = (
    EvaluationDatasetDefinition(
        dataset_id="ops-regression-v1",
        name="运营回归集",
        description="覆盖销售、客服、会议纪要和营销内容四类典型工作流，用于回归验证。",
        cases=(
            EvaluationCaseDefinition(
                case_id="sales-conversion",
                title="销售转化漏斗分析",
                workflow_type="sales_followup",
                input_payload={
                    "period": "2026-W13",
                    "region": "华东",
                    "sales_reps": ["王晨", "李雪"],
                    "focus_metric": "conversion_rate",
                },
                expected_status="waiting_human",
                expected_keywords=("转化", "风险客户", "跟进"),
            ),
            EvaluationCaseDefinition(
                case_id="marketing-assets",
                title="营销多渠道内容生成",
                workflow_type="marketing_campaign",
                input_payload={
                    "product_name": "FlowPilot AI 自动化平台",
                    "audience": "B2B 企业运营负责人",
                    "channels": ["xiaohongshu", "douyin", "wechat"],
                    "key_benefits": ["多智能体执行", "人工接管", "流程可观测"],
                    "tone": "专业但有行动感",
                },
                expected_status="waiting_human",
                expected_keywords=("投放", "合规", "内容"),
            ),
            EvaluationCaseDefinition(
                case_id="support-handoff",
                title="客服故障升级判断",
                workflow_type="support_triage",
                input_payload={
                    "tickets": [
                        {"customer": "示例客户", "message": "系统报错并影响上线，请尽快恢复。"},
                        {"customer": "星云教育", "message": "请问支持开票和合同模板下载吗？"},
                    ]
                },
                expected_status="waiting_human",
                expected_keywords=("紧急", "升级", "回复"),
            ),
            EvaluationCaseDefinition(
                case_id="meeting-followup",
                title="会议纪要拆解待办",
                workflow_type="meeting_minutes",
                input_payload={
                    "meeting_title": "AI 增长周会",
                    "notes": "1. 张敏本周五前整理竞品投放复盘；2. 陈涛下周二前提交销售线索分层方案；3. 王晨今天下班前确认客户试点名单。",
                },
                expected_status="completed",
                expected_keywords=("行动项", "负责人", "总结"),
            ),
        ),
    ),
)


DEFAULT_PROMPT_PROFILE_ID = BUILTIN_PROMPT_PROFILES[0].profile_id
DEFAULT_ROUTING_POLICY_ID = ROUTING_POLICIES[1].policy_id

_MODEL_INDEX = {item.model_name: item for item in AVAILABLE_MODELS}
_ROUTING_INDEX = {item.policy_id: item for item in ROUTING_POLICIES}
_DATASET_INDEX = {item.dataset_id: item for item in EVALUATION_DATASETS}


def list_model_options() -> list[dict[str, str]]:
    return [
        {"model_name": item.model_name, "label": item.label, "description": item.description}
        for item in AVAILABLE_MODELS
    ]


def list_routing_policies() -> list[dict[str, str]]:
    return [
        {"policy_id": item.policy_id, "name": item.name, "description": item.description}
        for item in ROUTING_POLICIES
    ]


def list_evaluation_datasets() -> list[dict[str, str]]:
    return [
        {
            "dataset_id": item.dataset_id,
            "name": item.name,
            "description": item.description,
            "case_count": str(len(item.cases)),
        }
        for item in EVALUATION_DATASETS
    ]


def get_routing_policy(policy_id: str | None = None) -> RoutingPolicyDefinition:
    return _ROUTING_INDEX.get(policy_id or DEFAULT_ROUTING_POLICY_ID) or _ROUTING_INDEX[DEFAULT_ROUTING_POLICY_ID]


def get_evaluation_dataset(dataset_id: str) -> EvaluationDatasetDefinition:
    return _DATASET_INDEX[dataset_id]


def resolve_execution_profile(
    *,
    default_model_name: str,
    prompt_profile: PromptProfile,
    model_name_override: str | None = None,
    routing_policy_id: str | None = None,
) -> ExecutionProfile:
    primary_model_name = model_name_override if model_name_override in _MODEL_INDEX else default_model_name
    primary_model = _MODEL_INDEX.get(primary_model_name) or _MODEL_INDEX[default_model_name]
    routing_policy = get_routing_policy(routing_policy_id)
    return ExecutionProfile(
        primary_model_name=primary_model.model_name,
        primary_model_label=primary_model.label,
        prompt_profile=prompt_profile.as_ref(),
        routing_policy=RoutingPolicyRef(
            policy_id=routing_policy.policy_id,
            name=routing_policy.name,
            description=routing_policy.description,
        ),
        model_routes=routing_policy.resolve_routes(primary_model.model_name),
    )
