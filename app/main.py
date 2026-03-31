from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import ROLE_ADMIN, ROLE_OPERATOR, ROLE_REVIEWER, AuthService, AuthUser
from app.cache import CacheStore
from app.config import Settings
from app.db import Database
from app.models import PromptProfileForm, ReviewSubmission, WorkflowRequest, WorkflowRun, WorkflowType
from app.prompt_catalog import DEFAULT_PROMPT_PROFILE_ID, DEFAULT_ROUTING_POLICY_ID
from app.repository import WorkflowRepository
from app.services import EvaluationService, WorkflowEngine


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
settings = Settings.from_env()
database = Database(settings.database_url)
cache = CacheStore(settings.redis_url)

app = FastAPI(title="FlowPilot", version="0.5.0")
repository = WorkflowRepository(database, cache)
engine = WorkflowEngine(repository, settings)
evaluation_service = EvaluationService(repository, engine)
auth_service = AuthService(settings, database)


def _safe_next_path(next_path: str | None) -> str:
    if next_path and next_path.startswith("/"):
        return next_path
    return "/dashboard"


def _redirect_to_login(request: Request) -> RedirectResponse:
    next_path = request.url.path
    if request.url.query:
        next_path = f"{next_path}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={quote(next_path)}", status_code=303)


def _page_user(request: Request, roles: set[str] | None = None) -> AuthUser | None:
    user = auth_service.get_user_from_request(request)
    if user is None:
        return None
    if roles and user.role not in roles:
        raise HTTPException(status_code=403, detail="permission denied")
    return user


