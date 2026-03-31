from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from statistics import mean
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.data import RISK_CUSTOMERS, SALES_DATA, WORKFLOW_TEMPLATES
from app.llm import LLMService
from app.models import (
    EvaluationRun,
    ExecutionProfile,
    PromptProfile,
    PromptProfileForm,
    ReviewDecision,
    RunStatus,
    ToolCall,
    WorkflowPlan,
    WorkflowRequest,
    WorkflowRun,
    WorkflowType,
)
from app.prompt_catalog import (
    BUILTIN_PROMPT_PROFILES,
    DEFAULT_PROMPT_PROFILE_ID,
    DEFAULT_ROUTING_POLICY_ID,
    get_evaluation_dataset,
    list_evaluation_datasets,
    list_model_options,
    list_routing_policies,
    resolve_execution_profile,
)
from app.repository import WorkflowRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowState(TypedDict, total=False):
    run: WorkflowRun
    request: WorkflowRequest
    execution_profile: ExecutionProfile
    prompt_profile: PromptProfile
    raw_result: dict[str, Any]
    analysis: dict[str, Any]
    deliverables: dict[str, Any]
    review: dict[str, Any]
    persist: bool


class PromptProfileService:
    def __init__(self, repository: WorkflowRepository) -> None:
        self.repository = repository
        self.repository.ensure_prompt_profiles(list(BUILTIN_PROMPT_PROFILES))

    def list_profiles(self, include_inactive: bool = False) -> list[PromptProfile]:
        return self.repository.list_prompt_profiles(include_inactive=include_inactive)

    def get_profile(self, profile_id: str | None) -> PromptProfile:
        resolved = self.repository.get_prompt_profile(profile_id or DEFAULT_PROMPT_PROFILE_ID)
        if resolved is None:
            fallback = self.repository.get_prompt_profile(DEFAULT_PROMPT_PROFILE_ID)
            if fallback is None:
                raise ValueError("prompt profile not found")
            return fallback
        return resolved

    def create_profile(self, form: PromptProfileForm) -> PromptProfile:
        if self.repository.get_prompt_profile(form.profile_id):
            raise ValueError("prompt profile id already exists")
        profile = PromptProfile(
            profile_id=form.profile_id,
            base_profile_id=form.base_profile_id,
            name=form.name,
            version=form.version,
            description=form.description,
            analyst_instruction=form.analyst_instruction,
            content_instruction=form.content_instruction,
            reviewer_instruction=form.reviewer_instruction,
            is_builtin=False,
            is_active=True,
        )
        return self.repository.save_prompt_profile(profile)

    def update_profile(self, profile_id: str, form: PromptProfileForm) -> PromptProfile:
        existing = self.repository.get_prompt_profile(profile_id)
        if existing is None:
            raise ValueError("prompt profile not found")
        if existing.is_builtin:
            raise ValueError("builtin prompt profile cannot be edited directly, please create a new version")
        existing.base_profile_id = form.base_profile_id
        existing.name = form.name
        existing.version = form.version
        existing.description = form.description
        existing.analyst_instruction = form.analyst_instruction
        existing.content_instruction = form.content_instruction
        existing.reviewer_instruction = form.reviewer_instruction
        existing.updated_at = utc_now()
        return self.repository.save_prompt_profile(existing)


