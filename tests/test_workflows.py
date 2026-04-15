from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import UserAccountRecord
from app.main import app, database
from app.services import ReviewerAgent, RouterAgent


client = TestClient(app)


def login_as(username: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/dashboard"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def create_sales_run() -> dict:
    login_as("operator", "operator123")
    response = client.post(
        "/api/workflows/run",
        json={
            "workflow_type": "sales_followup",
            "input_payload": {
                "period": "2026-W13",
                "region": "??",
                "sales_reps": ["??", "??"],
                "focus_metric": "conversion_rate",
            },
            "model_name_override": "qwen-plus",
            "prompt_profile_id": "ops-deep-v1",
            "routing_policy_id": "balanced-router-v1",
        },
    )
    assert response.status_code == 200
    return response.json()


def create_support_run() -> dict:
    login_as("operator", "operator123")
    response = client.post(
        "/api/workflows/run",
        json={
            "workflow_type": "support_triage",
            "input_payload": {
                "tickets": [
                    {
                        "customer": "????",
                        "message": "???????????????????????",
                    }
                ]
            },
            "model_name_override": "qwen-turbo",
            "prompt_profile_id": "balanced-v1",
            "routing_policy_id": "strict-review-v1",
        },
    )
    assert response.status_code == 200
    return response.json()


def test_seeded_users_are_persisted_with_password_hash() -> None:
    with database.session() as session:
        record = session.get(UserAccountRecord, "admin")
        assert record is not None
        assert record.password_hash.startswith("pbkdf2_sha256$")
        assert record.password_hash != "admin123"


def test_root_redirects_to_login() -> None:
    anonymous = TestClient(app)
    response = anonymous.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_page_and_session_endpoint() -> None:
    response = client.get("/login")
    assert response.status_code == 200
    assert "FlowPilot" in response.text

    login_as("viewer", "viewer123")
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["role"] == "viewer"
    assert session.json()["capabilities"]["can_view"] is True


def test_dashboard_renders_all_real_data_source_options() -> None:
    login_as("operator", "operator123")
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "?????" in response.text
    assert "???????" in response.text
    assert "GitHub Issues" in response.text
    assert "NYC 311" in response.text
    assert "Stack Overflow" in response.text
    assert "Hacker News" in response.text


def test_health_endpoint_exposes_backend_shape() -> None:
    response = client.get("/api/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["database_backend"] in {"sqlite", "mysql", "custom"}
    assert "redis_enabled" in body
    assert "monthly_budget_usd" in body


def test_catalog_endpoint_exposes_models_prompts_routing_and_datasets() -> None:
    login_as("viewer", "viewer123")
    response = client.get("/api/experiments/catalog")
    body = response.json()
    assert response.status_code == 200
    assert any(item["model_name"] == "qwen3-max" for item in body["models"])
    assert any(item["profile_id"] == "balanced-v1" for item in body["prompt_profiles"])
    assert any(item["policy_id"] == "balanced-router-v1" for item in body["routing_policies"])
    assert any(item["dataset_id"] == "ops-regression-v1" for item in body["datasets"])


def test_graph_endpoint_exposes_langgraph_shape() -> None:
    login_as("viewer", "viewer123")
    response = client.get("/api/workflows/graph")
    body = response.json()
    assert response.status_code == 200
    assert body["runtime"] == "langgraph"
    assert "router" in body["nodes"]
    assert any(edge["from"] == "planner" and edge["to"] == "router" for edge in body["edges"])
    assert any(edge["from"] == "router" and edge["to"] == "planner" for edge in body["edges"])
    assert any(edge["from"] == "router" and edge["to"] == "reviewer" for edge in body["edges"])


def test_review_page_requires_reviewer_role() -> None:
    login_as("operator", "operator123")
    forbidden = client.get("/reviews")
    assert forbidden.status_code == 403

    login_as("reviewer", "reviewer123")
    allowed = client.get("/reviews")
    assert allowed.status_code == 200


def test_sales_workflow_runs_with_selected_model_prompt_and_routing() -> None:
    body = create_sales_run()
    assert body["result"]["raw_result"]["lead_count"] > 0
    assert body["result"]["execution_profile"]["primary_model_name"] == "qwen-plus"
    assert body["result"]["execution_profile"]["prompt_profile"]["profile_id"] == "ops-deep-v1"
    assert body["result"]["execution_profile"]["routing_policy"]["policy_id"] == "balanced-router-v1"
    assert "planning_context" in body["result"]
    assert "memory" in body["result"]["planning_context"]
    planner_logs = [log for log in body["logs"] if log["agent"] == "PlannerAgent"]
    assert len(planner_logs) == 1
    assert planner_logs[0]["tool_call"]["name"] == "planning_context_tool"
    llm_logs = [log for log in body["logs"] if log.get("llm_call")]
    assert len(llm_logs) == 4
    assert {log["llm_call"]["route_target"] for log in llm_logs} == {"planner", "analyst", "content", "reviewer"}


def test_router_requests_one_replan_when_content_is_missing() -> None:
    decision = RouterAgent().decide(
        last_node="content",
        state={"deliverables": {}, "replan_count": 0},
    )

    assert decision["next_node"] == "planner"
    assert decision["replan_count"] == 1


def test_workflow_result_records_route_decisions() -> None:
    body = create_sales_run()

    route_decisions = body["result"]["route_decisions"]

    assert route_decisions[0]["from_node"] == "planner"
    assert route_decisions[0]["next_node"] == "operator"
    assert any(item["next_node"] == "reviewer" for item in route_decisions)


def test_waiting_human_reasons_do_not_include_auto_execute_copy() -> None:
    merged = ReviewerAgent._merge_review(
        {
            "status": "completed",
            "needs_human_review": False,
            "score": 0.92,
            "reasons": ["???????????????"],
        },
        {
            "status": "waiting_human",
            "needs_human_review": True,
            "score": 0.65,
            "reasons": ["?????????????????"],
        },
    )
    assert merged["status"] == "waiting_human"
    assert merged["needs_human_review"] is True
    assert all("????" not in reason for reason in merged["reasons"])
    assert any("??" in reason or "??" in reason for reason in merged["reasons"])


def test_support_workflow_flags_human_review_and_can_be_approved() -> None:
    body = create_support_run()
    assert body["review"]["needs_human_review"] is True
    assert body["status"] == "waiting_human"

    login_as("reviewer", "reviewer123")
    queue = client.get("/api/workflows/review-queue").json()
    assert any(item["id"] == body["id"] for item in queue)

    approved = client.post(
        f"/api/workflows/{body['id']}/review",
        json={"approve": True, "comment": "??????????????????"},
    ).json()
    assert approved["status"] == "completed"
    assert any("???" in log["message"] for log in approved["logs"])


def test_run_detail_page_contains_all_export_formats() -> None:
    created = create_sales_run()
    detail = client.get(f"/runs/{created['id']}")
    assert detail.status_code == 200
    assert "?????" in detail.text
    assert "?? Markdown" in detail.text
    assert "?? HTML" in detail.text
    assert "?? PDF" in detail.text


def test_workflow_export_endpoint_supports_markdown_html_and_pdf() -> None:
    created = create_sales_run()
    markdown = client.get(f"/runs/{created['id']}/export?format=markdown")
    html = client.get(f"/runs/{created['id']}/export?format=html")
    pdf = client.get(f"/runs/{created['id']}/export?format=pdf")
    assert markdown.status_code == 200
    assert html.status_code == 200
    assert pdf.status_code == 200
    assert "text/markdown" in markdown.headers["content-type"]
    assert "text/html" in html.headers["content-type"]
    assert "application/pdf" in pdf.headers["content-type"]


def test_runs_page_supports_status_and_workflow_filters() -> None:
    create_support_run()
    response = client.get("/runs?status_filter=waiting_human&workflow_filter=support_triage")
    assert response.status_code == 200
    assert "?????" in response.text
    assert "????????" in response.text


def test_prompt_profile_can_be_created_and_updated() -> None:
    login_as("admin", "admin123")
    profile_id = f"ops-lab-{uuid4().hex[:8]}"
    created = client.post(
        "/api/prompts",
        json={
            "profile_id": profile_id,
            "base_profile_id": "ops-deep-v1",
            "name": "?????",
            "version": "v1",
            "description": "??????????????????",
            "analyst_instruction": "????????",
            "content_instruction": "????????????",
            "reviewer_instruction": "??????????????",
        },
    )
    assert created.status_code == 200
    assert created.json()["profile_id"] == profile_id

    updated = client.put(
        f"/api/prompts/{profile_id}",
        json={
            "profile_id": profile_id,
            "base_profile_id": "ops-deep-v1",
            "name": "?????",
            "version": "v2",
            "description": "??????????????????",
            "analyst_instruction": "????????????",
            "content_instruction": "????????????",
            "reviewer_instruction": "??????????????",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == "v2"


def test_compare_page_and_api_show_routing_experiments() -> None:
    create_sales_run()
    login_as("viewer", "viewer123")
    page = client.get("/compare")
    assert page.status_code == 200

    compare_api = client.get("/api/experiments/compare")
    body = compare_api.json()
    assert compare_api.status_code == 200
    assert body["run_count"] >= 1
    assert isinstance(body["rows"], list)


def test_evaluation_run_page_supports_trend_and_drilldown_and_exports() -> None:
    login_as("operator", "operator123")
    page = client.get("/evaluations")
    assert page.status_code == 200
    assert "????" in page.text

    response = client.post(
        "/evaluations/run",
        data={
            "dataset_id": "ops-regression-v1",
            "candidate_model_name": "qwen-plus",
            "candidate_prompt_profile_id": "balanced-v1",
            "candidate_routing_policy_id": "balanced-router-v1",
            "baseline_model_name": "qwen-turbo",
            "baseline_prompt_profile_id": "balanced-v1",
            "baseline_routing_policy_id": "single-model-v1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "????" in response.text
    assert "???????" in response.text

    evaluations = client.get("/api/evaluations").json()
    assert len(evaluations) >= 1
    evaluation_id = evaluations[0]["id"]
    markdown = client.get(f"/evaluations/{evaluation_id}/export?format=markdown")
    html = client.get(f"/evaluations/{evaluation_id}/export?format=html")
    pdf = client.get(f"/evaluations/{evaluation_id}/export?format=pdf")
    assert "text/markdown" in markdown.headers["content-type"]
    assert "text/html" in html.headers["content-type"]
    assert "application/pdf" in pdf.headers["content-type"]


def test_batch_experiment_run_and_listing_work() -> None:
    login_as("operator", "operator123")
    response = client.post(
        "/api/batches",
        json={
            "name": "Prompt AB ??",
            "workflow_type": "sales_followup",
            "input_payload": {
                "period": "2026-W13",
                "region": "??",
                "sales_reps": ["??", "??"],
                "focus_metric": "conversion_rate",
            },
            "repeats": 1,
            "variants": [
                {
                    "variant_id": "control",
                    "label": "???",
                    "model_name": "qwen-plus",
                    "prompt_profile_id": "balanced-v1",
                    "routing_policy_id": "balanced-router-v1",
                },
                {
                    "variant_id": "challenger",
                    "label": "???",
                    "model_name": "qwen3-max",
                    "prompt_profile_id": "ops-deep-v1",
                    "routing_policy_id": "strict-review-v1",
                },
            ],
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["summary"]["variant_count"] == 2
    assert body["summary"]["champion"] is not None


def test_feedback_review_creates_feedback_sample() -> None:
    body = create_support_run()
    login_as("reviewer", "reviewer123")
    client.post(
        f"/api/workflows/{body['id']}/review",
        json={"approve": True, "comment": "?????????????"},
    )
    feedback_samples = client.get("/api/feedback-samples")
    assert feedback_samples.status_code == 200
    assert len(feedback_samples.json()) >= 1


def test_runtime_memory_context_is_recorded_for_analyst_and_reviewer() -> None:
    first = create_support_run()
    login_as("reviewer", "reviewer123")
    reviewed = client.post(
        f"/api/workflows/{first['id']}/review",
        json={"approve": True, "comment": "keep owner and risk notes"},
    )
    assert reviewed.status_code == 200

    second = create_support_run()

    analyst_context = second["result"]["analyst_context"]
    reviewer_context = second["result"]["reviewer_context"]

    assert analyst_context["memory_hits"] >= 1
    assert reviewer_context["memory_hits"] >= 1
    assert len(analyst_context["memory"]["recent_runs"]) >= 1
    assert len(reviewer_context["memory"]["feedback_samples"]) >= 1
    assert any("????" in log["message"] for log in second["logs"] if log["agent"] == "AnalystAgent")
    assert any("????" in log["message"] for log in second["logs"] if log["agent"] == "ReviewerAgent")


def test_runtime_memory_context_is_recorded_for_content_agent() -> None:
    first = create_support_run()
    login_as("reviewer", "reviewer123")
    reviewed = client.post(
        f"/api/workflows/{first['id']}/review",
        json={"approve": True, "comment": "keep owner and risk notes"},
    )
    assert reviewed.status_code == 200

    second = create_support_run()

    content_context = second["result"]["content_context"]

    assert content_context["memory_hits"] >= 1
    assert len(content_context["memory"]["recent_runs"]) >= 1
    assert len(content_context["memory"]["feedback_samples"]) >= 1
    assert any("????" in log["message"] for log in second["logs"] if log["agent"] == "ContentAgent")


def test_run_detail_page_shows_runtime_memory_summary() -> None:
    first = create_support_run()
    login_as("reviewer", "reviewer123")
    reviewed = client.post(
        f"/api/workflows/{first['id']}/review",
        json={"approve": True, "comment": "keep owner and risk notes"},
    )
    assert reviewed.status_code == 200

    second = create_support_run()

    detail = client.get(f"/runs/{second['id']}")

    assert detail.status_code == 200
    assert "?????" in detail.text
    assert "AnalystAgent" in detail.text
    assert "ContentAgent" in detail.text
    assert "ReviewerAgent" in detail.text
    assert "memory hits" in detail.text


def test_settings_can_disable_runtime_memory(monkeypatch) -> None:
    monkeypatch.setenv("FLOWPILOT_ENABLE_RUNTIME_MEMORY", "false")
    settings = Settings.from_env()
    assert settings.enable_runtime_memory is False