def _template_response(request: Request, template_name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def _workflow_titles() -> dict[str, str]:
    return {item["workflow_type"]: item["title"] for item in engine.list_templates()}


def _extract_execution_profile(run: WorkflowRun) -> dict[str, str]:
    payload = run.result.get("execution_profile") if isinstance(run.result, dict) else None
    if not isinstance(payload, dict):
        return {
            "primary_model_name": settings.model_name,
            "primary_model_label": settings.model_name,
            "prompt_profile_id": DEFAULT_PROMPT_PROFILE_ID,
            "prompt_profile_name": "平衡版",
            "prompt_profile_version": "v1",
            "prompt_profile_description": "",
            "routing_policy_id": DEFAULT_ROUTING_POLICY_ID,
            "routing_policy_name": "均衡路由",
            "routing_policy_description": "",
        }
    prompt_profile = payload.get("prompt_profile") or {}
    routing_policy = payload.get("routing_policy") or {}
    return {
        "primary_model_name": payload.get("primary_model_name", settings.model_name),
        "primary_model_label": payload.get("primary_model_label", payload.get("primary_model_name", settings.model_name)),
        "prompt_profile_id": prompt_profile.get("profile_id", DEFAULT_PROMPT_PROFILE_ID),
        "prompt_profile_name": prompt_profile.get("name", "平衡版"),
        "prompt_profile_version": prompt_profile.get("version", "v1"),
        "prompt_profile_description": prompt_profile.get("description", ""),
        "routing_policy_id": routing_policy.get("policy_id", DEFAULT_ROUTING_POLICY_ID),
        "routing_policy_name": routing_policy.get("name", "均衡路由"),
        "routing_policy_description": routing_policy.get("description", ""),
    }


def _normalized_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _summarize_runs(runs: list[WorkflowRun]) -> dict[str, int]:
    return {
        "total_runs": len(runs),
        "completed_runs": sum(1 for run in runs if run.status.value == "completed"),
        "waiting_review": sum(1 for run in runs if run.status.value == "waiting_human"),
        "failed_runs": sum(1 for run in runs if run.status.value == "failed"),
    }


def _build_timeline(run: WorkflowRun) -> list[dict]:
    if not run.logs:
        return []
    total_seconds = max((run.updated_at - run.created_at).total_seconds(), 1)
    points: list[dict] = []
    count = len(run.logs)
    for index, log in enumerate(run.logs):
        offset_seconds = max((log.timestamp - run.created_at).total_seconds(), 0)
        left = 50 if count == 1 else round((offset_seconds / total_seconds) * 100, 2)
        next_timestamp = run.logs[index + 1].timestamp if index + 1 < count else run.updated_at
        duration_seconds = max((next_timestamp - log.timestamp).total_seconds(), 0)
        points.append(
            {
                "index": index + 1,
                "agent": log.agent,
                "message": log.message,
                "timestamp": log.timestamp.astimezone().strftime("%H:%M:%S"),
                "left": min(max(left, 2), 98),
                "duration": f"{duration_seconds:.2f}s",
                "tool_name": log.tool_call.name if log.tool_call else "",
            }
        )
    return points


def _build_run_table(runs: list[WorkflowRun]) -> list[dict]:
    titles = _workflow_titles()
    rows = []
    for run in runs:
        execution_profile = _extract_execution_profile(run)
        rows.append(
            {
                "id": run.id,
                "title": titles.get(run.workflow_type.value, run.workflow_type.value),
                "workflow_type": run.workflow_type.value,
                "status": run.status.value,
                "current_step": run.current_step,
                "objective": run.objective,
                "review_score": f"{run.review.score:.2f}" if run.review else "--",
                "updated_at": run.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "created_at": run.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "model_name": execution_profile["primary_model_name"],
                "prompt_profile_label": f"{execution_profile['prompt_profile_name']} {execution_profile['prompt_profile_version']}",
                "routing_policy_name": execution_profile["routing_policy_name"],
            }
        )
    return rows


def _build_llm_summary(run: WorkflowRun) -> dict[str, object]:
    llm_calls = [log.llm_call for log in run.logs if log.llm_call is not None]
    if not llm_calls:
        return {
            "total_requests": 0,
            "model_names": [],
            "route_targets": [],
            "prompt_profiles": [],
            "routing_policies": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "total_latency_ms": 0,
            "avg_latency_ms": 0,
            "fallback_requests": 0,
        }
    total_latency_ms = sum(call.latency_ms for call in llm_calls)
    return {
        "total_requests": len(llm_calls),
        "model_names": sorted({call.model_name for call in llm_calls}),
        "route_targets": sorted({call.route_target for call in llm_calls}),
        "prompt_profiles": sorted({f"{call.prompt_profile_name} {call.prompt_profile_version}" for call in llm_calls}),
        "routing_policies": sorted({call.routing_policy_name or "" for call in llm_calls}),
        "prompt_tokens": sum(call.prompt_tokens for call in llm_calls),
        "completion_tokens": sum(call.completion_tokens for call in llm_calls),
        "total_tokens": sum(call.total_tokens for call in llm_calls),
        "total_latency_ms": total_latency_ms,
        "avg_latency_ms": round(total_latency_ms / len(llm_calls)),
        "fallback_requests": sum(1 for call in llm_calls if call.used_fallback),
    }


def _build_compare_summary(runs: list[WorkflowRun]) -> dict[str, object]:
    grouped: dict[tuple[str, str, str, str], list[WorkflowRun]] = defaultdict(list)
    for run in runs:
        profile = _extract_execution_profile(run)
        grouped[
            (
                run.workflow_type.value,
                profile["primary_model_name"],
                profile["prompt_profile_id"],
                profile["routing_policy_id"],
            )
        ].append(run)

    titles = _workflow_titles()
    rows = []
    for (workflow_type, model_name, prompt_profile_id, routing_policy_id), group_runs in grouped.items():
        scores = [run.review.score for run in group_runs if run.review]
        llm_calls = [log.llm_call for run in group_runs for log in run.logs if log.llm_call]
        profile = _extract_execution_profile(group_runs[0])
        waiting_count = sum(1 for run in group_runs if run.status.value == "waiting_human")
        fallback_count = sum(1 for call in llm_calls if call.used_fallback)
        rows.append(
            {
                "workflow_title": titles.get(workflow_type, workflow_type),
                "model_name": model_name,
                "prompt_profile_id": prompt_profile_id,
                "prompt_profile_label": f"{profile['prompt_profile_name']} {profile['prompt_profile_version']}",
                "routing_policy_id": routing_policy_id,
                "routing_policy_name": profile["routing_policy_name"],
                "run_count": len(group_runs),
                "avg_score": round(mean(scores), 2) if scores else 0.0,
                "avg_tokens": round(mean(call.total_tokens for call in llm_calls)) if llm_calls else 0,
                "avg_latency_ms": round(mean(call.latency_ms for call in llm_calls)) if llm_calls else 0,
                "handoff_rate": round(waiting_count / len(group_runs) * 100, 1) if group_runs else 0.0,
                "fallback_rate": round(fallback_count / len(llm_calls) * 100, 1) if llm_calls else 0.0,
                "latest_run_at": max(_normalized_datetime(run.updated_at) for run in group_runs).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    rows.sort(key=lambda item: (-item["run_count"], item["workflow_title"], item["model_name"]))
    return {
        "rows": rows,
        "combination_count": len(rows),
        "run_count": len(runs),
        "model_count": len({row["model_name"] for row in rows}),
        "prompt_count": len({row["prompt_profile_id"] for row in rows}),
        "routing_count": len({row["routing_policy_id"] for row in rows}),
    }


def _common_context(request: Request, user: AuthUser, active_page: str, **kwargs: object) -> dict[str, object]:
    return {
        "request": request,
        "current_user": user,
        "capabilities": auth_service.capabilities_for(user),
        "active_page": active_page,
        **kwargs,
    }


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> RedirectResponse:
    if auth_service.get_user_from_request(request) is None:
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard"):
    user = auth_service.get_user_from_request(request)
    if user is not None:
        return RedirectResponse(url=_safe_next_path(next), status_code=303)
    return _template_response(request, "login.html", {"request": request, "next": _safe_next_path(next), "error": ""})


@app.post("/login", response_class=HTMLResponse)
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
):
    user = auth_service.authenticate(username, password)
    safe_next = _safe_next_path(next)
    if user is None:
        return _template_response(
            request,
            "login.html",
            {"request": request, "next": safe_next, "error": "用户名或密码错误，请重试。"},
            status_code=401,
        )
    response = RedirectResponse(url=safe_next, status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        auth_service.build_session_cookie(user),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 8,
    )
    return response


@app.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    user = _page_user(request)
    if user is None:
        return _redirect_to_login(request)
    runs = engine.list_runs()
    queue = engine.list_review_queue() if user.role in {ROLE_REVIEWER, ROLE_ADMIN} else []
    return _template_response(
        request,
        "dashboard.html",
        _common_context(
            request,
            user,
            "dashboard",
            templates_data=engine.list_templates(),
            model_options=engine.list_model_options(),
            prompt_profiles=engine.list_prompt_profiles(),
            routing_policies=engine.list_routing_policies(),
            default_model_name=settings.model_name,
            default_prompt_profile_id=DEFAULT_PROMPT_PROFILE_ID,
            default_routing_policy_id=DEFAULT_ROUTING_POLICY_ID,
            recent_runs=_build_run_table(runs[:6]),
            summary=_summarize_runs(runs),
            review_queue=_build_run_table(queue[:5]),
            graph=engine.get_graph_definition(),
        ),
    )


@app.post("/dashboard/run")
def dashboard_run_action(
    request: Request,
    workflow_type: str = Form(...),
    payload_json: str = Form("{}"),
    selected_model_name: str = Form(settings.model_name, alias="model_name"),
    prompt_profile_id: str = Form(DEFAULT_PROMPT_PROFILE_ID),
    routing_policy_id: str = Form(DEFAULT_ROUTING_POLICY_ID),
) -> RedirectResponse:
    user = _page_user(request, {ROLE_OPERATOR, ROLE_ADMIN})
    if user is None:
        return _redirect_to_login(request)
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"payload_json is invalid JSON: {exc}") from exc
    run = engine.run_workflow(
        WorkflowRequest(
            workflow_type=WorkflowType(workflow_type),
            input_payload=payload,
            model_name_override=selected_model_name,
            prompt_profile_id=prompt_profile_id,
            routing_policy_id=routing_policy_id,
        )
    )
    return RedirectResponse(url=f"/runs/{run.id}", status_code=303)


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request):
    user = _page_user(request)
    if user is None:
        return _redirect_to_login(request)
    runs = engine.list_runs()
    return _template_response(
        request,
        "runs.html",
        _common_context(request, user, "runs", runs=_build_run_table(runs), summary=_summarize_runs(runs)),
    )