class PlannerAgent:
    def plan(self, request: WorkflowRequest) -> WorkflowPlan:
        title = self._workflow_title(request.workflow_type)
        objective = self._objective(request.workflow_type, request.input_payload)
        steps = {
            WorkflowType.SALES_FOLLOWUP: [
                "读取销售数据并聚合转化漏斗",
                "识别高风险客户和周期瓶颈",
                "整理跟进动作与资源建议",
                "给出是否需要人工确认的判断",
            ],
            WorkflowType.MARKETING_CAMPAIGN: [
                "梳理目标产品、受众和渠道",
                "生成渠道内容和投放建议",
                "补充合规提示与复用素材",
                "判断是否需要人工审核后发布",
            ],
            WorkflowType.SUPPORT_TRIAGE: [
                "解析工单内容并分类优先级",
                "生成回复草稿和升级建议",
                "识别高风险或上线故障类工单",
                "判断是否需要人工接管",
            ],
            WorkflowType.MEETING_MINUTES: [
                "抽取会议待办、负责人和截止时间",
                "整理会后同步摘要",
                "补充执行提醒和风险项",
                "判断是否可直接流转",
            ],
        }[request.workflow_type]
        expected_outputs = {
            WorkflowType.SALES_FOLLOWUP: ["分析结论", "跟进计划", "经理备注"],
            WorkflowType.MARKETING_CAMPAIGN: ["渠道文案", "投放建议", "审核提示"],
            WorkflowType.SUPPORT_TRIAGE: ["工单分类", "回复草稿", "升级决策"],
            WorkflowType.MEETING_MINUTES: ["行动项", "负责人分配", "会后摘要"],
        }[request.workflow_type]
        return WorkflowPlan(
            workflow_type=request.workflow_type,
            objective=f"{title}：{objective}",
            steps=steps,
            expected_outputs=expected_outputs,
        )

    @staticmethod
    def _workflow_title(workflow_type: WorkflowType) -> str:
        for template in WORKFLOW_TEMPLATES:
            if template.workflow_type == workflow_type:
                return template.title
        return workflow_type.value

    @staticmethod
    def _objective(workflow_type: WorkflowType, payload: dict[str, Any]) -> str:
        if workflow_type == WorkflowType.SALES_FOLLOWUP:
            return f"分析 {payload.get('period', '当前周期')} {payload.get('region', '全区域')} 销售表现并输出跟进计划"
        if workflow_type == WorkflowType.MARKETING_CAMPAIGN:
            return f"为 {payload.get('product_name', '目标产品')} 生成多渠道营销内容"
        if workflow_type == WorkflowType.SUPPORT_TRIAGE:
            return f"处理 {len(payload.get('tickets', []))} 条客服工单并判断是否升级"
        return f"整理《{payload.get('meeting_title', '会议')}》纪要并落成执行项"


