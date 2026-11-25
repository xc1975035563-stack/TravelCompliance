"""不调用对话模型的核对用例。期望金额和拒绝原因写死在用例里。"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal

from app.engine import (
    Application,
    CalcContext,
    EventInfo,
    InvoiceDoc,
    Order,
    Policy,
    Segment,
    Traveler,
    aggregate_costs,
    calculate,
    classify_import_rows,
    explain_result,
    validate_order,
)
from app.errors import BusinessError
from app.money import D

STAFF = Traveler("t-staff", "李维", "staff")
MANAGER = Traveler("t-manager", "周敏", "manager")


def _policy(
    code="v1",
    action="submit_approval",
    weekend=False,
    hotel=None,
    drop=None,
    split_gap=1,
):
    standards = {}
    hotels = {
        ("staff", "T1"): "500",
        ("staff", "T2"): "400",
        ("staff", "T3"): "300",
        ("manager", "T1"): "800",
        ("manager", "T2"): "650",
        ("manager", "T3"): "500",
    }
    if hotel:
        hotels.update(hotel)
    meals = {"T1": "120", "T2": "100", "T3": "80"}
    locals_ = {"T1": "80", "T2": "60", "T3": "40"}
    for rank in ("staff", "manager"):
        standards[(rank, "*", "flight")] = {
            "allowed": ["economy"] if rank == "staff" else ["economy", "business"]
        }
        standards[(rank, "*", "train")] = {
            "allowed": ["second_class"] if rank == "staff" else ["second_class", "first_class"]
        }
        for tier in ("T1", "T2", "T3"):
            standards[(rank, tier, "hotel")] = {"amount": D(hotels[(rank, tier)])}
            standards[(rank, tier, "meal")] = {"amount": D(meals[tier])}
            standards[(rank, tier, "local")] = {"amount": D(locals_[tier])}
    for key in drop or []:
        standards.pop(key, None)
    return Policy(
        code=code,
        over_standard_action=action,
        onsite_weekend_allowance=weekend,
        onsite_split_gap_days=split_gap,
        standby_day_allowance=True,
        standards=standards,
        city_tiers={"上海": "T1", "杭州": "T2", "成都": "T2", "江城": "T3"},
        dedup_fields=["traveler", "type", "date", "amount", "application_code"],
    )


def _app(trip, traveler, start, end, policy=None, **kwargs):
    policy = policy or _policy()
    data = dict(
        id=kwargs.pop("id", f"app-{trip}"),
        code=kwargs.pop("code", f"SQ-{trip}"),
        status="approved",
        trip_type=trip,
        travelers=[traveler],
        reason=kwargs.pop("reason", "演示事由"),
        origin=kwargs.pop("origin", "杭州"),
        destination=kwargs.pop("destination", "上海"),
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        headcount=1,
        estimated_budget=D(kwargs.pop("budget", "20000")),
        project_id=kwargs.pop("project_id", None),
        project_name=kwargs.pop("project_name", None),
        department_id=kwargs.pop("department_id", "dept-rd"),
        department_name=kwargs.pop("department_name", "研发部"),
        cost_owner_type=kwargs.pop("cost_owner_type", "project"),
        cost_center_code=kwargs.pop("cost_center_code", "CC-QH-001"),
        client_name=kwargs.pop("client_name", None),
        visit_purpose=kwargs.pop("visit_purpose", None),
        event=kwargs.pop("event", None),
        policy=policy,
        created_at=kwargs.pop("created_at", datetime(2026, 1, 1, 8, 0, 0)),
    )
    data.update(kwargs)
    return Application(**data)


def _ticket(oid, traveler, kind, day, amount, **kwargs):
    return Order(
        id=oid,
        type=kind,
        traveler_id=traveler.id,
        amount=D(amount),
        start=date.fromisoformat(day),
        application_id=kwargs.pop("application_id", "app"),
        payment_mode=kwargs.pop("payment_mode", "personal"),
        cabin=kwargs.pop("cabin", "economy" if kind == "flight" else "second_class"),
        benchmark_amount=D(kwargs["benchmark"]) if "benchmark" in kwargs else None,
        is_supplement=kwargs.pop("is_supplement", False),
        change_fee=D(kwargs.pop("change_fee", "0")),
        refund_fee=D(kwargs.pop("refund_fee", "0")),
        change_approved=kwargs.pop("change_approved", False),
        over_standard_status=kwargs.pop("over_standard_status", "ok"),
        approved_reimburse_amount=D(kwargs["approved_amount"]) if "approved_amount" in kwargs else None,
        over_standard_reason=kwargs.pop("over_standard_reason", None),
        nights=kwargs.pop("nights", None),
        end=date.fromisoformat(kwargs["end"]) if "end" in kwargs else None,
        city=kwargs.pop("city", None),
        scope=kwargs.pop("scope", None),
        destination=kwargs.pop("destination", None),
        origin=kwargs.pop("origin", None),
    )


def _inv(iid, order, traveler, amount, day, **kwargs):
    return InvoiceDoc(
        id=iid,
        number=kwargs.pop("number", f"INV-{iid}"),
        category=kwargs.pop("category", order.type if order else "transport"),
        amount=D(amount),
        verification=kwargs.pop("verification", "pass"),
        traveler_id=kwargs.pop("traveler_id", traveler.id if traveler else None),
        date=date.fromisoformat(day) if day else None,
        city=kwargs.pop("city", None),
        order_id=order.id if order else kwargs.pop("order_id", None),
        status=kwargs.pop("status", "available"),
        nights=kwargs.pop("nights", None),
    )


def _event(**kwargs):
    return EventInfo(
        id=kwargs.get("id", "evt-1"),
        name=kwargs.get("name", "江城质量管理培训"),
        organizer=kwargs.get("organizer", "江城培训中心"),
        city=kwargs.get("city", "江城"),
        report_date=date.fromisoformat(kwargs.get("report", "2026-05-11")),
        event_start=date.fromisoformat(kwargs.get("event_start", "2026-05-12")),
        event_end=date.fromisoformat(kwargs.get("event_end", "2026-05-14")),
        return_date=date.fromisoformat(kwargs.get("return_date", "2026-05-15")),
        organizer_provides_lodging=kwargs.get("lodging", True),
        organizer_provides_meals=kwargs.get("meals", True),
        organizer_provides_local=kwargs.get("local", True),
    )


def _calc(app, orders=None, invoices=None, others=None):
    for order in orders or []:
        order.application_id = app.id
    return calculate(
        CalcContext(application=app, orders=orders or [], invoices=invoices or [], other_applications=others or [])
    )


def _pair(order, traveler, day, amount=None, **kwargs):
    inv = _inv(f"inv-{order.id}", order, traveler, amount if amount is not None else order.amount, day, **kwargs)
    return inv


def case_onsite_main():
    app = _app(
        "onsite",
        STAFF,
        "2026-03-02",
        "2026-03-10",
        id="onsite-main",
        project_id="proj-qinghe",
        project_name="青禾工业项目组",
        destination="上海",
        origin="杭州",
        budget="9000",
    )
    hotel = _ticket("ht1", STAFF, "hotel", "2026-03-02", "3840", end="2026-03-10", nights=8, cabin=None, city="上海")
    go = _ticket("fl1", STAFF, "flight", "2026-03-02", "800", payment_mode="company", cabin="economy")
    back = _ticket("fl2", STAFF, "flight", "2026-03-10", "800", payment_mode="company", cabin="economy")
    invoices = [
        _pair(hotel, STAFF, "2026-03-02", city="上海", category="hotel"),
        _pair(go, STAFF, "2026-03-02", category="flight"),
        _pair(back, STAFF, "2026-03-10", category="flight"),
    ]
    return _calc(app, [hotel, go, back], invoices)


def case_onsite_overlap():
    policy = _policy()
    first = _app(
        "onsite",
        STAFF,
        "2026-03-02",
        "2026-03-10",
        id="onsite-first",
        project_id="proj-qinghe",
        created_at=datetime(2026, 2, 1, 9, 0, 0),
        policy=policy,
    )
    second = _app(
        "onsite",
        STAFF,
        "2026-03-08",
        "2026-03-12",
        id="onsite-second",
        project_id="proj-qinghe",
        created_at=datetime(2026, 2, 2, 9, 0, 0),
        policy=policy,
    )
    return _calc(second, others=[first])


def case_onsite_split():
    policy = _policy()
    first = _app(
        "onsite",
        STAFF,
        "2026-03-02",
        "2026-03-10",
        id="split-first",
        project_id="proj-qinghe",
        created_at=datetime(2026, 2, 1, 9, 0, 0),
        policy=policy,
    )
    second = _app(
        "onsite",
        STAFF,
        "2026-03-11",
        "2026-03-13",
        id="split-second",
        project_id="proj-qinghe",
        created_at=datetime(2026, 2, 3, 9, 0, 0),
        policy=policy,
    )
    return _calc(second, others=[first])


def case_onsite_provided():
    app = _app(
        "onsite",
        MANAGER,
        "2026-06-01",
        "2026-06-09",
        id="onsite-provided",
        project_id="proj-qinghe",
        project_name="青禾工业项目组",
        destination="成都",
        origin="上海",
        project_provides_lodging=True,
        project_provides_meals=True,
        budget="8000",
    )
    hotel = _ticket("htp", MANAGER, "hotel", "2026-06-01", "3000", end="2026-06-09", nights=8, cabin=None, city="成都")
    train = _ticket("trp", MANAGER, "train", "2026-06-01", "700", cabin="second_class")
    invoices = [
        _pair(hotel, MANAGER, "2026-06-01", city="成都", category="hotel"),
        _pair(train, MANAGER, "2026-06-01", category="train"),
    ]
    return _calc(app, [hotel, train], invoices)


def case_mid_unapproved():
    app = _app(
        "onsite",
        STAFF,
        "2026-03-02",
        "2026-03-10",
        id="mid-bad",
        project_id="proj-qinghe",
        has_unapproved_change=True,
        change_approved=False,
    )
    return _calc(app)


def case_mid_approved():
    app = _app(
        "onsite",
        STAFF,
        "2026-03-02",
        "2026-03-10",
        id="mid-ok",
        project_id="proj-qinghe",
        destination="上海",
        change_approved=True,
        segments=[
            Segment(date(2026, 3, 2), date(2026, 3, 5), "杭州", "上海", "onsite"),
            Segment(date(2026, 3, 6), date(2026, 3, 7), "上海", "杭州", "gap"),
            Segment(date(2026, 3, 8), date(2026, 3, 10), "杭州", "上海", "onsite"),
        ],
        budget="20000",
    )
    hotel = _ticket("htm", STAFF, "hotel", "2026-03-02", "2400", end="2026-03-10", nights=5, cabin=None, city="上海")
    flights = [
        _ticket("f-out", STAFF, "flight", "2026-03-02", "800"),
        _ticket("f-home", STAFF, "flight", "2026-03-05", "500"),
        _ticket("f-back", STAFF, "flight", "2026-03-08", "500"),
        _ticket("f-end", STAFF, "flight", "2026-03-10", "800"),
    ]
    invoices = [_pair(hotel, STAFF, "2026-03-02", city="上海", category="hotel")]
    invoices += [_pair(item, STAFF, item.start.isoformat(), category="flight") for item in flights]
    return _calc(app, [hotel, *flights], invoices)


def case_visit_overnight():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-08",
        "2026-04-09",
        id="visit-night",
        destination="杭州",
        origin="上海",
        project_id="proj-nanhu",
        project_name="南湖精密拜访项目",
        cost_center_code="CC-NH-014",
        department_id="dept-mkt",
        client_name="南湖精密",
        visit_purpose="设备验收沟通",
        budget="3000",
    )
    hotel = _ticket("vht", STAFF, "hotel", "2026-04-08", "380", end="2026-04-09", nights=1, cabin=None, city="杭州")
    train = _ticket("vtr", STAFF, "train", "2026-04-08", "300")
    invoices = [
        _pair(hotel, STAFF, "2026-04-08", city="杭州", category="hotel"),
        _pair(train, STAFF, "2026-04-08", category="train"),
    ]
    return _calc(app, [hotel, train], invoices)


def case_visit_same_day():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        id="visit-day",
        destination="杭州",
        origin="上海",
        cost_owner_type="department",
        project_id=None,
        department_id="dept-mkt",
        department_name="市场部",
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="当天技术交流",
        budget="2000",
    )
    train = _ticket("dtr", STAFF, "train", "2026-04-15", "300")
    return _calc(app, [train], [_pair(train, STAFF, "2026-04-15", category="train")])


def case_visit_cascade():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-08",
        "2026-04-09",
        id="visit-cascade",
        destination="杭州",
        origin="上海",
        project_id="proj-nanhu",
        cost_center_code="CC-NH-014",
        client_name="南湖精密",
        visit_purpose="过夜拜访但未交住宿票",
        budget="3000",
    )
    train = _ticket("ctr", STAFF, "train", "2026-04-08", "300")
    return _calc(app, [train], [_pair(train, STAFF, "2026-04-08", category="train")])


def case_visit_host_meal():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-16",
        "2026-04-16",
        id="visit-meal",
        destination="杭州",
        origin="上海",
        cost_owner_type="department",
        project_id=None,
        department_id="dept-mkt",
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="客户安排午宴的拜访",
        client_meal_dates=[date(2026, 4, 16)],
        budget="2000",
    )
    train = _ticket("mtr", STAFF, "train", "2026-04-16", "280")
    return _calc(app, [train], [_pair(train, STAFF, "2026-04-16", category="train")])


def case_training_edge():
    app = _app(
        "training",
        STAFF,
        "2026-05-11",
        "2026-05-15",
        id="train-edge",
        destination="江城",
        origin="杭州",
        cost_owner_type="department",
        project_id=None,
        department_id="dept-rd",
        cost_center_code="CC-RD",
        event=_event(),
        budget="5000",
    )
    go = _ticket("tg", STAFF, "train", "2026-05-11", "280")
    back = _ticket("tb", STAFF, "train", "2026-05-15", "280")
    invoices = [
        _pair(go, STAFF, "2026-05-11", category="train"),
        _pair(back, STAFF, "2026-05-15", category="train"),
    ]
    return _calc(app, [go, back], invoices)


def case_training_self():
    app = _app(
        "training",
        STAFF,
        "2026-05-11",
        "2026-05-14",
        id="train-self",
        destination="江城",
        origin="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-RD",
        event=_event(
            lodging=False,
            meals=False,
            local=False,
            event_end="2026-05-13",
            return_date="2026-05-14",
            name="滨江工艺分享会",
            organizer="江城培训中心",
        ),
        budget="5000",
    )
    hotel = _ticket("ths", STAFF, "hotel", "2026-05-11", "840", end="2026-05-14", nights=3, cabin=None, city="江城")
    train = _ticket("tts", STAFF, "train", "2026-05-11", "400")
    invoices = [
        _pair(hotel, STAFF, "2026-05-11", city="江城", category="hotel"),
        _pair(train, STAFF, "2026-05-11", category="train"),
    ]
    return _calc(app, [hotel, train], invoices)


def case_training_mismatch():
    app = _app(
        "training",
        STAFF,
        "2026-05-11",
        "2026-05-15",
        destination="上海",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-RD",
        event=_event(),
    )
    return _calc(app)


def case_training_missing():
    app = _app(
        "training",
        STAFF,
        "2026-05-11",
        "2026-05-15",
        destination="江城",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-RD",
        event=None,
    )
    return _calc(app)


def case_missing_client():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name=None,
        visit_purpose="只写了事由",
    )
    return _calc(app)


def case_missing_project():
    app = _app(
        "onsite",
        STAFF,
        "2026-03-02",
        "2026-03-10",
        project_id=None,
        cost_owner_type="project",
        cost_center_code=None,
    )
    return _calc(app)


def case_no_app():
    return calculate(CalcContext(application=None))


def case_not_approved():
    app = _app("client_visit", STAFF, "2026-04-15", "2026-04-15", status="pending_dept", destination="杭州", cost_owner_type="department", project_id=None, cost_center_code="CC-MKT", client_name="南湖精密", visit_purpose="尚未审批")
    return _calc(app)


def _over_app():
    return _app(
        "client_visit",
        STAFF,
        "2026-04-20",
        "2026-04-20",
        id="over-app",
        destination="杭州",
        origin="上海",
        cost_owner_type="department",
        project_id=None,
        department_id="dept-mkt",
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="超标机票示例",
        budget="5000",
    )


def case_over_self():
    app = _over_app()
    flight = _ticket(
        "ob",
        STAFF,
        "flight",
        "2026-04-20",
        "2000",
        cabin="business",
        benchmark="800",
        over_standard_status="pending",
        over_standard_reason="只有该航班能赶上客户会议",
    )
    return _calc(app, [flight], [_pair(flight, STAFF, "2026-04-20", category="flight")])


def case_over_approved():
    app = _over_app()
    app.id = "over-ok"
    flight = _ticket(
        "oa",
        STAFF,
        "flight",
        "2026-04-20",
        "2000",
        cabin="business",
        benchmark="800",
        over_standard_status="approved",
        approved_amount="2000",
        over_standard_reason="客户指定航班，已批准全额",
    )
    return _calc(app, [flight], [_pair(flight, STAFF, "2026-04-20", category="flight")])


def case_over_block():
    app = _over_app()
    app.policy = _policy(action="block")
    flight = _ticket("obk", STAFF, "flight", "2026-04-20", "2000", cabin="business", benchmark="800")
    return _calc(app, [flight], [_pair(flight, STAFF, "2026-04-20", category="flight")])


def case_undefined():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-08",
        "2026-04-09",
        destination="杭州",
        project_id="proj-nanhu",
        cost_center_code="CC-NH-014",
        client_name="南湖精密",
        visit_purpose="差标缺失",
        policy=_policy(drop=[("staff", "T2", "hotel")]),
    )
    return _calc(app)


def case_benchmark_missing():
    app = _over_app()
    flight = _ticket("bm", STAFF, "flight", "2026-04-20", "2000", cabin="business")
    return _calc(app, [flight], [_pair(flight, STAFF, "2026-04-20", category="flight")])


def case_duplicate_invoice():
    app = case_visit_same_day()
    # rebuild with claimed invoice
    base = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="重复用票",
    )
    train = _ticket("dup", STAFF, "train", "2026-04-15", "300")
    inv = _pair(train, STAFF, "2026-04-15", category="train", status="claimed")
    return _calc(base, [train], [inv])


def case_supplement():
    app = _over_app()
    app.id = "sup-app"
    flight = _ticket(
        "sup",
        STAFF,
        "flight",
        "2026-04-20",
        "2000",
        cabin="business",
        benchmark="800",
        is_supplement=True,
        over_standard_status="pending",
        over_standard_reason="线下购票后补单",
    )
    return _calc(app, [flight], [_pair(flight, STAFF, "2026-04-20", category="flight")])


def case_change_unapproved():
    app = case_visit_same_day()
    return None


def case_change_fee(approved: bool):
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="改签费",
        budget="2000",
    )
    train = _ticket("chg", STAFF, "train", "2026-04-15", "300", change_fee="80", change_approved=approved)
    return _calc(app, [train], [_pair(train, STAFF, "2026-04-15", category="train")])


def case_budget():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="预算不足",
        budget="200",
    )
    train = _ticket("bd", STAFF, "train", "2026-04-15", "300")
    return _calc(app, [train], [_pair(train, STAFF, "2026-04-15", category="train")])


def case_company():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="公司统付",
        budget="2000",
    )
    train = _ticket("cp", STAFF, "train", "2026-04-15", "300", payment_mode="company")
    return _calc(app, [train], [_pair(train, STAFF, "2026-04-15", category="train")])


def case_three_way():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-08",
        "2026-04-09",
        destination="杭州",
        project_id="proj-nanhu",
        cost_center_code="CC-NH-014",
        client_name="南湖精密",
        visit_purpose="票面出行人不符",
        budget="3000",
    )
    hotel = _ticket("th", STAFF, "hotel", "2026-04-08", "380", end="2026-04-09", nights=1, cabin=None, city="杭州")
    train = _ticket("tt", STAFF, "train", "2026-04-08", "300")
    invoices = [
        _pair(hotel, STAFF, "2026-04-08", city="杭州", category="hotel"),
        _pair(train, STAFF, "2026-04-08", category="train", traveler_id="someone-else"),
    ]
    return _calc(app, [hotel, train], invoices)


def case_verify(result_code, marker):
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="模拟查验",
        budget="2000",
    )
    train = _ticket("vf", STAFF, "train", "2026-04-15", "300")
    inv = _pair(train, STAFF, "2026-04-15", category="train", verification=result_code)
    return _calc(app, [train], [inv])


def case_amount_mismatch():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="金额不符",
        budget="2000",
    )
    train = _ticket("am", STAFF, "train", "2026-04-15", "300")
    inv = _pair(train, STAFF, "2026-04-15", amount="280", category="train", verification="pass")
    return _calc(app, [train], [inv])


def case_missing_cabin():
    app = _app(
        "client_visit",
        STAFF,
        "2026-04-15",
        "2026-04-15",
        destination="杭州",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-MKT",
        client_name="南湖精密",
        visit_purpose="缺席别",
        budget="20000",
    )
    train = _ticket("nc", STAFF, "train", "2026-04-15", "9999", cabin=None)
    return _calc(app, [train], [])


def case_order_conference():
    app = _app(
        "training",
        STAFF,
        "2026-05-11",
        "2026-05-15",
        destination="江城",
        cost_owner_type="department",
        project_id=None,
        cost_center_code="CC-RD",
        event=_event(),
        budget="5000",
    )
    hotel = _ticket("ch", STAFF, "hotel", "2026-05-12", "320", end="2026-05-13", nights=1, cabin=None, city="江城")
    hotel.application_id = app.id
    return validate_order(hotel, app, Decimal("0"))


def case_order_supplement():
    app = _over_app()
    flight = _ticket(
        "so",
        STAFF,
        "flight",
        "2026-04-20",
        "2000",
        cabin="business",
        benchmark="800",
        is_supplement=True,
        over_standard_reason="线下购票补单",
    )
    flight.application_id = app.id
    return validate_order(flight, app, Decimal("0"))


def case_dirty():
    rows = [
        {"type": "train", "date": "2026-04-15", "amount": "300", "application_code": "SQ-1"},
        {"traveler": "李维", "type": "train", "date": "2026-04-15", "application_code": "SQ-1"},
        {"traveler": "李维", "type": "hotel", "date": "2026-03-02", "amount": "100", "application_code": "SQ-2", "trip_type": "onsite"},
        {"traveler": "李维", "type": "train", "date": "2026-04-15", "amount": "300", "application_code": "SQ-1", "trip_type": "client_visit", "client_name": "南湖精密"},
    ]
    return classify_import_rows(rows, set(), ["traveler", "type", "date", "amount", "application_code"])


def case_dedup():
    row = {"traveler": "李维", "type": "train", "date": "2026-04-15", "amount": "300", "application_code": "SQ-1", "trip_type": "client_visit", "client_name": "南湖精密"}
    other = dict(row, amount="280", date="2026-04-16")
    return classify_import_rows([row, dict(row), other], set(), ["traveler", "type", "date", "amount", "application_code"])


def case_snapshot():
    def one(code, limit):
        policy = _policy(code=code, hotel={("staff", "T2"): limit})
        app = _app(
            "client_visit",
            STAFF,
            "2026-04-08",
            "2026-04-09",
            destination="杭州",
            project_id="proj-nanhu",
            cost_center_code="CC-NH-014",
            client_name="南湖精密",
            visit_purpose="制度版本对照",
            policy=policy,
            budget="5000",
        )
        hotel = _ticket("sv", STAFF, "hotel", "2026-04-08", "500", end="2026-04-09", nights=1, cabin=None, city="杭州")
        train = _ticket("st", STAFF, "train", "2026-04-08", "300")
        invoices = [
            _pair(hotel, STAFF, "2026-04-08", city="杭州", category="hotel"),
            _pair(train, STAFF, "2026-04-08", category="train"),
        ]
        return _calc(app, [hotel, train], invoices)

    first = one("v1", "400")
    second = one("v2", "600")
    stored = json.loads(json.dumps(first))
    return {
        "v1_amount": first["personal_claimable"],
        "v2_amount": second["personal_claimable"],
        "stored_amount": stored["personal_claimable"],
        "stored_version": stored["policy_version"],
        "differs": first["personal_claimable"] != second["personal_claimable"],
        "stored_unchanged": stored["personal_claimable"] == first["personal_claimable"] and stored["policy_version"] == "v1",
    }


def case_model():
    result = case_visit_same_day()
    blocked = explain_result(result, proposed_amount="99999.00")
    plain = explain_result(result, proposed_amount=None)
    return {
        "guard_code": blocked["error_code"],
        "plain_ok": plain["ok"],
        "plain_amount": plain["personal_claimable"],
        "engine_amount": result["personal_claimable"],
        "quotes_clause": "VISIT-SAMEDAY-01" in plain["text"],
        "invents_amount": "99999.00" in plain["text"],
        "says_simulated": "模拟查验" in plain["text"],
    }


def case_aggregate_bad():
    try:
        aggregate_costs(
            [],
            start=date(2026, 1, 1),
            end=date(2026, 12, 31),
            group_by="project",
            known_departments={"dept-rd"},
            known_projects={"proj-qinghe"},
            project_id="proj-not-exist",
        )
        return {"error_code": None, "total": "0.00"}
    except BusinessError as exc:
        return {"error_code": exc.code, "message": exc.message, "total": None}


def case_period_bad():
    try:
        aggregate_costs(
            [],
            start=date(2026, 5, 2),
            end=date(2026, 5, 1),
            group_by="trip_type",
            known_departments=set(),
            known_projects=set(),
        )
        return {"error_code": None, "total": "0.00"}
    except BusinessError as exc:
        return {"error_code": exc.code, "message": exc.message, "total": None}


CASES = [
    {"id": "onsite_weekend_allowance", "trip_type": "onsite", "title": "驻场跨 7 天以上，周末不发补助，统付机票不付给个人", "expect_ok": True, "run": case_onsite_main, "expect": {"ok": True, "calculated": True, "personal_claimable": "5240.00", "allowance": "1400.00", "company_paid": "1600.00", "self_pay": "0.00", "budget_exceeded": False, "clause_codes": ["ONSITE-WEEKEND-01", "ONSITE-HOTEL-01", "ONSITE-PROJECT-01", "COMMON-COMPANY-PAY-01", "COMMON-VERSION-01"], "allowance_day_count": 7, "excluded_date": "2026-03-07"}},
    {"id": "onsite_overlap_rejected", "trip_type": "onsite", "title": "同一人同一项目驻场日期重叠，后单拒绝", "expect_ok": False, "run": case_onsite_overlap, "expect": {"ok": False, "calculated": True, "personal_claimable": "0.00", "refusal_code": "ONSITE_OVERLAP", "clause_codes": ["ONSITE-OVERLAP-01"]}},
    {"id": "onsite_split_rejected", "trip_type": "onsite", "title": "连续驻场拆成相接的短途申请，后单拒绝", "expect_ok": False, "run": case_onsite_split, "expect": {"ok": False, "refusal_code": "ONSITE_SPLIT", "personal_claimable": "0.00", "clause_codes": ["ONSITE-SPLIT-01"]}},
    {"id": "onsite_project_provided", "trip_type": "onsite", "title": "项目部提供住宿和用餐，停发住宿费和伙食补助", "expect_ok": True, "run": case_onsite_provided, "expect": {"ok": True, "personal_claimable": "1120.00", "allowance": "420.00", "clause_codes": ["ONSITE-PROVIDED-01"], "reason_contains": ["项目部已提供住宿", "伙食补助停发"]}},
    {"id": "onsite_mid_return_unapproved", "trip_type": "onsite", "title": "驻场中途返回未补批，整单按异常拒绝", "expect_ok": False, "run": case_mid_unapproved, "expect": {"ok": False, "refusal_code": "CHANGE_NOT_APPROVED", "personal_claimable": "0.00", "clause_codes": ["ONSITE-MIDRETURN-01"]}},
    {"id": "onsite_mid_return_approved", "trip_type": "onsite", "title": "驻场中途返回已补批，只按在场日发补助，不按两段满额", "expect_ok": True, "run": case_mid_approved, "expect": {"ok": True, "allowance": "1200.00", "personal_claimable": "6200.00", "clause_codes": ["ONSITE-MIDRETURN-01"], "excluded_date": "2026-03-06", "allowance_day_count": 6}},
    {"id": "visit_overnight_ok", "trip_type": "client_visit", "title": "过夜客户拜访，住宿在限额内凭票核定", "expect_ok": True, "run": case_visit_overnight, "expect": {"ok": True, "personal_claimable": "1000.00", "allowance": "320.00", "self_pay": "0.00", "clause_codes": ["VISIT-REQUIRED-01", "VISIT-COST-01"]}},
    {"id": "visit_same_day_no_hotel", "trip_type": "client_visit", "title": "当天往返拜访无住宿发票，仍核定交通和当天补助", "expect_ok": True, "run": case_visit_same_day, "expect": {"ok": True, "personal_claimable": "460.00", "allowance": "160.00", "clause_codes": ["VISIT-SAMEDAY-01", "VISIT-COST-01"], "clause_absent": ["VISIT-NOHOTEL-01", "ONSITE-NOHOTEL-01"]}},
    {"id": "visit_overnight_no_hotel_cascade", "trip_type": "client_visit", "title": "过夜拜访无住宿发票，交通和补助一并拒发", "expect_ok": False, "run": case_visit_cascade, "expect": {"ok": False, "personal_claimable": "0.00", "allowance": "0.00", "company_paid": "0.00", "refusal_code": "CASCADE_NO_LODGING_INVOICE", "clause_codes": ["VISIT-NOHOTEL-01"]}},
    {"id": "visit_client_meal_stops_allowance", "trip_type": "client_visit", "title": "客户安排用餐后，当日伙食补助停发", "expect_ok": True, "run": case_visit_host_meal, "expect": {"ok": True, "personal_claimable": "340.00", "allowance": "60.00", "clause_codes": ["VISIT-HOST-01"], "reason_contains": ["伙食补助停发"]}},
    {"id": "visit_missing_client", "trip_type": "client_visit", "title": "拜访没有对象时拒算", "expect_ok": False, "run": case_missing_client, "expect": {"calculated": False, "ok": False, "personal_claimable": None, "refusal_code": "MISSING_REQUIRED_FIELDS", "reason_contains": ["拜访无对象"]}},
    {"id": "training_edge_days_only", "trip_type": "training", "title": "举办方安排食宿，只报往返交通和首尾两天补助", "expect_ok": True, "run": case_training_edge, "expect": {"ok": True, "personal_claimable": "800.00", "allowance": "240.00", "allowance_day_count": 2, "clause_codes": ["TRAIN-EDGE-01", "TRAIN-LODGING-01"], "reason_contains": ["不因无住宿发票拒绝往返交通"], "clause_absent": ["ONSITE-NOHOTEL-01"]}},
    {"id": "training_full_period_allowance_rejected", "trip_type": "training", "title": "会期补助按全程满额发放应失败", "expect_ok": True, "run": case_training_edge, "expect": {"ok": True, "allowance": "240.00", "allowance_day_count": 2, "personal_claimable": "800.00"}},
    {"id": "training_conference_hotel_rejected", "trip_type": "training", "title": "举办方已安排住宿时，会期酒店订单被拒绝", "expect_ok": False, "run": case_order_conference, "expect": {"ok": False, "error_code": "CONFERENCE_HOTEL_REJECTED", "clause_codes": ["TRAIN-LODGING-01"]}},
    {"id": "training_self_lodge_within_limit", "trip_type": "training", "title": "自行安排住宿时，限额内凭票核定，补助仍只发首尾两天", "expect_ok": True, "run": case_training_self, "expect": {"ok": True, "personal_claimable": "1480.00", "allowance": "240.00", "allowance_day_count": 2, "clause_codes": ["TRAIN-SELF-01", "TRAIN-EDGE-01"]}},
    {"id": "training_location_mismatch", "trip_type": "training", "title": "会议地点与申请不一致则拒绝", "expect_ok": False, "run": case_training_mismatch, "expect": {"ok": False, "personal_claimable": "0.00", "refusal_code": "TRAINING_MISMATCH", "clause_codes": ["TRAIN-MATCH-01"]}},
    {"id": "training_missing_event", "trip_type": "training", "title": "培训没有会议信息时拒算", "expect_ok": False, "run": case_training_missing, "expect": {"calculated": False, "personal_claimable": None, "refusal_code": "MISSING_REQUIRED_FIELDS", "reason_contains": ["培训无会议信息"]}},
    {"id": "unauthorized_no_application", "trip_type": "client_visit", "title": "无申请单整单不可报", "expect_ok": False, "run": case_no_app, "expect": {"ok": False, "personal_claimable": "0.00", "refusal_code": "UNAUTHORIZED_TRAVEL", "clause_codes": ["COMMON-APPROVAL-01"]}},
    {"id": "application_not_approved", "trip_type": "client_visit", "title": "申请未通过时整单不可报", "expect_ok": False, "run": case_not_approved, "expect": {"ok": False, "personal_claimable": "0.00", "refusal_code": "APPLICATION_NOT_APPROVED"}},
    {"id": "over_standard_self_pay", "trip_type": "client_visit", "title": "超标舱位未批准时，超标部分自理", "expect_ok": True, "run": case_over_self, "expect": {"ok": True, "personal_claimable": "960.00", "self_pay": "1200.00", "allowance": "160.00", "clause_codes": ["COMMON-OVER-01"]}},
    {"id": "over_standard_approved", "trip_type": "client_visit", "title": "超标审批通过后按批准口径入账", "expect_ok": True, "run": case_over_approved, "expect": {"ok": True, "personal_claimable": "2160.00", "self_pay": "0.00", "clause_codes": ["COMMON-OVER-01"], "reason_contains": ["批准口径"]}},
    {"id": "over_standard_blocked", "trip_type": "client_visit", "title": "制度配置为拦截时，超标订单不予入账", "expect_ok": True, "run": case_over_block, "expect": {"ok": True, "personal_claimable": "160.00", "self_pay": "0.00", "clause_codes": ["COMMON-OVER-BLOCK-01"], "reason_contains": ["拦截"]}},
    {"id": "undefined_standard", "trip_type": "client_visit", "title": "未定义差标时拒算，不给近似金额", "expect_ok": False, "run": case_undefined, "expect": {"calculated": False, "personal_claimable": None, "refusal_code": "STANDARD_UNDEFINED"}},
    {"id": "benchmark_missing_refuses", "trip_type": "client_visit", "title": "超标但没有对照价格时拒绝编造可报金额", "expect_ok": False, "run": case_benchmark_missing, "expect": {"calculated": False, "personal_claimable": None, "refusal_code": "BENCHMARK_MISSING"}},
    {"id": "duplicate_invoice_second_claim", "trip_type": "client_visit", "title": "同一张票据被第二笔报销使用时拒绝", "expect_ok": False, "run": case_duplicate_invoice, "expect": {"ok": False, "personal_claimable": "0.00", "refusal_code": "INVOICE_ALREADY_CLAIMED", "clause_codes": ["COMMON-INVOICE-DUP-01"]}},
    {"id": "supplement_cannot_bypass_standard", "trip_type": "client_visit", "title": "补单不能绕过差标，超标部分仍然自理", "expect_ok": True, "run": case_supplement, "expect": {"ok": True, "personal_claimable": "960.00", "self_pay": "1200.00", "clause_codes": ["COMMON-SUPPLEMENT-01", "COMMON-OVER-01"]}},
    {"id": "supplement_order_validation", "trip_type": "client_visit", "title": "补单写入时按差标给出合规金额而不是原价", "expect_ok": True, "run": case_order_supplement, "expect": {"ok": True, "over_standard": True, "compliant_amount": "800.00", "self_pay": "1200.00", "clause_codes": ["COMMON-SUPPLEMENT-01"]}},
    {"id": "change_fee_unapproved", "trip_type": "client_visit", "title": "未经批准的改签费个人自理", "expect_ok": True, "run": lambda: case_change_fee(False), "expect": {"ok": True, "personal_claimable": "460.00", "self_pay": "80.00", "clause_codes": ["COMMON-CHANGE-01"]}},
    {"id": "change_fee_approved", "trip_type": "client_visit", "title": "已批准的改签费可以计入", "expect_ok": True, "run": lambda: case_change_fee(True), "expect": {"ok": True, "personal_claimable": "540.00", "self_pay": "0.00", "clause_codes": ["COMMON-CHANGE-01"]}},
    {"id": "budget_exceeded_blocks_payment", "trip_type": "client_visit", "title": "报销超出已批预算时明确提示且不能直接支付", "expect_ok": True, "run": case_budget, "expect": {"ok": True, "personal_claimable": "460.00", "budget_exceeded": True, "payment_allowed": False, "clause_codes": ["COMMON-BUDGET-01"], "reason_contains": ["不能静默通过"]}},
    {"id": "company_paid_not_to_person", "trip_type": "client_visit", "title": "公司统付不进个人报销", "expect_ok": True, "run": case_company, "expect": {"ok": True, "personal_claimable": "160.00", "company_paid": "300.00", "allowance": "160.00", "clause_codes": ["COMMON-COMPANY-PAY-01"]}},
    {"id": "three_way_traveler_mismatch", "trip_type": "client_visit", "title": "票面出行人不在申请范围内，该笔交通拒报", "expect_ok": True, "run": case_three_way, "expect": {"ok": True, "personal_claimable": "700.00", "allowance": "320.00", "clause_codes": ["COMMON-THREEWAY-01"], "reason_contains": ["出行人"]}},
    {"id": "mock_invoice_not_found", "trip_type": "client_visit", "title": "模拟查验查无此票时，该笔交通不可报", "expect_ok": True, "run": lambda: case_verify("not_found", "查无此票"), "expect": {"ok": True, "personal_claimable": "160.00", "reason_contains": ["模拟查验", "查无此票"], "clause_codes": ["COMMON-MOCK-VERIFY-01"]}},
    {"id": "mock_invoice_header_mismatch", "trip_type": "client_visit", "title": "模拟查验抬头或税号不符", "expect_ok": True, "run": lambda: case_verify("header_mismatch", "抬头"), "expect": {"personal_claimable": "160.00", "reason_contains": ["模拟查验", "抬头或税号不符"]}},
    {"id": "mock_invoice_already_used", "trip_type": "client_visit", "title": "模拟查验发票已用", "expect_ok": True, "run": lambda: case_verify("already_used", "已用"), "expect": {"personal_claimable": "160.00", "reason_contains": ["模拟查验", "发票已用"]}},
    {"id": "invoice_amount_mismatch", "trip_type": "client_visit", "title": "票面金额与订单不符", "expect_ok": True, "run": case_amount_mismatch, "expect": {"personal_claimable": "160.00", "reason_contains": ["模拟查验", "票面金额与订单不符"], "clause_codes": ["COMMON-THREEWAY-01"]}},
    {"id": "missing_cabin_excluded", "trip_type": "client_visit", "title": "缺少席别的订单不进入金额计算", "expect_ok": True, "run": case_missing_cabin, "expect": {"ok": True, "personal_claimable": "160.00", "self_pay": "0.00", "excluded_count": 1, "reason_contains": ["无舱位或席别"]}},
    {"id": "onsite_missing_project", "trip_type": "onsite", "title": "驻场没有项目时拒算", "expect_ok": False, "run": case_missing_project, "expect": {"calculated": False, "personal_claimable": None, "refusal_code": "MISSING_REQUIRED_FIELDS", "reason_contains": ["驻场无项目"]}},
    {"id": "dirty_rows_excluded", "trip_type": "onsite", "title": "关键字段缺失的导入记录不进入计算，并给出数量和样例", "expect_ok": False, "run": case_dirty, "expect": {"accepted_count": 1, "dirty_count": 3, "duplicate_count": 0}},
    {"id": "duplicate_orders_deduped", "trip_type": "client_visit", "title": "重复订单按键去重并返回去重条数", "expect_ok": True, "run": case_dedup, "expect": {"accepted_count": 2, "duplicate_count": 1, "dirty_count": 0}},
    {"id": "policy_snapshot_not_rewritten", "trip_type": "client_visit", "title": "新制度版本不改写已保存的历史核定", "expect_ok": True, "run": case_snapshot, "expect": {"v1_amount": "1020.00", "v2_amount": "1120.00", "stored_amount": "1020.00", "stored_version": "v1", "differs": True, "stored_unchanged": True}},
    {"id": "model_cannot_set_amount", "trip_type": "client_visit", "title": "说明环节不能绕过规则直接给报销金额", "expect_ok": False, "run": case_model, "expect": {"guard_code": "MODEL_CANNOT_SET_AMOUNT", "plain_ok": True, "plain_amount": "460.00", "engine_amount": "460.00", "quotes_clause": True, "invents_amount": False, "says_simulated": True}},
    {"id": "reconcile_unknown_project", "trip_type": "onsite", "title": "费用归属无法匹配时拒绝汇总", "expect_ok": False, "run": case_aggregate_bad, "expect": {"error_code": "ATTRIBUTION_UNMATCHED", "total": None}},
    {"id": "reconcile_bad_period", "trip_type": "training", "title": "统计区间无效时拒绝汇总", "expect_ok": False, "run": case_period_bad, "expect": {"error_code": "PERIOD_INVALID", "total": None}},
]


def _codes(actual: dict) -> list[str]:
    found = []
    for clause in actual.get("clauses") or []:
        if isinstance(clause, dict):
            found.append(clause["code"])
        else:
            found.append(clause)
    for line in actual.get("lines") or []:
        found.extend(line.get("clause_codes") or [])
    return found


def check_expect(actual: dict, expect: dict) -> list[str]:
    errors = []
    for key, value in expect.items():
        if key == "clause_codes":
            got = _codes(actual)
            for code in value:
                if code not in got:
                    errors.append(f"缺少条款 {code}")
        elif key == "clause_absent":
            got = _codes(actual)
            for code in value:
                if code in got:
                    errors.append(f"不应出现条款 {code}")
        elif key == "reason_contains":
            blob = json.dumps(actual, ensure_ascii=False)
            for text in value:
                if text not in blob:
                    errors.append(f"结果中没有“{text}”")
        elif key == "allowance_day_count":
            got = len(actual.get("allowance_days") or [])
            if got != value:
                errors.append(f"补助天数 {got} != {value} {actual.get('allowance_days')}")
        elif key == "excluded_date":
            dates = [item.get("date") for item in actual.get("allowance_excluded_days") or []]
            if value not in dates:
                errors.append(f"未排除日期 {value}，实际 {dates}")
        else:
            if actual.get(key) != value:
                errors.append(f"{key}: 实际 {actual.get(key)!r}，期望 {value!r}")
    return errors


def run_all() -> dict:
    results = []
    passed = 0
    for case in CASES:
        try:
            actual = case["run"]()
            errors = check_expect(actual, case["expect"])
        except Exception as exc:  # noqa: BLE001 - 用例失败要留下来，不能中断整批
            actual = None
            errors = [f"{type(exc).__name__}: {exc}"]
        ok = not errors
        passed += int(ok)
        results.append(
            {
                "id": case["id"],
                "title": case["title"],
                "trip_type": case["trip_type"],
                "expect_ok": case["expect_ok"],
                "passed": ok,
                "errors": errors,
            }
        )
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "cases": results}


def list_cases() -> list[dict]:
    return [
        {"id": case["id"], "title": case["title"], "trip_type": case["trip_type"], "expect": case["expect"]}
        for case in CASES
    ]
