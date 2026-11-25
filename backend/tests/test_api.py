from fastapi.testclient import TestClient

from app.main import app
from app.seed import seed


def _login(client, username):
    response = client.post("/api/auth/login", json={"username": username, "password": "DemoOnly#2026"})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_demo_flow_keeps_history_and_blocks_unauthorized_approval():
    seed(reset=True)
    client = TestClient(app)
    admin = _login(client, "admin")
    traveler = _login(client, "liwei")

    denied = client.get("/api/approvals", headers=traveler)
    assert denied.status_code == 403
    assert denied.json()["error_code"] == "ROLE_FORBIDDEN"

    applications = client.get("/api/applications", headers=admin).json()
    types = {item["trip_type"] for item in applications}
    assert types == {"onsite", "client_visit", "training"}
    frozen = next(item for item in applications if item["code"] == "SQ-VISIT-FREEZE")
    claim = next(
        item
        for item in client.get("/api/reimbursements", headers=admin).json()
        if item["application_id"] == frozen["id"] and item["status"] == "approved"
    )
    assert claim["personal_claimable"] == "1020.00"
    assert claim["result"]["policy_version"] == "v1"

    published = client.post(
        "/api/policies",
        headers=admin,
        json={
            "note": "上调专员二线住宿限额，只影响新核定",
            "standards": [{"rank": "staff", "city_tier": "T2", "item": "hotel", "amount": "600"}],
        },
    )
    assert published.status_code == 200, published.text
    new_policy = published.json()["id"]
    again = client.get(f"/api/reimbursements/{claim['id']}", headers=admin).json()
    assert again["personal_claimable"] == "1020.00"
    assert again["result"]["policy_version"] == "v1"
    preview = client.post(
        "/api/reimbursements/preview",
        headers=admin,
        json={"application_id": frozen["id"], "hypothetical_policy_version_id": new_policy},
    ).json()
    assert preview["hypothetical"] is True
    assert preview["personal_claimable"] == "1120.00"
    stored = client.get(f"/api/reimbursements/{claim['id']}", headers=admin).json()
    assert stored["personal_claimable"] == "1020.00"

    tasks = client.get("/api/approvals", headers=admin).json()
    assert tasks
    blocked = client.post(
        f"/api/approvals/{tasks[0]['id']}/decision",
        headers=admin,
        json={"approve": True, "comment": "管理员越权"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == "ROLE_FORBIDDEN"

    bad_project = client.get("/api/reconcile?group_by=project&project_id=missing", headers=admin)
    assert bad_project.status_code == 400
    assert bad_project.json()["error_code"] == "ATTRIBUTION_UNMATCHED"
    assert "total" not in bad_project.json()

    report = client.post("/api/admin/evaluations/run", headers=admin).json()
    assert report["total"] >= 25
    assert report["failed"] == 0
    race = client.post("/api/admin/invoice-race", headers=admin).json()
    assert race["passed"] is True
    metrics = client.get("/api/admin/metrics", headers=admin).json()
    assert metrics["reset_on_restart"] is True
    assert "model_failure_count" in metrics

    explain = client.post(
        f"/api/reimbursements/{claim['id']}/explain",
        headers=admin,
        json={"proposed_amount": "1.00"},
    )
    assert explain.status_code == 400
    assert explain.json()["error_code"] == "MODEL_CANNOT_SET_AMOUNT"
