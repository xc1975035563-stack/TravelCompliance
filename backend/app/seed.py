"""虚构演示数据。不包含真实公司、客户、证件或发票。"""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.auth import hash_password
from app.clauses import FALLBACK_CLAUSES
from app.config import DEMO_PASSWORD
from app.db import SessionLocal, init_db
from app.errors import BusinessError
from app.models import (
    Base,
    BudgetPool,
    Department,
    Event,
    PolicyCity,
    PolicyClause,
    PolicyStandard,
    PolicyVersion,
    Project,
    User,
)
from app.db import engine
from app.money import money
from app.services import (
    create_application,
    create_change,
    create_invoice,
    create_order,
    decide_task,
    submit_application,
    submit_reimbursement,
)

SALT = "demo-salt-not-for-production"


def _user(db, **kwargs):
    row = User(
        password_salt=SALT,
        password_hash=hash_password(DEMO_PASSWORD, SALT),
        **kwargs,
    )
    db.add(row)
    return row


def _standards(db, policy_id: str):
    hotels = {
        ("staff", "T1"): "500",
        ("staff", "T2"): "400",
        ("staff", "T3"): "300",
        ("manager", "T1"): "800",
        ("manager", "T2"): "650",
        ("manager", "T3"): "500",
    }
    meals = {"T1": "120", "T2": "100", "T3": "80"}
    locals_ = {"T1": "80", "T2": "60", "T3": "40"}
    for rank in ("staff", "manager"):
        db.add(
            PolicyStandard(
                id=f"{policy_id}-{rank}-flight",
                policy_version_id=policy_id,
                rank=rank,
                city_tier="*",
                item="flight",
                allowed=["economy"] if rank == "staff" else ["economy", "business"],
            )
        )
        db.add(
            PolicyStandard(
                id=f"{policy_id}-{rank}-train",
                policy_version_id=policy_id,
                rank=rank,
                city_tier="*",
                item="train",
                allowed=["second_class"] if rank == "staff" else ["second_class", "first_class"],
            )
        )
        for tier in ("T1", "T2", "T3"):
            db.add(
                PolicyStandard(
                    id=f"{policy_id}-{rank}-{tier}-hotel",
                    policy_version_id=policy_id,
                    rank=rank,
                    city_tier=tier,
                    item="hotel",
                    amount=Decimal(hotels[(rank, tier)]),
                )
            )
            db.add(
                PolicyStandard(
                    id=f"{policy_id}-{rank}-{tier}-meal",
                    policy_version_id=policy_id,
                    rank=rank,
                    city_tier=tier,
                    item="meal",
                    amount=Decimal(meals[tier]),
                )
            )
            db.add(
                PolicyStandard(
                    id=f"{policy_id}-{rank}-{tier}-local",
                    policy_version_id=policy_id,
                    rank=rank,
                    city_tier=tier,
                    item="local",
                    amount=Decimal(locals_[tier]),
                )
            )