@app.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    user = _page_user(request)
    if user is None:
        return _redirect_to_login(request)
    runs = engine.list_runs()
    return _template_response(
        request,
        "compare.html",
        _common_context(
            request,
            user,
            "compare",
            compare_summary=_build_compare_summary(runs),
            model_options=engine.list_model_options(),
            prompt_profiles=engine.list_prompt_profiles(),
            routing_policies=engine.list_routing_policies(),
            latest_runs=_build_run_table(runs[:8]),
        ),
    )


@app.get("/prompts", response_class=HTMLResponse)
def prompts_page(request: Request):
    user = _page_user(request)
    if user is None:
        return _redirect_to_login(request)
    return _template_response(
        request,
        "prompts.html",
        _common_context(
            request,
            user,
            "prompts",
            prompt_profiles=engine.list_prompt_profiles(),
        ),
    )


@app.post("/prompts/create")
def prompt_create_action(
    request: Request,
    profile_id: str = Form(...),
    base_profile_id: str = Form(""),
    name: str = Form(...),
    version: str = Form(...),
    description: str = Form(...),
    analyst_instruction: str = Form(...),
    content_instruction: str = Form(...),
    reviewer_instruction: str = Form(...),
) -> RedirectResponse:
    user = _page_user(request, {ROLE_ADMIN})
    if user is None:
        return _redirect_to_login(request)
    engine.create_prompt_profile(
        PromptProfileForm(
            profile_id=profile_id,
            base_profile_id=base_profile_id or None,
            name=name,
            version=version,
            description=description,
            analyst_instruction=analyst_instruction,
            content_instruction=content_instruction,
            reviewer_instruction=reviewer_instruction,
        )
    )
    return RedirectResponse(url="/prompts", status_code=303)


