"""两线程争用同一张发票。每次使用独立数据库，可重复运行。"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from sqlalchemy import select

from app.db import session_for
from app.errors import BusinessError
from app.models import User
from app.seed import seed_reference
from app.services import (
    create_application,
    create_invoice,
    create_order,
    decide_task,
    submit_application,
    submit_reimbursement,
)


def run_invoice_race() -> dict:
    folder = Path(tempfile.mkdtemp(prefix="tc-race-"))
    url = "sqlite:///" + (folder / "race.db").as_posix()
    session_factory, _engine = session_for(url)
    db = session_factory()
    seed_reference(db)
    traveler = db.scalar(select(User).where(User.username == "liwei"))
    approver = db.scalar(select(User).where(User.username == "dept-rd"))
    finance = db.scalar(select(User).where(User.username == "finance"))
    booker = db.scalar(select(User).where(User.username == "booker"))
    application = create_application(
        db,
        traveler,
        {
            "trip_type": "client_visit",
            "traveler_ids": [traveler.id],
            "reason": "并发核销演示",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-08-03",
            "end_date": "2026-08-03",
            "estimated_budget": "2000",
            "cost_owner_type": "department",
            "department_id": "dept-rd",
            "client_name": "南湖精密",
            "visit_purpose": "发票争用",
            "scenario": "发票并发",
        },
    )
    submit_application(db, traveler, application.id)
    _approve(db, "application", application.id, approver, finance)
    order = create_order(
        db,
        booker,
        {
            "application_id": application.id,
            "traveler_id": traveler.id,
            "type": "train",
            "amount": "300",
            "start_date": "2026-08-03",
            "cabin": "second_class",
        },
    )
    create_invoice(
        db,
        booker,
        {
            "number": "DEMO-RACE-INV-001",
            "category": "train",
            "amount": "300",
            "order_id": order.id,
            "traveler_id": traveler.id,
            "invoice_date": "2026-08-03",
            "verification_result": "pass",
            "verification_message": "模拟查验：通过",
        },
    )
    db.close()

    barrier = threading.Barrier(2)
    results = []

    def worker():
        local = session_factory()
        person = local.get(User, traveler.id)
        try:
            barrier.wait(timeout=5)
            submit_reimbursement(local, person, application.id)
            results.append("ok")
        except BusinessError as exc:
            results.append(exc.code)
        finally:
            local.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    results.sort()
    return {
        "results": results,
        "success_count": results.count("ok"),
        "conflict_count": results.count("INVOICE_ALREADY_CLAIMED"),
        "passed": results == ["INVOICE_ALREADY_CLAIMED", "ok"],
        "database": str(folder / "race.db"),
    }


def _approve(db, entity_type, entity_id, approver, finance):
    from app.models import ApprovalTask

    while True:
        task = db.scalar(
            select(ApprovalTask)
            .where(
                ApprovalTask.entity_type == entity_type,
                ApprovalTask.entity_id == entity_id,
                ApprovalTask.status == "pending",
            )
            .order_by(ApprovalTask.sequence)
        )
        if task is None:
            return
        actor = approver if task.assignee_role == "dept_approver" else finance
        decide_task(db, actor, task.id, True, "并发演示审批", acknowledge_budget=True)