def seed_reference(db):
    db.add_all(
        [
            Department(id="dept-rd", name="研发部", cost_center_code="CC-RD"),
            Department(id="dept-mkt", name="市场部", cost_center_code="CC-MKT"),
            Department(id="dept-fin", name="财务管理部", cost_center_code="CC-FIN"),
            Department(id="dept-adm", name="综合管理部", cost_center_code="CC-ADM"),
        ]
    )
    db.add_all(
        [
            Project(id="proj-qinghe", name="青禾工业项目组", cost_center_code="CC-QH-001", department_id="dept-rd"),
            Project(id="proj-nanhu", name="南湖精密拜访项目", cost_center_code="CC-NH-014", department_id="dept-mkt"),
            Project(id="proj-hechuan", name="河澄设备驻场", cost_center_code="CC-HC-008", department_id="dept-rd"),
        ]
    )
    db.add_all(
        [
            BudgetPool(id="pool-qinghe", owner_type="project", owner_id="proj-qinghe", total=Decimal("500000"), reserved=0),
            BudgetPool(id="pool-nanhu", owner_type="project", owner_id="proj-nanhu", total=Decimal("300000"), reserved=0),
            BudgetPool(id="pool-hechuan", owner_type="project", owner_id="proj-hechuan", total=Decimal("200000"), reserved=0),
            BudgetPool(id="pool-rd", owner_type="department", owner_id="dept-rd", total=Decimal("300000"), reserved=0),
            BudgetPool(id="pool-mkt", owner_type="department", owner_id="dept-mkt", total=Decimal("300000"), reserved=0),
        ]
    )
    _user(db, id="u-liwei", username="liwei", display_name="李维", role="traveler", rank="staff", department_id="dept-rd")
    _user(db, id="u-zhoumin", username="zhoumin", display_name="周敏", role="traveler", rank="manager", department_id="dept-mkt")
    _user(db, id="u-booker", username="booker", display_name="何安", role="booker", rank=None, department_id="dept-rd")
    _user(db, id="u-dept-rd", username="dept-rd", display_name="陈衡", role="dept_approver", rank="manager", department_id="dept-rd")
    _user(db, id="u-dept-mkt", username="dept-mkt", display_name="苏晚", role="dept_approver", rank="manager", department_id="dept-mkt")
    _user(db, id="u-finance", username="finance", display_name="梁清", role="finance", rank=None, department_id="dept-fin")
    _user(db, id="u-admin", username="admin", display_name="沈知", role="admin", rank=None, department_id="dept-adm")
    policy = PolicyVersion(
        id="pol-v1",
        code="v1",
        status="active",
        note="2026 年演示制度。项目驻场周末不发放伙食和市内交通补助；超标预订需填原因并审批，未批准时超标部分自理。",
        over_standard_action="submit_approval",
        onsite_weekend_allowance=False,
        onsite_split_gap_days=1,
        standby_day_allowance=True,
        dedup_fields=["traveler", "type", "date", "amount", "application_code"],
        created_by="u-admin",
    )
    db.add(policy)
    db.flush()
    _standards(db, policy.id)
    for city, tier in (("上海", "T1"), ("杭州", "T2"), ("成都", "T2"), ("江城", "T3")):
        db.add(PolicyCity(id=f"city-v1-{city}", policy_version_id=policy.id, city=city, tier=tier))
    for code, text in FALLBACK_CLAUSES.items():
        db.add(PolicyClause(id=f"clause-v1-{code}", policy_version_id=policy.id, code=code, text=text))
    db.add(
        Event(
            id="evt-qc",
            name="江城质量管理培训",
            organizer="江城培训中心",
            city="江城",
            report_date=date(2026, 5, 11),
            event_start=date(2026, 5, 12),
            event_end=date(2026, 5, 14),
            return_date=date(2026, 5, 15),
            organizer_provides_lodging=True,
            organizer_provides_meals=True,
            organizer_provides_local=True,
            created_by="u-admin",
        )
    )
    db.commit()


def _by_name(db, username: str) -> User:
    return db.scalar(select(User).where(User.username == username))


def _expect(code: str, action):
    try:
        action()
    except BusinessError as exc:
        if exc.code != code:
            raise RuntimeError(f"期望 {code}，实际 {exc.code}：{exc.message}") from exc
        return
    raise RuntimeError(f"期望拒绝 {code}，但操作成功了")


def _approve(db, entity_type: str, entity_id: str, approved_amount=None, reject=False):
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
        if task.assignee_role == "dept_approver":
            actor = db.scalar(
                select(User).where(User.role == "dept_approver", User.department_id == task.department_id)
            )
        else:
            actor = _by_name(db, "finance")
        decide_task(
            db,
            actor,
            task.id,
            not reject,
            "演示审批，非生产流程。",
            acknowledge_budget=True,
            approved_amount=approved_amount if task.assignee_role == "finance" else None,
        )
        if reject:
            return


def _apply(db, username: str, payload: dict):
    row = create_application(db, _by_name(db, username), payload)
    submit_application(db, _by_name(db, username), row.id)
    _approve(db, "application", row.id)
    return db.get(type(row), row.id)


def _order(db, payload: dict):
    return create_order(db, _by_name(db, "booker"), payload)


def _invoice(db, order, number: str, city: str | None = None):
    create_invoice(
        db,
        _by_name(db, "booker"),
        {
            "number": number,
            "category": order.type,
            "amount": money(order.amount),
            "order_id": order.id,
            "traveler_id": order.traveler_id,
            "invoice_date": order.start_date.isoformat(),
            "city": city,
            "nights": order.nights,
            "verification_result": "pass",
            "verification_message": "模拟查验：通过",
        },
    )


def _claim(db, username: str, application_id: str):
    row = submit_reimbursement(db, _by_name(db, username), application_id)
    if row.status != "rejected":
        _approve(db, "reimbursement", row.id)
    return db.get(type(row), row.id)


