from fastapi.testclient import TestClient

from app.db import UserAccountRecord
from app.main import app, database
from app.services import ReviewerAgent


client = TestClient(app)


def login_as(username: str, password: str) -> None:
    response = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "next": "/dashboard",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


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
    assert "进入工作台" in response.text

    login_as("viewer", "viewer123")
    session = client.get("/api/session")
    assert session.status_code == 200
    assert session.json()["role"] == "viewer"
    assert session.json()["capabilities"]["can_view"] is True


def test_dashboard_renders_multi_page_shell() -> None:
    login_as("operator", "operator123")
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "新建工作流" in response.text
    assert "模型与 Prompt 对比" in response.text
    assert "Prompt 方案" in response.text


def test_health_endpoint_exposes_backend_shape() -> None:
    response = client.get("/api/health")
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["database_backend"] in {"sqlite", "mysql", "custom"}
    assert "redis_enabled" in body


def test_catalog_endpoint_exposes_models_and_prompt_profiles() -> None:
    login_as("viewer", "viewer123")
    response = client.get("/api/experiments/catalog")
    body = response.json()
    assert response.status_code == 200
    assert any(item["model_name"] == "qwen3-max" for item in body["models"])
    assert any(item["profile_id"] == "balanced-v1" for item in body["prompt_profiles"])


def test_graph_endpoint_exposes_langgraph_shape() -> None:
    login_as("viewer", "viewer123")
    response = client.get("/api/workflows/graph")
    body = response.json()
    assert response.status_code == 200
    assert body["runtime"] == "langgraph"
    assert "planner" in body["nodes"]
    assert any(edge["from"] == "reviewer" for edge in body["edges"])


def test_review_page_requires_reviewer_role() -> None:
    login_as("operator", "operator123")
    forbidden = client.get("/reviews")
    assert forbidden.status_code == 403

    login_as("reviewer", "reviewer123")
    allowed = client.get("/reviews")
    assert allowed.status_code == 200
    assert "审核中心" in allowed.text


def test_sales_workflow_runs_with_selected_model_and_prompt_profile() -> None:
    login_as("operator", "operator123")
    response = client.post(
        "/api/workflows/run",
        json={
            "workflow_type": "sales_followup",
            "input_payload": {
                "period": "2026-W13",
                "region": "华东",
                "sales_reps": ["王晨", "李雪"],
            },
            "model_name_override": "qwen-plus",
            "prompt_profile_id": "ops-deep-v1",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["result"]["raw_result"]["summary"]["lead_count"] > 0
    assert body["result"]["execution_profile"]["model_name"] == "qwen-plus"
    assert body["result"]["execution_profile"]["prompt_profile"]["profile_id"] == "ops-deep-v1"
    llm_logs = [log for log in body["logs"] if log.get("llm_call")]
    assert len(llm_logs) == 3
    assert all(log["llm_call"]["model_name"] == "qwen-plus" for log in llm_logs)
    assert all(log["llm_call"]["prompt_profile_id"] == "ops-deep-v1" for log in llm_logs)


def test_waiting_human_reasons_do_not_include_auto_execute_copy() -> None:
    merged = ReviewerAgent._merge_review(
        {
            "status": "completed",
            "needs_human_review": False,
            "score": 0.92,
            "reasons": ["结果结构完整，可直接流转执行。"],
        },
        {
            "status": "waiting_human",
            "needs_human_review": True,
            "score": 0.65,
            "reasons": ["需要人工确认一对一辅导计划及风险客户跟进策略的可行性与资源支持。"],
        },
    )
    assert merged["status"] == "waiting_human"
    assert merged["needs_human_review"] is True
    assert all("可直接流转执行" not in reason for reason in merged["reasons"])
    assert any("人工" in reason for reason in merged["reasons"])


def test_support_workflow_flags_human_review_and_can_be_approved() -> None:
    login_as("operator", "operator123")
    response = client.post(
        "/api/workflows/run",
        json={
            "workflow_type": "support_triage",
            "input_payload": {
                "tickets": [
                    {
                        "customer": "示例客户",
                        "message": "系统报错并影响上线，请尽快恢复。",
                    }
                ]
            },
            "model_name_override": "qwen-turbo",
            "prompt_profile_id": "balanced-v1",
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert body["review"]["needs_human_review"] is True
    assert body["status"] == "waiting_human"
    assert any("人工接管" in log["message"] for log in body["logs"])

    login_as("reviewer", "reviewer123")
    queue = client.get("/api/workflows/review-queue").json()
    assert any(item["id"] == body["id"] for item in queue)

    approved = client.post(
        f"/api/workflows/{body['id']}/review",
        json={"approve": True, "comment": "值班工程师已确认处理方案"},
    ).json()
    assert approved["status"] == "completed"
    assert any("审核负责人" in log["message"] for log in approved["logs"])


def test_detail_page_contains_ai_metrics_and_execution_profile() -> None:
    login_as("operator", "operator123")
    created = client.post(
        "/api/workflows/run",
        json={
            "workflow_type": "meeting_minutes",
            "input_payload": {
                "meeting_title": "产品周会",
                "notes": "1. 张敏本周五前完成竞品复盘；2. 王晨今天下班前确认试点客户。",
            },
            "model_name_override": "qwen3-max",
            "prompt_profile_id": "exec-brief-v2",
        },
    ).json()
    detail = client.get(f"/runs/{created['id']}")
    assert detail.status_code == 200
    assert "图形化执行时间线" in detail.text
    assert "AI 运行指标" in detail.text
    assert "执行配置" in detail.text
    assert "管理摘要版 v2" in detail.text


def test_compare_page_and_api_show_prompt_experiments() -> None:
    login_as("viewer", "viewer123")
    page = client.get("/compare")
    assert page.status_code == 200
    assert "模型与 Prompt 对比" in page.text

    compare_api = client.get("/api/experiments/compare")
    body = compare_api.json()
    assert compare_api.status_code == 200
    assert body["run_count"] >= 1
    assert any("prompt_profile_label" in row for row in body["rows"])