class ToolCenter:
    def run(self, workflow_type: WorkflowType, payload: dict[str, Any]) -> tuple[dict[str, Any], ToolCall]:
        if workflow_type == WorkflowType.SALES_FOLLOWUP:
            result = self._sales_analytics(payload)
            return result, ToolCall(name="sales_analytics_tool", input=payload, output=result)
        if workflow_type == WorkflowType.MARKETING_CAMPAIGN:
            result = self._marketing_brief(payload)
            return result, ToolCall(name="marketing_brief_tool", input=payload, output=result)
        if workflow_type == WorkflowType.SUPPORT_TRIAGE:
            result = self._support_triage(payload)
            return result, ToolCall(name="support_triage_tool", input=payload, output=result)
        result = self._meeting_extract(payload)
        return result, ToolCall(name="meeting_minutes_tool", input=payload, output=result)

    def _sales_analytics(self, payload: dict[str, Any]) -> dict[str, Any]:
        region = payload.get("region")
        reps = set(payload.get("sales_reps", []))
        rows = [
            row for row in SALES_DATA
            if (not region or row["region"] == region) and (not reps or row["rep"] in reps)
        ]
        if not rows:
            rows = SALES_DATA[:]
        lead_count = sum(item["leads"] for item in rows)
        qualified = sum(item["qualified"] for item in rows)
        deals = sum(item["deals"] for item in rows)
        avg_cycle_days = round(mean(item["avg_cycle_days"] for item in rows), 1)
        conversion_rate = round(deals / lead_count, 2) if lead_count else 0.0
        qualified_rate = round(qualified / lead_count, 2) if lead_count else 0.0
        risk_customers = [item for item in RISK_CUSTOMERS if not reps or item["owner"] in reps]
        return {
            "focus_metric": payload.get("focus_metric", "conversion_rate"),
            "period": payload.get("period", "当前周期"),
            "region": region or "全区域",
            "lead_count": lead_count,
            "qualified_leads": qualified,
            "deals": deals,
            "conversion_rate": conversion_rate,
            "qualified_rate": qualified_rate,
            "avg_cycle_days": avg_cycle_days,
            "risk_customers": risk_customers,
        }

    def _marketing_brief(self, payload: dict[str, Any]) -> dict[str, Any]:
        channels = payload.get("channels", [])
        product = payload.get("product_name", "目标产品")
        benefits = payload.get("key_benefits", [])
        return {
            "product_name": product,
            "audience": payload.get("audience", "目标用户"),
            "tone": payload.get("tone", "专业"),
            "core_benefits": benefits,
            "channels": [
                {
                    "channel": channel,
                    "angle": f"突出{benefits[min(index, len(benefits) - 1)]}" if benefits else "突出效率提升",
                }
                for index, channel in enumerate(channels)
            ],
            "compliance_flags": ["需要人工审核对外表述", "避免承诺绝对效果"],
            "launch_goal": f"围绕 {product} 完成多渠道内容预热",
        }

    def _support_triage(self, payload: dict[str, Any]) -> dict[str, Any]:
        results = []
        handoff = False
        for ticket in payload.get("tickets", []):
            message = ticket.get("message", "")
            priority = "medium"
            category = "咨询"
            if any(keyword in message for keyword in ["报错", "故障", "恢复", "上线"]):
                priority = "critical"
                category = "故障"
                handoff = True
            elif any(keyword in message for keyword in ["退款", "投诉"]):
                priority = "high"
                category = "投诉"
                handoff = True
            elif "开票" in message or "合同" in message:
                priority = "low"
                category = "商务支持"
            results.append(
                {
                    "customer": ticket.get("customer", "未知客户"),
                    "category": category,
                    "priority": priority,
                    "reply_outline": f"先确认问题背景，再给出{category}处理路径。",
                }
            )
        return {
            "ticket_count": len(results),
            "tickets": results,
            "needs_handoff": handoff,
        }

    def _meeting_extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        notes = payload.get("notes", "")
        items = []
        for raw in re.split(r"[；;。]\s*|\n", notes):
            raw = raw.strip()
            if not raw:
                continue
            raw = re.sub(r"^\d+\.\s*", "", raw)
            owner = raw[:2] if len(raw) >= 2 else "待定"
            deadline_match = re.search(r"(本周[一二三四五六日天]|下周[一二三四五六日天]|今天|明天)", raw)
            items.append(
                {
                    "owner": owner,
                    "deadline": deadline_match.group(1) if deadline_match else "待确认",
                    "task": raw,
                }
            )
        return {
            "meeting_title": payload.get("meeting_title", "会议"),
            "action_items": items,
            "summary": f"共提取 {len(items)} 条待办事项",
        }


class AnalystAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def run(
        self,
        run: WorkflowRun,
        raw_result: dict[str, Any],
        prompt_profile: PromptProfile,
        execution_profile: ExecutionProfile,
    ) -> tuple[dict[str, Any], Any]:
        fallback = self._fallback(run.workflow_type, raw_result)
        system_prompt = (
            "你是企业 AI 工作流中的分析 Agent。"
            f"{prompt_profile.analyst_instruction}"
            "请输出 JSON，字段必须包含 summary、insights、action_plan。"
        )
        user_prompt = json.dumps(
            {
                "workflow_type": run.workflow_type.value,
                "objective": run.objective,
                "raw_result": raw_result,
            },
            ensure_ascii=False,
        )
        response = self.llm_service.generate_json(
            route_target="analyst",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            execution_profile=execution_profile,
        )
        return response.payload, response.call

    def _fallback(self, workflow_type: WorkflowType, raw_result: dict[str, Any]) -> dict[str, Any]:
        if workflow_type == WorkflowType.SALES_FOLLOWUP:
            return {
                "summary": f"整体成交转化率为 {raw_result.get('conversion_rate', 0):.0%}，平均销售周期 {raw_result.get('avg_cycle_days', 0)} 天。",
                "insights": [
                    "当前销售漏斗存在明显瓶颈，需要优先处理高风险客户。",
                    "销售周期偏长，说明推进节奏和资源协调仍有阻塞。",
                ],
                "action_plan": [
                    "针对高风险客户安排一对一复盘和下一步推进动作。",
                    "补充 ROI 材料，推动卡点客户决策。",
                ],
            }
        if workflow_type == WorkflowType.MARKETING_CAMPAIGN:
            return {
                "summary": "内容生成已完成，但对外表达和品牌调性仍需人工确认。",
                "insights": ["不同渠道需要差异化表达。", "投放前需要检查合规和夸张表述。"],
                "action_plan": ["保留一版对外发布草案。", "交由人工审核后再统一发布。"],
            }
        if workflow_type == WorkflowType.SUPPORT_TRIAGE:
            return {
                "summary": f"共处理 {raw_result.get('ticket_count', 0)} 条工单，存在需要人工接管的高风险问题。",
                "insights": ["故障类工单影响上线。", "商务支持类工单可标准化回复。"],
                "action_plan": ["高优先级工单立即升级值班工程师。", "低优先级工单使用模板回复。"],
            }
        return {
            "summary": raw_result.get("summary", "会议纪要已完成结构化整理。"),
            "insights": ["行动项已经拆解为负责人和时间节点。", "适合直接同步到任务面板。"],
            "action_plan": ["确认关键任务负责人。", "发送会后总结并跟踪执行。"],
        }


class ContentAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def run(
        self,
        run: WorkflowRun,
        raw_result: dict[str, Any],
        analysis: dict[str, Any],
        prompt_profile: PromptProfile,
        execution_profile: ExecutionProfile,
    ) -> tuple[dict[str, Any], Any]:
        fallback = self._fallback(run.workflow_type, raw_result, analysis)
        system_prompt = (
            "你是企业 AI 工作流中的内容 Agent。"
            f"{prompt_profile.content_instruction}"
            "请输出 JSON，字段必须包含 deliverables 和 manager_note。"
        )
        user_prompt = json.dumps(
            {
                "workflow_type": run.workflow_type.value,
                "objective": run.objective,
                "raw_result": raw_result,
                "analysis": analysis,
            },
            ensure_ascii=False,
        )
        response = self.llm_service.generate_json(
            route_target="content",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=fallback,
            execution_profile=execution_profile,
        )
        return response.payload, response.call

    def _fallback(self, workflow_type: WorkflowType, raw_result: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
        if workflow_type == WorkflowType.SALES_FOLLOWUP:
            return {
                "deliverables": {
                    "daily_brief": analysis.get("summary", ""),
                    "manager_note": "建议销售经理确认高风险客户的一对一辅导资源。",
                },
                "manager_note": "优先安排本周高风险客户复盘。",
            }
        if workflow_type == WorkflowType.MARKETING_CAMPAIGN:
            channels = raw_result.get("channels", [])
            return {
                "deliverables": {
                    "content_assets": [
                        {"channel": item["channel"], "copy": f"围绕{item['angle']}输出一版首发文案。"}
                        for item in channels
                    ],
                    "review_hint": "对外发布前需人工审核措辞和合规风险。",
                },
                "manager_note": "建议先审核，再按渠道分发。",
            }
        if workflow_type == WorkflowType.SUPPORT_TRIAGE:
            return {
                "deliverables": {
                    "reply_templates": [
                        {
                            "customer": ticket["customer"],
                            "reply": f"已收到{ticket['category']}问题，我们会尽快处理并同步进展。",
                        }
                        for ticket in raw_result.get("tickets", [])
                    ]
                },
                "manager_note": "高优先级工单需要人工确认升级路径。",
            }
        return {
            "deliverables": {
                "summary_mail": analysis.get("summary", ""),
                "task_board": raw_result.get("action_items", []),
            },
            "manager_note": "可直接同步到项目群和任务系统。",
        }


class ReviewerAgent:
    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    def run(
        self,
        run: WorkflowRun,
        raw_result: dict[str, Any],
        analysis: dict[str, Any],
        deliverables: dict[str, Any],
        prompt_profile: PromptProfile,
        execution_profile: ExecutionProfile,
    ) -> tuple[dict[str, Any], Any]:
        rule_review = self._rule_review(run.workflow_type, raw_result)
        system_prompt = (
            "你是企业 AI 工作流中的审核 Agent。"
            f"{prompt_profile.reviewer_instruction}"
            "请输出 JSON，字段必须包含 status、needs_human_review、score、reasons。"
        )
        user_prompt = json.dumps(
            {
                "workflow_type": run.workflow_type.value,
                "raw_result": raw_result,
                "analysis": analysis,
                "deliverables": deliverables,
                "rule_review": rule_review,
            },
            ensure_ascii=False,
        )
        response = self.llm_service.generate_json(
            route_target="reviewer",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback=rule_review,
            execution_profile=execution_profile,
        )
        merged = self._merge_review(rule_review, response.payload)
        return merged, response.call

    def _rule_review(self, workflow_type: WorkflowType, raw_result: dict[str, Any]) -> dict[str, Any]:
        if workflow_type == WorkflowType.MARKETING_CAMPAIGN:
            return {
                "status": "waiting_human",
                "needs_human_review": True,
                "score": 0.68,
                "reasons": ["对外营销内容默认进入人工审核，需确认品牌表述和合规边界。"],
            }
        if workflow_type == WorkflowType.SUPPORT_TRIAGE and raw_result.get("needs_handoff"):
            return {
                "status": "waiting_human",
                "needs_human_review": True,
                "score": 0.62,
                "reasons": ["存在高优先级故障工单，需要人工接管和升级确认。"],
            }
        if workflow_type == WorkflowType.SALES_FOLLOWUP and raw_result.get("risk_customers"):
            return {
                "status": "waiting_human",
                "needs_human_review": True,
                "score": 0.65,
                "reasons": ["涉及高风险客户和辅导计划，建议销售经理人工确认。"],
            }
        return {
            "status": "completed",
            "needs_human_review": False,
            "score": 0.88,
            "reasons": ["结果结构完整，可直接流转执行。"],
        }

    @staticmethod
    def _merge_review(rule_review: dict[str, Any], llm_review: dict[str, Any]) -> dict[str, Any]:
        status = str(llm_review.get("status") or rule_review.get("status") or "completed")
        status_alias = {"approved": "completed", "approve": "completed", "rejected": "failed", "reject": "failed"}
        status = status_alias.get(status, status)
        needs_human = bool(llm_review.get("needs_human_review", rule_review.get("needs_human_review", False)))
        if needs_human:
            status = "waiting_human"
        elif status == "waiting_human":
            status = "completed"
        score = float(llm_review.get("score", rule_review.get("score", 0.8)))
        reasons = [*rule_review.get("reasons", []), *llm_review.get("reasons", [])]
        filtered = []
        for reason in reasons:
            text = str(reason)
            if status == "waiting_human" and ("可直接流转" in text or "自动执行" in text):
                continue
            if status == "completed" and ("人工审核" in text or "人工确认" in text or "人工接管" in text):
                continue
            if text not in filtered:
                filtered.append(text)
        if not filtered:
            filtered = ["需要人工确认后再继续执行。"] if status == "waiting_human" else ["结果结构完整，可直接流转执行。"]
        return {
            "status": status,
            "needs_human_review": status == "waiting_human",
            "score": round(max(0.0, min(score, 1.0)), 2),
            "reasons": filtered,
        }


class WorkflowEngine:
    def __init__(self, repository: WorkflowRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self.prompt_service = PromptProfileService(repository)
        self.llm_service = LLMService(settings)
        self.planner = PlannerAgent()
        self.tool_center = ToolCenter()
        self.analyst = AnalystAgent(self.llm_service)
        self.content = ContentAgent(self.llm_service)
        self.reviewer = ReviewerAgent(self.llm_service)
        self.graph = self._build_graph()

    def list_templates(self) -> list[dict[str, Any]]:
        return [template.model_dump(mode="json") for template in WORKFLOW_TEMPLATES]

    def list_model_options(self) -> list[dict[str, str]]:
        return list_model_options()

    def list_routing_policies(self) -> list[dict[str, str]]:
        return list_routing_policies()

    def list_prompt_profiles(self) -> list[PromptProfile]:
        return self.prompt_service.list_profiles()

    def get_prompt_profile(self, profile_id: str) -> PromptProfile:
        return self.prompt_service.get_profile(profile_id)

    def create_prompt_profile(self, form: PromptProfileForm) -> PromptProfile:
        return self.prompt_service.create_profile(form)

    def update_prompt_profile(self, profile_id: str, form: PromptProfileForm) -> PromptProfile:
        return self.prompt_service.update_profile(profile_id, form)

    def list_runs(self) -> list[WorkflowRun]:
        return self.repository.list_all()

    def list_review_queue(self) -> list[WorkflowRun]:
        return self.repository.list_waiting_human()

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self.repository.get(run_id)

    def build_execution_profile(self, request: WorkflowRequest) -> tuple[PromptProfile, ExecutionProfile]:
        prompt_profile = self.prompt_service.get_profile(request.prompt_profile_id)
        execution_profile = resolve_execution_profile(
            default_model_name=self.settings.model_name,
            prompt_profile=prompt_profile,
            model_name_override=request.model_name_override,
            routing_policy_id=request.routing_policy_id or DEFAULT_ROUTING_POLICY_ID,
        )
        return prompt_profile, execution_profile

    def run_workflow(self, request: WorkflowRequest, persist: bool = True) -> WorkflowRun:
        prompt_profile, execution_profile = self.build_execution_profile(request)
        run = WorkflowRun(workflow_type=request.workflow_type, input_payload=request.input_payload)
        state: WorkflowState = {
            "run": run,
            "request": request,
            "execution_profile": execution_profile,
            "prompt_profile": prompt_profile,
            "persist": persist,
        }
        result = self.graph.invoke(state)
        final_run = result["run"]
        if persist:
            self.repository.save(final_run)
        return final_run

    def submit_review(self, run_id: str, approve: bool, comment: str, reviewer_name: str) -> WorkflowRun | None:
        run = self.repository.get(run_id)
        if run is None:
            return None
        run.review = ReviewDecision(
            status=RunStatus.COMPLETED if approve else RunStatus.FAILED,
            needs_human_review=not approve,
            score=run.review.score if run.review else 0.7,
            reasons=[comment or ("审核通过，允许流转执行。" if approve else "审核驳回，请补充后重试。")],
        )
        run.touch(status=run.review.status, current_step="human_review")
        verdict = "通过" if approve else "驳回"
        run.add_log("ReviewerAgent", f"审核负责人 {reviewer_name} 已{verdict}任务：{comment or '无补充说明'}")
        self.repository.save(run)
        return run

    def get_graph_definition(self) -> dict[str, Any]:
        return {
            "runtime": "langgraph",
            "entrypoint": "planner",
            "nodes": ["planner", "operator", "analyst", "content", "reviewer"],
            "edges": [
                {"from": "planner", "to": "operator"},
                {"from": "operator", "to": "analyst"},
                {"from": "analyst", "to": "content"},
                {"from": "content", "to": "reviewer"},
                {"from": "reviewer", "to": "END"},
            ],
        }

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("planner", self._planner_node)
        graph.add_node("operator", self._operator_node)
        graph.add_node("analyst", self._analyst_node)
        graph.add_node("content", self._content_node)
        graph.add_node("reviewer", self._reviewer_node)
        graph.add_edge(START, "planner")
        graph.add_edge("planner", "operator")
        graph.add_edge("operator", "analyst")
        graph.add_edge("analyst", "content")
        graph.add_edge("content", "reviewer")
        graph.add_edge("reviewer", END)
        return graph.compile()

    def _save_if_needed(self, state: WorkflowState) -> None:
        if state.get("persist"):
            self.repository.save(state["run"])

    def _planner_node(self, state: WorkflowState) -> WorkflowState:
        run = state["run"]
        request = state["request"]
        plan = self.planner.plan(request)
        run.plan = plan
        run.objective = plan.objective
        run.touch(status=RunStatus.PLANNING, current_step="planner")
        run.add_log("PlannerAgent", f"已生成执行计划，共 {len(plan.steps)} 步。")
        self._save_if_needed(state)
        return state

    def _operator_node(self, state: WorkflowState) -> WorkflowState:
        run = state["run"]
        request = state["request"]
        raw_result, tool_call = self.tool_center.run(request.workflow_type, request.input_payload)
        state["raw_result"] = raw_result
        run.touch(status=RunStatus.EXECUTING, current_step="operator")
        run.add_log("OperatorAgent", f"完成工具调用：{tool_call.name}", tool_call=tool_call)
        self._save_if_needed(state)
        return state

    def _analyst_node(self, state: WorkflowState) -> WorkflowState:
        run = state["run"]
        analysis, llm_call = self.analyst.run(run, state["raw_result"], state["prompt_profile"], state["execution_profile"])
        state["analysis"] = analysis
        run.touch(status=RunStatus.EXECUTING, current_step="analyst")
        run.add_log("AnalystAgent", "已完成结果分析与行动建议整理。", llm_call=llm_call)
        self._save_if_needed(state)
        return state

    def _content_node(self, state: WorkflowState) -> WorkflowState:
        run = state["run"]
        deliverables, llm_call = self.content.run(
            run,
            state["raw_result"],
            state["analysis"],
            state["prompt_profile"],
            state["execution_profile"],
        )
        state["deliverables"] = deliverables
        run.touch(status=RunStatus.REVIEWING, current_step="content")
        run.add_log("ContentAgent", "已补充业务可直接使用的输出内容。", llm_call=llm_call)
        self._save_if_needed(state)
        return state

    def _reviewer_node(self, state: WorkflowState) -> WorkflowState:
        run = state["run"]
        review_payload, llm_call = self.reviewer.run(
            run,
            state["raw_result"],
            state["analysis"],
            state["deliverables"],
            state["prompt_profile"],
            state["execution_profile"],
        )
        run.review = ReviewDecision(**review_payload)
        run.result = {
            "execution_profile": state["execution_profile"].model_dump(mode="json"),
            "prompt_profile_snapshot": state["prompt_profile"].model_dump(mode="json"),
            "raw_result": state["raw_result"],
            "analysis": state["analysis"],
            "deliverables": state["deliverables"],
            "metadata": state["request"].metadata,
        }
        final_status = RunStatus.WAITING_HUMAN if review_payload["status"] == "waiting_human" else RunStatus.COMPLETED
        run.touch(status=final_status, current_step="reviewer")
        message = "已完成质量审核与人工接管判断。"
        if final_status == RunStatus.WAITING_HUMAN:
            message = "已完成质量审核，任务进入人工接管。"
        run.add_log("ReviewerAgent", message, llm_call=llm_call)
        self._save_if_needed(state)
        return state


class EvaluationService:
    def __init__(self, repository: WorkflowRepository, engine: WorkflowEngine) -> None:
        self.repository = repository
        self.engine = engine

    def list_datasets(self) -> list[dict[str, str]]:
        return list_evaluation_datasets()

    def list_evaluations(self) -> list[EvaluationRun]:
        return self.repository.list_evaluations()

    def get_evaluation(self, evaluation_id: str) -> EvaluationRun | None:
        return self.repository.get_evaluation(evaluation_id)

    def run_evaluation(
        self,
        *,
        dataset_id: str,
        candidate_request: WorkflowRequest,
        baseline_request: WorkflowRequest,
    ) -> EvaluationRun:
        dataset = get_evaluation_dataset(dataset_id)
        candidate_prompt, candidate_profile = self.engine.build_execution_profile(candidate_request)
        baseline_prompt, baseline_profile = self.engine.build_execution_profile(baseline_request)
        case_results: list[dict[str, Any]] = []
        for case in dataset.cases:
            candidate_run = self.engine.run_workflow(
                WorkflowRequest(
                    workflow_type=WorkflowType(case.workflow_type),
                    input_payload=case.input_payload,
                    model_name_override=candidate_request.model_name_override,
                    prompt_profile_id=candidate_prompt.profile_id,
                    routing_policy_id=candidate_request.routing_policy_id,
                    metadata={"evaluation_case_id": case.case_id, "lane": "candidate"},
                ),
                persist=False,
            )
            baseline_run = self.engine.run_workflow(
                WorkflowRequest(
                    workflow_type=WorkflowType(case.workflow_type),
                    input_payload=case.input_payload,
                    model_name_override=baseline_request.model_name_override,
                    prompt_profile_id=baseline_prompt.profile_id,
                    routing_policy_id=baseline_request.routing_policy_id,
                    metadata={"evaluation_case_id": case.case_id, "lane": "baseline"},
                ),
                persist=False,
            )
            candidate_score = self._score_case(candidate_run, case.expected_status, case.expected_keywords)
            baseline_score = self._score_case(baseline_run, case.expected_status, case.expected_keywords)
            case_results.append(
                {
                    "case_id": case.case_id,
                    "title": case.title,
                    "expected_status": case.expected_status,
                    "candidate": candidate_score,
                    "baseline": baseline_score,
                    "delta": round(candidate_score["score"] - baseline_score["score"], 3),
                }
            )
        evaluation = EvaluationRun(
            dataset_id=dataset.dataset_id,
            dataset_name=dataset.name,
            candidate_profile=candidate_profile,
            baseline_profile=baseline_profile,
            summary=self._build_summary(case_results),
            case_results=case_results,
        )
        return self.repository.save_evaluation(evaluation)

    def _score_case(self, run: WorkflowRun, expected_status: str, expected_keywords: tuple[str, ...]) -> dict[str, Any]:
        text = json.dumps(run.result, ensure_ascii=False)
        keyword_hits = sum(1 for keyword in expected_keywords if keyword in text)
        llm_calls = [log.llm_call for log in run.logs if log.llm_call]
        total_tokens = sum(call.total_tokens for call in llm_calls)
        total_latency = sum(call.latency_ms for call in llm_calls)
        status_score = 1.0 if run.status.value == expected_status else 0.0
        keyword_score = keyword_hits / max(len(expected_keywords), 1)
        review_score = run.review.score if run.review else 0.0
        structure_score = 1.0 if {"raw_result", "analysis", "deliverables"} <= set(run.result.keys()) else 0.0
        score = round(status_score * 0.3 + keyword_score * 0.25 + review_score * 0.25 + structure_score * 0.2, 3)
        return {
            "status": run.status.value,
            "score": score,
            "review_score": review_score,
            "keyword_hits": keyword_hits,
            "expected_keywords": list(expected_keywords),
            "total_tokens": total_tokens,
            "latency_ms": total_latency,
            "needs_human_review": bool(run.review and run.review.needs_human_review),
        }

    def _build_summary(self, case_results: list[dict[str, Any]]) -> dict[str, Any]:
        candidate_scores = [case["candidate"]["score"] for case in case_results]
        baseline_scores = [case["baseline"]["score"] for case in case_results]
        candidate_tokens = [case["candidate"]["total_tokens"] for case in case_results]
        baseline_tokens = [case["baseline"]["total_tokens"] for case in case_results]
        candidate_latency = [case["candidate"]["latency_ms"] for case in case_results]
        baseline_latency = [case["baseline"]["latency_ms"] for case in case_results]
        candidate_handoff = [1 if case["candidate"]["needs_human_review"] else 0 for case in case_results]
        baseline_handoff = [1 if case["baseline"]["needs_human_review"] else 0 for case in case_results]
        return {
            "case_count": len(case_results),
            "candidate_avg_score": round(mean(candidate_scores), 3) if candidate_scores else 0.0,
            "baseline_avg_score": round(mean(baseline_scores), 3) if baseline_scores else 0.0,
            "score_delta": round((mean(candidate_scores) - mean(baseline_scores)), 3) if case_results else 0.0,
            "candidate_avg_tokens": round(mean(candidate_tokens)) if candidate_tokens else 0,
            "baseline_avg_tokens": round(mean(baseline_tokens)) if baseline_tokens else 0,
            "candidate_avg_latency_ms": round(mean(candidate_latency)) if candidate_latency else 0,
            "baseline_avg_latency_ms": round(mean(baseline_latency)) if baseline_latency else 0,
            "candidate_handoff_rate": round(mean(candidate_handoff) * 100, 1) if candidate_handoff else 0.0,
            "baseline_handoff_rate": round(mean(baseline_handoff) * 100, 1) if baseline_handoff else 0.0,
        }