def seed_scenarios(db):
    liwei = "u-liwei"
    onsite = _apply(
        db,
        "liwei",
        {
            "code": "SQ-ONSITE-001",
            "trip_type": "onsite",
            "traveler_ids": [liwei],
            "reason": "青禾工业项目连续驻场实施",
            "origin": "杭州",
            "destination": "上海",
            "start_date": "2026-03-02",
            "end_date": "2026-03-10",
            "estimated_budget": "20000",
            "cost_owner_type": "project",
            "project_id": "proj-qinghe",
            "department_id": "dept-rd",
            "transport_mode": "flight",
            "scenario": "驻场主单",
        },
    )
    go = _order(
        db,
        {
            "application_id": onsite.id,
            "traveler_id": liwei,
            "type": "flight",
            "amount": "800",
            "start_date": "2026-03-02",
            "cabin": "economy",
            "payment_mode": "company",
            "origin": "杭州",
            "destination": "上海",
        },
    )
    back = _order(
        db,
        {
            "application_id": onsite.id,
            "traveler_id": liwei,
            "type": "flight",
            "amount": "800",
            "start_date": "2026-03-10",
            "cabin": "economy",
            "payment_mode": "company",
            "origin": "上海",
            "destination": "杭州",
        },
    )
    hotel = _order(
        db,
        {
            "application_id": onsite.id,
            "traveler_id": liwei,
            "type": "hotel",
            "amount": "3840",
            "start_date": "2026-03-02",
            "end_date": "2026-03-10",
            "nights": 8,
            "city": "上海",
            "payment_mode": "personal",
        },
    )
    _invoice(db, go, "DEMO-FL-20260302-001")
    _invoice(db, back, "DEMO-FL-20260310-001")
    _invoice(db, hotel, "DEMO-HT-20260302-001", "上海")
    _claim(db, "liwei", onsite.id)

    overlap = create_application(
        db,
        _by_name(db, "liwei"),
        {
            "code": "SQ-ONSITE-OVL",
            "trip_type": "onsite",
            "traveler_ids": [liwei],
            "reason": "试图把同一驻场再报一次",
            "origin": "杭州",
            "destination": "上海",
            "start_date": "2026-03-08",
            "end_date": "2026-03-12",
            "estimated_budget": "8000",
            "cost_owner_type": "project",
            "project_id": "proj-qinghe",
            "department_id": "dept-rd",
            "scenario": "日期重叠被拒",
        },
    )
    _expect("ONSITE_OVERLAP", lambda: submit_application(db, _by_name(db, "liwei"), overlap.id))

    provided = _apply(
        db,
        "zhoumin",
        {
            "code": "SQ-ONSITE-002",
            "trip_type": "onsite",
            "traveler_ids": ["u-zhoumin"],
            "reason": "项目部统一安排住宿和用餐的驻场",
            "origin": "上海",
            "destination": "成都",
            "start_date": "2026-06-01",
            "end_date": "2026-06-09",
            "estimated_budget": "10000",
            "cost_owner_type": "project",
            "project_id": "proj-qinghe",
            "department_id": "dept-mkt",
            "project_provides_lodging": True,
            "project_provides_meals": True,
            "scenario": "项目提供食宿",
        },
    )
    _expect(
        "LODGING_PROVIDED",
        lambda: _order(
            db,
            {
                "application_id": provided.id,
                "traveler_id": "u-zhoumin",
                "type": "hotel",
                "amount": "3000",
                "start_date": "2026-06-01",
                "end_date": "2026-06-09",
                "nights": 8,
                "city": "成都",
            },
        ),
    )
    train = _order(
        db,
        {
            "application_id": provided.id,
            "traveler_id": "u-zhoumin",
            "type": "train",
            "amount": "700",
            "start_date": "2026-06-01",
            "cabin": "second_class",
            "payment_mode": "personal",
        },
    )
    _invoice(db, train, "DEMO-TR-20260601-001")
    _claim(db, "zhoumin", provided.id)

    overnight = _apply(
        db,
        "liwei",
        {
            "code": "SQ-VISIT-NIGHT",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "过夜拜访",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-08",
            "end_date": "2026-04-09",
            "estimated_budget": "5000",
            "cost_owner_type": "project",
            "project_id": "proj-nanhu",
            "department_id": "dept-rd",
            "client_name": "南湖精密",
            "visit_purpose": "设备验收沟通",
            "scenario": "过夜拜访",
        },
    )
    night_hotel = _order(
        db,
        {
            "application_id": overnight.id,
            "traveler_id": liwei,
            "type": "hotel",
            "amount": "380",
            "start_date": "2026-04-08",
            "end_date": "2026-04-09",
            "nights": 1,
            "city": "杭州",
        },
    )
    night_train = _order(
        db,
        {
            "application_id": overnight.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "300",
            "start_date": "2026-04-08",
            "cabin": "second_class",
        },
    )
    _invoice(db, night_hotel, "DEMO-HT-20260408-001", "杭州")
    _invoice(db, night_train, "DEMO-TR-20260408-001")
    _claim(db, "liwei", overnight.id)

    same_day = _apply(
        db,
        "liwei",
        {
            "code": "SQ-VISIT-DAY",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "当天往返拜访",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-15",
            "end_date": "2026-04-15",
            "estimated_budget": "3000",
            "cost_owner_type": "department",
            "department_id": "dept-mkt",
            "client_name": "南湖精密",
            "visit_purpose": "当天技术交流",
            "scenario": "当天往返",
        },
    )
    day_train = _order(
        db,
        {
            "application_id": same_day.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "300",
            "start_date": "2026-04-15",
            "cabin": "second_class",
        },
    )
    _invoice(db, day_train, "DEMO-TR-20260415-001")
    _claim(db, "liwei", same_day.id)
    _expect("INVOICE_ALREADY_CLAIMED", lambda: submit_reimbursement(db, _by_name(db, "liwei"), same_day.id))

    meal = _apply(
        db,
        "liwei",
        {
            "code": "SQ-VISIT-MEAL",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "客户安排用餐",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-16",
            "end_date": "2026-04-16",
            "estimated_budget": "3000",
            "cost_owner_type": "department",
            "department_id": "dept-mkt",
            "client_name": "南湖精密",
            "visit_purpose": "客户接待午宴后的拜访",
            "client_meal_dates": ["2026-04-16"],
            "scenario": "客户接待用餐",
        },
    )
    meal_train = _order(
        db,
        {
            "application_id": meal.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "280",
            "start_date": "2026-04-16",
            "cabin": "second_class",
        },
    )
    _invoice(db, meal_train, "DEMO-TR-20260416-001")
    _claim(db, "liwei", meal.id)

    training = _apply(
        db,
        "liwei",
        {
            "code": "SQ-TRAIN-001",
            "trip_type": "training",
            "traveler_ids": [liwei],
            "reason": "参加质量管理培训",
            "origin": "杭州",
            "destination": "江城",
            "start_date": "2026-05-11",
            "end_date": "2026-05-15",
            "estimated_budget": "5000",
            "cost_owner_type": "department",
            "department_id": "dept-rd",
            "event_id": "evt-qc",
            "scenario": "培训开会",
        },
    )
    _expect(
        "CONFERENCE_HOTEL_REJECTED",
        lambda: _order(
            db,
            {
                "application_id": training.id,
                "traveler_id": liwei,
                "type": "hotel",
                "amount": "320",
                "start_date": "2026-05-12",
                "end_date": "2026-05-13",
                "nights": 1,
                "city": "江城",
            },
        ),
    )
    train_go = _order(
        db,
        {
            "application_id": training.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "280",
            "start_date": "2026-05-11",
            "cabin": "second_class",
        },
    )
    train_back = _order(
        db,
        {
            "application_id": training.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "280",
            "start_date": "2026-05-15",
            "cabin": "second_class",
        },
    )
    _invoice(db, train_go, "DEMO-TR-20260511-001")
    _invoice(db, train_back, "DEMO-TR-20260515-001")
    _claim(db, "liwei", training.id)

    supplement = _apply(
        db,
        "liwei",
        {
            "code": "SQ-OVER-SELF",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "线下购票后补单",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-20",
            "end_date": "2026-04-20",
            "estimated_budget": "5000",
            "cost_owner_type": "department",
            "department_id": "dept-rd",
            "client_name": "南湖精密",
            "visit_purpose": "补单超标示例",
            "scenario": "补单超标自理",
        },
    )
    extra = _order(
        db,
        {
            "application_id": supplement.id,
            "traveler_id": liwei,
            "type": "flight",
            "amount": "2000",
            "start_date": "2026-04-20",
            "cabin": "business",
            "benchmark_amount": "800",
            "is_supplement": True,
            "over_standard_reason": "线下只买到公务舱，申请超标",
            "payment_mode": "personal",
        },
    )
    _approve(db, "order", extra.id, reject=True)
    _invoice(db, extra, "DEMO-FL-20260420-001")
    _claim(db, "liwei", supplement.id)

    approved = _apply(
        db,
        "liwei",
        {
            "code": "SQ-OVER-OK",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "超标已批准",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-21",
            "end_date": "2026-04-21",
            "estimated_budget": "5000",
            "cost_owner_type": "department",
            "department_id": "dept-rd",
            "client_name": "南湖精密",
            "visit_purpose": "客户指定航班",
            "scenario": "超标审批通过",
        },
    )
    full = _order(
        db,
        {
            "application_id": approved.id,
            "traveler_id": liwei,
            "type": "flight",
            "amount": "2000",
            "start_date": "2026-04-21",
            "cabin": "business",
            "benchmark_amount": "800",
            "over_standard_reason": "客户指定航班时刻",
        },
    )
    _approve(db, "order", full.id, approved_amount="2000")
    _invoice(db, full, "DEMO-FL-20260421-001")
    _claim(db, "liwei", approved.id)

    changed = _apply(
        db,
        "liwei",
        {
            "code": "SQ-CHANGE-FEE",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "改签未批",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-22",
            "end_date": "2026-04-22",
            "estimated_budget": "3000",
            "cost_owner_type": "department",
            "department_id": "dept-rd",
            "client_name": "南湖精密",
            "visit_purpose": "改签费示例",
            "scenario": "改签未批",
        },
    )
    fee = _order(
        db,
        {
            "application_id": changed.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "300",
            "start_date": "2026-04-22",
            "cabin": "second_class",
            "change_fee": "80",
            "change_approved": False,
        },
    )
    _invoice(db, fee, "DEMO-TR-20260422-001")
    _claim(db, "liwei", changed.id)

    frozen = _apply(
        db,
        "liwei",
        {
            "code": "SQ-VISIT-FREEZE",
            "trip_type": "client_visit",
            "traveler_ids": [liwei],
            "reason": "用来观察制度版本冻结",
            "origin": "上海",
            "destination": "杭州",
            "start_date": "2026-04-27",
            "end_date": "2026-04-28",
            "estimated_budget": "5000",
            "cost_owner_type": "department",
            "department_id": "dept-rd",
            "client_name": "南湖精密",
            "visit_purpose": "住宿超当日限额，按旧标准核定",
            "scenario": "制度版本冻结",
        },
    )
    freeze_hotel = _order(
        db,
        {
            "application_id": frozen.id,
            "traveler_id": liwei,
            "type": "hotel",
            "amount": "500",
            "start_date": "2026-04-27",
            "end_date": "2026-04-28",
            "nights": 1,
            "city": "杭州",
            "over_standard_reason": "房价高于专员二线限额",
        },
    )
    _approve(db, "order", freeze_hotel.id, reject=True)
    freeze_train = _order(
        db,
        {
            "application_id": frozen.id,
            "traveler_id": liwei,
            "type": "train",
            "amount": "300",
            "start_date": "2026-04-27",
            "cabin": "second_class",
        },
    )
    _invoice(db, freeze_hotel, "DEMO-HT-20260427-001", "杭州")
    _invoice(db, freeze_train, "DEMO-TR-20260427-001")
    _claim(db, "liwei", frozen.id)

    pending = _apply(
        db,
        "liwei",
        {
            "code": "SQ-ONSITE-CHANGE",
            "trip_type": "onsite",
            "traveler_ids": [liwei],
            "reason": "河澄设备驻场，中途返回尚未补批",
            "origin": "杭州",
            "destination": "成都",
            "start_date": "2026-07-06",
            "end_date": "2026-07-12",
            "estimated_budget": "12000",
            "cost_owner_type": "project",
            "project_id": "proj-hechuan",
            "department_id": "dept-rd",
            "scenario": "中途返回未补批",
        },
    )
    child = create_change(
        db,
        _by_name(db, "liwei"),
        pending.id,
        {
            "reason": "驻场中途临时返回，待补批",
            "start_date": "2026-07-06",
            "end_date": "2026-07-12",
            "segments": [
                {"start": "2026-07-06", "end": "2026-07-08", "origin": "杭州", "destination": "成都", "kind": "onsite"},
                {"start": "2026-07-09", "end": "2026-07-09", "origin": "成都", "destination": "杭州", "kind": "gap"},
                {"start": "2026-07-10", "end": "2026-07-12", "origin": "杭州", "destination": "成都", "kind": "onsite"},
            ],
        },
    )
    submit_application(db, _by_name(db, "liwei"), child.id)
    _claim(db, "liwei", pending.id)


def seed(reset: bool = False):
    init_db()
    if reset:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.scalar(select(User.id)):
            return
        seed_reference(db)
        seed_scenarios(db)
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导入虚构演示数据")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    seed(reset=True if args.reset or True else False)