@app.post("/prompts/{profile_id}/update")
def prompt_update_action(
    profile_id: str,
    request: Request,
    base_profile_id: str = Form(""),
    name: str = Form(...),
    version: str = Form(...),
    description: str = Form(...),
    analyst_instruction: str = Form(...),
    content_instruction: str = Form(...),
    reviewer_instruction: str = Form(...),
) -> RedirectResponse:
    user = _page_user(request, {ROLE_ADMIN})
    if user is None:
        return _redirect_to_login(request)
    engine.update_prompt_profile(
        profile_id,
        PromptProfileForm(
            profile_id=profile_id,
            base_profile_id=base_profile_id or None,
            name=name,
            version=version,
            description=description,
            analyst_instruction=analyst_instruction,
            content_instruction=content_instruction,
            reviewer_instruction=reviewer_instruction,
        ),
    )
    return RedirectResponse(url="/prompts", status_code=303)


@app.get("/evaluations", response_class=HTMLResponse)
def evaluations_page(request: Request):
    user = _page_user(request)
    if user is None:
        return _redirect_to_login(request)
    return _template_response(
        request,
        "evaluations.html",
        _common_context(
            request,
            user,
            "evaluations",
            datasets=evaluation_service.list_datasets(),
            model_options=engine.list_model_options(),
            prompt_profiles=engine.list_prompt_profiles(),
            routing_policies=engine.list_routing_policies(),
            default_model_name=settings.model_name,
            default_prompt_profile_id=DEFAULT_PROMPT_PROFILE_ID,
            default_routing_policy_id=DEFAULT_ROUTING_POLICY_ID,
            evaluations=evaluation_service.list_evaluations(),
        ),
    )


