"""接口层联调：鉴权、越权、测验闭环、链路查询。

单元测试证明「函数对」，这一组证明「接线对」——很多权限问题不是逻辑写错，
是某个路由忘了挂 Depends。
"""

import pytest
from fastapi.testclient import TestClient

from server.main import app

client = TestClient(app)


def _login(username: str, password: str) -> str:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_is_public():
    assert client.get("/api/health").json()["ok"] is True


def test_business_endpoints_require_token():
    assert client.get("/api/chapters").status_code == 401
    assert client.post("/api/ask", json={"question": "年假几天"}).status_code == 401
    assert client.get("/api/mastery").status_code == 401


def test_login_rejects_bad_password():
    assert client.post("/api/auth/login", json={"username": "sales", "password": "nope"}).status_code == 401


def test_login_returns_department_scope():
    token = _login("sales", "sales12345")
    me = client.get("/api/auth/me", headers=_auth(token)).json()
    assert me["username"] == "sales"
    assert me["department"] == "销售"
    assert "销售" in me["audiences"]
    assert "客服" not in me["audiences"]


def test_employee_cannot_delete_docs():
    token = _login("sales", "sales12345")
    res = client.delete("/api/library/docs/handbook-attendance.md", headers=_auth(token))
    assert res.status_code == 403


def test_quiz_flow_hides_answers(monkeypatch):
    """完整走一遍：出题 → 提交 → 判分，全链路都不该把答案交给客户端。"""
    from backend.app import training

    monkeypatch.setattr(
        "backend.app.training.llm.chat_json",
        lambda prompt, system, temperature=0.2, **kw: {
            "questions": [
                {"question": "Q1", "options": ["A", "B", "C", "D"], "answer_index": 2,
                 "explanation": "e", "knowledge_point": "k"},
                {"question": "Q2", "options": ["A", "B", "C", "D"], "answer_index": 1,
                 "explanation": "e", "knowledge_point": "k"},
            ]
        },
    )
    token = _login("support", "support12345")
    res = client.post("/api/quiz", json={"chapter": "handbook-attendance.md", "count": 2}, headers=_auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    assert "answers" not in data
    assert all("answer_index" not in q for q in data["questions"])

    graded = client.post(
        "/api/quiz/submit",
        json={"session_id": data["session_id"], "answers": [2, 1]},
        headers=_auth(token),
    )
    assert graded.status_code == 200, graded.text
    assert graded.json()["score"] == 2
    assert graded.json()["passed"] is True


def test_quiz_rejects_foreign_session(monkeypatch):
    monkeypatch.setattr(
        "backend.app.training.llm.chat_json",
        lambda prompt, system, temperature=0.2, **kw: {
            "questions": [
                {"question": "Q1", "options": ["A", "B", "C"], "answer_index": 0,
                 "explanation": "e", "knowledge_point": "k"}
            ]
        },
    )
    sales = _login("sales", "sales12345")
    tech = _login("tech", "tech12345")
    created = client.post("/api/quiz", json={"chapter": "handbook-attendance.md", "count": 1}, headers=_auth(sales))
    session_id = created.json()["session_id"]
    stolen = client.post("/api/quiz/submit", json={"session_id": session_id, "answers": [0]}, headers=_auth(tech))
    assert stolen.status_code == 400


def test_traces_are_admin_only():
    sales = _login("sales", "sales12345")
    admin = _login("admin", "admin12345")
    assert client.get("/api/traces", headers=_auth(sales)).status_code == 403
    assert client.get("/api/traces", headers=_auth(admin)).status_code == 200


def test_mastery_is_per_user():
    admin = _login("admin", "admin12345")
    data = client.get("/api/mastery", headers=_auth(admin)).json()
    assert data["employee"] == "admin"
    assert "total_chapters" in data
    assert "weak" in data