@app.post("/evaluations/run")
def evaluation_run_action(
    request: Request,
    dataset_id: str = Form(...),
    candidate_model_name: str = Form(...),
    candidate_prompt_profile_id: str = Form(...),
    candidate_routing_policy_id: str = Form(...),
    baseline_model_name: str = Form(...),
    baseline_prompt_profile_id: str = Form(...),
    baseline_routing_policy_id: str = Form(...),
) -> RedirectResponse:
    user = _page_user(request, {ROLE_OPERATOR, ROLE_ADMIN})
    if user is None:
        return _redirect_to_login(request)
    evaluation = evaluation_service.run_evaluation(
        dataset_id=dataset_id,
        candidate_request=WorkflowRequest(
            workflow_type=WorkflowType.SALES_FOLLOWUP,
            model_name_override=candidate_model_name,
            prompt_profile_id=candidate_prompt_profile_id,
            routing_policy_id=candidate_routing_policy_id,
        ),
        baseline_request=WorkflowRequest(
            workflow_type=WorkflowType.SALES_FOLLOWUP,
            model_name_override=baseline_model_name,
            prompt_profile_id=baseline_prompt_profile_id,
            routing_policy_id=baseline_routing_policy_id,
        ),
    )
    return RedirectResponse(url=f"/evaluations?selected={evaluation.id}", status_code=303)


@app.get("/reviews", response_class=HTMLResponse)
def reviews_page(request: Request):
    user = _page_user(request, {ROLE_REVIEWER, ROLE_ADMIN})
    if user is None:
        return _redirect_to_login(request)
    return _template_response(
        request,
        "reviews.html",
        _common_context(request, user, "reviews", queue=_build_run_table(engine.list_review_queue())),
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(run_id: str, request: Request):
    user = _page_user(request)
    if user is None:
        return _redirect_to_login(request)
    run = engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return _template_response(
        request,
        "run_detail.html",
        _common_context(
            request,
            user,
            "runs",
            run=run,
            execution_profile=_extract_execution_profile(run),
            run_result_json=json.dumps(run.result, ensure_ascii=False, indent=2),
            run_view={
                "title": _workflow_titles().get(run.workflow_type.value, run.workflow_type.value),
                "timeline": _build_timeline(run),
                "updated_at": run.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "created_at": run.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            },
            llm_summary=_build_llm_summary(run),
            graph=engine.get_graph_definition(),
        ),
    )


@app.post("/runs/{run_id}/review/form")
def review_form_action(
    run_id: str,
    request: Request,
    decision: str = Form(...),
    comment: str = Form(""),
    next_page: str = Form("detail"),
) -> RedirectResponse:
    user = _page_user(request, {ROLE_REVIEWER, ROLE_ADMIN})
    if user is None:
        return _redirect_to_login(request)
    run = engine.submit_review(run_id, approve=decision == "approve", comment=comment, reviewer_name=user.display_name)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    target = "/reviews" if next_page == "reviews" else f"/runs/{run_id}"
    return RedirectResponse(url=target, status_code=303)


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "database_backend": settings.database_backend,
        "database_path": str(settings.database_file) if settings.database_backend == "sqlite" else settings.database_url,
        "redis_enabled": cache.enabled,
        "redis_url": settings.redis_url or "",
        "llm_enabled": settings.llm_enabled,
        "model_name": settings.model_name,
    }


@app.get("/api/session")
def session_info(request: Request) -> dict[str, object]:
    user = auth_service.require_user(request)
    return {
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "capabilities": auth_service.capabilities_for(user).__dict__,
    }


@app.get("/api/workflows/templates")
def list_templates(request: Request) -> list[dict]:
    auth_service.require_user(request)
    return engine.list_templates()


@app.get("/api/experiments/catalog")
def experiment_catalog(request: Request) -> dict[str, object]:
    auth_service.require_user(request)
    return {
        "models": engine.list_model_options(),
        "prompt_profiles": [item.model_dump(mode="json") for item in engine.list_prompt_profiles()],
        "routing_policies": engine.list_routing_policies(),
        "datasets": evaluation_service.list_datasets(),
        "default_model_name": settings.model_name,
        "default_prompt_profile_id": DEFAULT_PROMPT_PROFILE_ID,
        "default_routing_policy_id": DEFAULT_ROUTING_POLICY_ID,
    }


@app.get("/api/experiments/compare")
def compare_summary(request: Request) -> dict[str, object]:
    auth_service.require_user(request)
    return _build_compare_summary(engine.list_runs())


@app.get("/api/workflows")
def list_runs(request: Request) -> list[dict]:
    auth_service.require_user(request)
    return [run.model_dump(mode="json") for run in engine.list_runs()]


@app.get("/api/workflows/review-queue")
def get_review_queue(request: Request) -> list[dict]:
    auth_service.require_roles(request, ROLE_REVIEWER, ROLE_ADMIN)
    return [run.model_dump(mode="json") for run in engine.list_review_queue()]


@app.get("/api/workflows/graph")
def get_workflow_graph(request: Request) -> dict:
    auth_service.require_user(request)
    return engine.get_graph_definition()


@app.get("/api/workflows/{run_id}")
def get_run(run_id: str, request: Request) -> dict:
    auth_service.require_user(request)
    run = engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return run.model_dump(mode="json")


@app.post("/api/workflows/run")
def run_workflow(request: Request, body: WorkflowRequest) -> dict:
    auth_service.require_roles(request, ROLE_OPERATOR, ROLE_ADMIN)
    run = engine.run_workflow(body)
    return run.model_dump(mode="json")


@app.post("/api/workflows/{run_id}/review")
def submit_review(run_id: str, request: Request, submission: ReviewSubmission) -> dict:
    user = auth_service.require_roles(request, ROLE_REVIEWER, ROLE_ADMIN)
    run = engine.submit_review(run_id, submission.approve, submission.comment, reviewer_name=user.display_name)
    if run is None:
        raise HTTPException(status_code=404, detail="workflow run not found")
    return run.model_dump(mode="json")


@app.get("/api/prompts")
def list_prompt_profiles(request: Request) -> list[dict]:
    auth_service.require_user(request)
    return [item.model_dump(mode="json") for item in engine.list_prompt_profiles()]


@app.post("/api/prompts")
def create_prompt_profile(request: Request, body: PromptProfileForm) -> dict:
    auth_service.require_roles(request, ROLE_ADMIN)
    return engine.create_prompt_profile(body).model_dump(mode="json")


@app.put("/api/prompts/{profile_id}")
def update_prompt_profile(profile_id: str, request: Request, body: PromptProfileForm) -> dict:
    auth_service.require_roles(request, ROLE_ADMIN)
    return engine.update_prompt_profile(profile_id, body).model_dump(mode="json")


@app.get("/api/evaluations")
def list_evaluations(request: Request) -> list[dict]:
    auth_service.require_user(request)
    return [item.model_dump(mode="json") for item in evaluation_service.list_evaluations()]


@app.get("/api/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str, request: Request) -> dict:
    auth_service.require_user(request)
    evaluation = evaluation_service.get_evaluation(evaluation_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="evaluation not found")
    return evaluation.model_dump(mode="json")
