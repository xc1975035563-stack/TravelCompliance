"""确定性差旅核定。金额只在本模块计算，说明模块不得改写。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.clauses import TRIP_LABELS, VERIFY_LABELS, render_clause
from app.errors import BusinessError
from app.money import D, money

ZERO = Decimal("0.00")


@dataclass
class Traveler:
    id: str
    name: str
    rank: str


@dataclass
class EventInfo:
    id: str
    name: str
    organizer: str
    city: str
    report_date: date
    event_start: date
    event_end: date
    return_date: date
    organizer_provides_lodging: bool
    organizer_provides_meals: bool
    organizer_provides_local: bool


@dataclass
class Policy:
    code: str
    over_standard_action: str
    onsite_weekend_allowance: bool
    onsite_split_gap_days: int
    standby_day_allowance: bool
    standards: dict
    city_tiers: dict
    clauses: dict = field(default_factory=dict)
    dedup_fields: list | None = None

    def standard(self, rank: str, tier: str, item: str):
        return self.standards.get((rank, tier, item))

    def text(self, code: str) -> str:
        return render_clause(code, self.clauses, self.onsite_split_gap_days)


@dataclass
class Segment:
    start: date
    end: date
    origin: str
    destination: str
    kind: str = "onsite"


@dataclass
class Application:
    id: str
    code: str
    status: str
    trip_type: str
    travelers: list
    reason: str
    origin: str
    destination: str
    start: date
    end: date
    headcount: int
    estimated_budget: Decimal
    project_id: str | None = None
    project_name: str | None = None
    department_id: str | None = None
    department_name: str | None = None
    cost_owner_type: str | None = None
    cost_center_code: str | None = None
    client_name: str | None = None
    visit_purpose: str | None = None
    event: EventInfo | None = None
    project_provides_lodging: bool = False
    project_provides_meals: bool = False
    client_meal_dates: list = field(default_factory=list)
    client_transport_dates: list = field(default_factory=list)
    standby_dates: list = field(default_factory=list)
    segments: list | None = None
    change_of: str | None = None
    change_approved: bool = False
    has_unapproved_change: bool = False
    policy: Policy | None = None
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 1, 0, 0, 0))
    applicant_id: str = ""


@dataclass
class Order:
    id: str
    type: str
    traveler_id: str
    amount: Decimal
    start: date
    application_id: str
    payment_mode: str = "personal"
    end: date | None = None
    origin: str | None = None
    destination: str | None = None
    cabin: str | None = None
    benchmark_amount: Decimal | None = None
    is_supplement: bool = False
    change_fee: Decimal = ZERO
    refund_fee: Decimal = ZERO
    change_approved: bool = False
    over_standard_status: str = "ok"
    approved_reimburse_amount: Decimal | None = None
    over_standard_reason: str | None = None
    city: str | None = None
    nights: int | None = None
    scope: str | None = None
    external_ref: str | None = None
    already_reimbursed: bool = False
    seat_count: int | None = None


@dataclass
class InvoiceDoc:
    id: str
    number: str
    category: str
    amount: Decimal
    verification: str
    traveler_id: str | None = None
    date: date | None = None
    city: str | None = None
    order_id: str | None = None
    status: str = "available"
    nights: int | None = None


@dataclass
class CalcContext:
    application: Application | None
    orders: list = field(default_factory=list)
    invoices: list = field(default_factory=list)
    other_applications: list = field(default_factory=list)


@dataclass
class Line:
    item: str
    title: str
    gross: Decimal
    claimable: Decimal = ZERO
    self_pay: Decimal = ZERO
    rejected: Decimal = ZERO
    payment_path: str = "none"
    order_id: str | None = None
    reason_code: str | None = None
    reason: str = ""
    clause_codes: list = field(default_factory=list)
    simulated_verification: str | None = None
    scope: str | None = None
    hard_reject: bool = False


ITEM_TITLE = {
    "flight": "机票",
    "train": "火车票",
    "hotel": "住宿",
    "car": "用车",
    "meal_allowance": "伙食补助",
    "local_allowance": "市内交通补助",
    "change_fee": "改签费",
    "refund_fee": "退票费",
}


def _iso(d: date) -> str:
    return d.isoformat()


def _dates(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _nights_of(order: Order) -> int:
    if order.nights is not None:
        return int(order.nights)
    if order.end is None:
        return 0
    return max((order.end - order.start).days, 0)


def _traveler_ids(app: Application) -> set[str]:
    return {t.id for t in app.travelers}


def _overlap(a: Application, b: Application) -> bool:
    return a.start <= b.end and b.start <= a.end


def _gap(a: Application, b: Application) -> int:
    if a.end < b.start:
        return (b.start - a.end).days - 1
    if b.end < a.start:
        return (a.start - b.end).days - 1
    return -1


def _change_pair(a: Application, b: Application) -> bool:
    return a.change_of == b.id or b.change_of == a.id


def _occupied(status: str) -> bool:
    return status in {"pending_dept", "pending_finance", "approved", "change_pending"}


def required_nights(app: Application) -> int:
    if app.trip_type == "onsite" and app.change_approved and app.segments:
        total = 0
        for seg in app.segments:
            if seg.kind == "gap":
                continue
            total += max((seg.end - seg.start).days, 0)
        return total
    return max((app.end - app.start).days, 0)


def onsite_presence_days(app: Application) -> list[date]:
    if app.trip_type == "onsite" and app.change_approved and app.segments:
        days = []
        for seg in app.segments:
            if seg.kind == "gap":
                continue
            days.extend(list(_dates(seg.start, seg.end)))
        return days
    return list(_dates(app.start, app.end))


def find_onsite_conflict(app: Application, others: list[Application]):
    if app.trip_type != "onsite" or not app.project_id or app.policy is None:
        return None
    mine = _traveler_ids(app)
    for other in others:
        if other.id == app.id or other.trip_type != "onsite":
            continue
        if other.project_id != app.project_id or not _occupied(other.status):
            continue
        if mine.isdisjoint(_traveler_ids(other)) or _change_pair(app, other):
            continue
        if app.created_at < other.created_at:
            continue
        if _overlap(app, other):
            return (
                "ONSITE_OVERLAP",
                "同一人同一项目的驻场日期与已有申请重叠，后单拒绝。",
                ["ONSITE-OVERLAP-01"],
            )
        gap = _gap(app, other)
        if 0 <= gap <= app.policy.onsite_split_gap_days:
            return (
                "ONSITE_SPLIT",
                "同一人同一项目的连续驻场被拆成多段申请，后单拒绝。",
                ["ONSITE-SPLIT-01"],
            )
    return None


def missing_fields(app: Application) -> list[str]:
    missing = []
    if not app.travelers:
        missing.append("无出行人")
    if app.start is None or app.end is None:
        missing.append("无日期")
    if app.estimated_budget is None:
        missing.append("无预算")
    if app.travelers and app.headcount != len(app.travelers):
        missing.append("人数与出行人明细不一致")
    if not app.cost_owner_type:
        missing.append("未显式选择费用归属")
    if app.trip_type == "onsite":
        if not app.project_id:
            missing.append("驻场无项目")
        if app.cost_owner_type != "project" or not app.cost_center_code:
            missing.append("费用归属未落到项目或成本中心")
    elif app.trip_type == "client_visit":
        if not app.client_name:
            missing.append("拜访无对象")
        if not app.visit_purpose:
            missing.append("拜访无事由")
        if app.cost_owner_type == "project" and (not app.project_id or not app.cost_center_code):
            missing.append("客户项目或成本中心缺失")
        if app.cost_owner_type == "department" and (not app.department_id or not app.cost_center_code):
            missing.append("未显式选择部门")
        if app.cost_owner_type not in {"project", "department"}:
            missing.append("费用归属无效")
    elif app.trip_type == "training":
        if app.event is None:
            missing.append("培训无会议信息")
    else:
        missing.append("出差类型无效")
    return missing


def _base(app: Application | None, calculated: bool, ok: bool, refusal_code, refusal_reason, clauses, **extra):
    policy_code = app.policy.code if app and app.policy else None
    trip = app.trip_type if app else None
    body = {
        "calculated": calculated,
        "ok": ok,
        "trip_type": trip,
        "trip_type_label": TRIP_LABELS.get(trip, ""),
        "policy_version": policy_code,
        "personal_claimable": None,
        "self_pay": None,
        "allowance": None,
        "company_paid": None,
        "refusal_code": refusal_code,
        "refusal_reason": refusal_reason,
        "clauses": clauses,
        "lines": [],
        "rejected_items": [],
        "warnings": [],
        "budget_exceeded": False,
        "payment_allowed": False,
        "allowance_days": [],
        "allowance_excluded_days": [],
        "excluded": [],
        "simulated_verification_notice": render_clause("COMMON-MOCK-VERIFY-01"),
    }
    body.update(extra)
    return body


def _clause_objs(policy: Policy | None, codes: list[str]):
    seen = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    if policy is None:
        return [{"code": c, "text": render_clause(c)} for c in seen]
    return [{"code": c, "text": policy.text(c)} for c in seen]


def _refuse(app, code, reason, clause_codes, calculated=True):
    policy = app.policy if app else None
    return _base(
        app,
        calculated,
        False,
        code,
        reason,
        _clause_objs(policy, clause_codes),
        personal_claimable="0.00" if calculated else None,
        self_pay="0.00" if calculated else None,
        allowance="0.00" if calculated else None,
        company_paid="0.00" if calculated else None,
    )


def _not_calculated(app, code, reason, clause_codes, excluded=None):
    body = _refuse(app, code, reason, clause_codes, calculated=False)
    body["personal_claimable"] = None
    body["self_pay"] = None
    body["allowance"] = None
    body["company_paid"] = None
    body["excluded"] = excluded or []
    return body


def allowance_plan(app: Application):
    policy = app.policy
    paid = []
    excluded = []
    if app.trip_type == "training":
        event = app.event
        edge = []
        if event.report_date not in edge:
            edge.append(event.report_date)
        if event.return_date not in edge:
            edge.append(event.return_date)
        for d in edge:
            paid.append({"date": d, "meal": True, "local": True, "clause": "TRAIN-EDGE-01"})
        d = event.event_start
        while d <= event.event_end:
            if d not in edge:
                excluded.append(
                    {
                        "date": _iso(d),
                        "reason": "会期不发放伙食补助和市内交通补助",
                        "clause": "TRAIN-EDGE-01",
                    }
                )
            d += timedelta(days=1)
        return paid, excluded

    days = onsite_presence_days(app)
    if app.trip_type == "onsite" and app.change_approved and app.segments:
        for seg in app.segments:
            if seg.kind != "gap":
                continue
            for d in _dates(seg.start, seg.end):
                excluded.append(
                    {
                        "date": _iso(d),
                        "reason": "驻场中途离场，不按在场日发放补助",
                        "clause": "ONSITE-MIDRETURN-01",
                    }
                )
    meal_stop = set()
    local_stop = set()
    stop_clause = None
    if app.trip_type == "onsite" and app.project_provides_meals:
        meal_stop = set(days)
        stop_clause = "ONSITE-PROVIDED-01"
    if app.trip_type == "client_visit":
        meal_stop = set(app.client_meal_dates or [])
        local_stop = set(app.client_transport_dates or [])
        stop_clause = "VISIT-HOST-01"
    standby = set(app.standby_dates or [])
    for d in days:
        if app.trip_type == "onsite" and d.weekday() >= 5 and not policy.onsite_weekend_allowance:
            if not (d in standby and policy.standby_day_allowance):
                excluded.append(
                    {
                        "date": _iso(d),
                        "reason": "周末不发放伙食补助和市内交通补助",
                        "clause": "ONSITE-WEEKEND-01",
                    }
                )
                continue
        meal_ok = d not in meal_stop
        local_ok = d not in local_stop
        item_clause = stop_clause if (not meal_ok or not local_ok) else None
        if not meal_ok:
            excluded.append(
                {
                    "date": _iso(d),
                    "reason": "当日伙食补助停发",
                    "clause": item_clause or "ONSITE-PROVIDED-01",
                }
            )
        if not local_ok:
            excluded.append(
                {
                    "date": _iso(d),
                    "reason": "当日内市内交通补助停发",
                    "clause": item_clause or "VISIT-HOST-01",
                }
            )
        if meal_ok or local_ok:
            paid.append({"date": d, "meal": meal_ok, "local": local_ok, "clause": item_clause})
    return paid, excluded


def _conference_hotel(app: Application, order: Order) -> bool:
    if app.trip_type != "training" or order.type != "hotel" or app.event is None:
        return False
    if not app.event.organizer_provides_lodging:
        return False
    return app.event.event_start <= order.start <= app.event.event_end


def _invoice_problem(app: Application, order: Order, inv: InvoiceDoc | None):
    if inv is None:
        return "MISSING_INVOICE", "缺少与订单对应的票据，该费用不可报。"
    if inv.status != "available":
        return "INVOICE_ALREADY_CLAIMED", "票据已被其他报销单核销。"
    label = VERIFY_LABELS.get(inv.verification, inv.verification)
    if inv.verification != "pass":
        return (
            "MOCK_VERIFICATION_FAILED",
            f"模拟查验：{label}。该费用不可报。",
        )
    if inv.amount != order.amount:
        return (
            "AMOUNT_MISMATCH",
            f"模拟查验：通过。票面金额与订单不符（票据 {money(inv.amount)}，订单 {money(order.amount)}），该费用不可报。",
        )
    if inv.traveler_id not in _traveler_ids(app):
        return "THREE_WAY_MISMATCH", "票据出行人不在申请单范围内。"
    if inv.date is None or not (app.start <= inv.date <= app.end):
        return "THREE_WAY_MISMATCH", "票据日期不在申请单范围内。"
    if inv.city and order.type == "hotel" and inv.city != app.destination:
        return "THREE_WAY_MISMATCH", "住宿票据城市与申请目的地不一致。"
    if order.type in {"flight", "train"} and inv.category not in {order.type, "transport"}:
        return "THREE_WAY_MISMATCH", "票据类别与订单不一致。"
    if order.type == "hotel" and inv.category != "hotel":
        return "THREE_WAY_MISMATCH", "住宿订单缺少住宿票据。"
    return None, ""


def _hotel_cap(policy: Policy, rank: str, tier: str, nights: int):
    row = policy.standard(rank, tier, "hotel")
    if row is None:
        return None
    return D(row["amount"]) * nights


def _price_stay_or_ticket(app: Application, order: Order, traveler: Traveler, tier: str):
    policy = app.policy
    codes = []
    if order.is_supplement:
        codes.append("COMMON-SUPPLEMENT-01")
    if order.type == "car" and order.scope == "intra_city":
        return Line(
            item="car",
            title="市内用车",
            gross=D(order.amount),
            rejected=D(order.amount),
            order_id=order.id,
            reason_code="LOCAL_TRANSPORT_PACKAGED",
            reason="市内交通已按自然日包干，另报市内用车不予支付。",
            clause_codes=["LOCAL-PACKAGE-01"],
            scope=order.scope,
            hard_reject=True,
            payment_path="none",
        )
    if order.type == "hotel" and app.trip_type == "onsite" and app.project_provides_lodging:
        return Line(
            item="hotel",
            title="住宿",
            gross=D(order.amount),
            rejected=D(order.amount),
            order_id=order.id,
            reason_code="LODGING_PROVIDED",
            reason="项目部已提供住宿，住宿费停发。",
            clause_codes=["ONSITE-PROVIDED-01"],
            hard_reject=True,
        )
    if _conference_hotel(app, order):
        return Line(
            item="hotel",
            title="住宿",
            gross=D(order.amount),
            rejected=D(order.amount),
            order_id=order.id,
            reason_code="CONFERENCE_HOTEL_REJECTED",
            reason="举办方已统一安排食宿，会期住宿订单不可报销。",
            clause_codes=["TRAIN-LODGING-01"],
            hard_reject=True,
        )
    if order.type == "hotel" and app.trip_type == "training" and app.event and app.event.organizer_provides_lodging:
        # 报到日夜若落在会期外，统一安排食宿时仍不与会期住宿重复；会期外的夜由限额规则处理。
        pass

    over = False
    cap = None
    if order.type == "hotel":
        nights = _nights_of(order)
        cap = _hotel_cap(policy, traveler.rank, tier, nights)
        if cap is None:
            return "UNDEFINED"
        over = D(order.amount) > cap
        if app.trip_type == "onsite":
            codes.append("ONSITE-HOTEL-01")
        elif app.trip_type == "training":
            codes.append("TRAIN-SELF-01")
    elif order.type in {"flight", "train"}:
        std = policy.standard(traveler.rank, "*", order.type)
        if std is None:
            return "UNDEFINED"
        allowed = std.get("allowed") or []
        if not order.cabin:
            return "DIRTY"
        if order.cabin not in allowed:
            over = True
            if order.benchmark_amount is None and order.over_standard_status != "approved":
                return "BENCHMARK"
            cap = D(order.benchmark_amount) if order.benchmark_amount is not None else None
    else:
        cap = D(order.amount)

    gross = D(order.amount)
    if not over:
        return Line(
            item=order.type,
            title=ITEM_TITLE.get(order.type, order.type),
            gross=gross,
            claimable=gross,
            order_id=order.id,
            payment_path=order.payment_mode,
            clause_codes=codes,
            scope=order.scope,
        )

    codes.append("COMMON-OVER-01")
    if policy.over_standard_action == "block" or order.over_standard_status == "blocked":
        return Line(
            item=order.type,
            title=ITEM_TITLE.get(order.type, order.type),
            gross=gross,
            rejected=gross,
            order_id=order.id,
            reason_code="OVER_STANDARD_BLOCKED",
            reason="超出职级差标，当前制度为拦截，该订单不予入账。",
            clause_codes=codes + ["COMMON-OVER-BLOCK-01"],
            hard_reject=True,
            scope=order.scope,
        )
    if order.over_standard_status == "approved" and order.approved_reimburse_amount is not None:
        claimable = min(gross, D(order.approved_reimburse_amount))
        return Line(
            item=order.type,
            title=ITEM_TITLE.get(order.type, order.type),
            gross=gross,
            claimable=claimable,
            self_pay=gross - claimable,
            order_id=order.id,
            payment_path=order.payment_mode,
            reason_code="OVER_STANDARD_APPROVED" if claimable != gross else None,
            reason="超标审批已通过，按批准口径入账。" if claimable != gross or over else "",
            clause_codes=codes,
            scope=order.scope,
        )
    if cap is None:
        return "BENCHMARK"
    claimable = min(gross, cap)
    return Line(
        item=order.type,
        title=ITEM_TITLE.get(order.type, order.type),
        gross=gross,
        claimable=claimable,
        self_pay=gross - claimable,
        order_id=order.id,
        payment_path=order.payment_mode,
        reason_code="OVER_STANDARD_SELF_PAY",
        reason="超出职级差标，超标部分个人自理。",
        clause_codes=codes,
        scope=order.scope,
    )


def _apply_invoice(app: Application, order: Order, line: Line, inv: InvoiceDoc | None):
    if line.hard_reject:
        if inv is not None:
            line.simulated_verification = inv.verification
        return line
    code, reason = _invoice_problem(app, order, inv)
    if code:
        line.claimable = ZERO
        line.self_pay = ZERO
        line.rejected = line.gross
        line.payment_path = "none"
        line.reason_code = code
        line.reason = reason
        line.hard_reject = True
        line.clause_codes = list(dict.fromkeys(line.clause_codes + ["COMMON-THREEWAY-01", "COMMON-MOCK-VERIFY-01"]))
        if inv is not None:
            line.simulated_verification = inv.verification
        return line
    line.simulated_verification = inv.verification if inv else None
    if inv is not None:
        line.clause_codes = list(dict.fromkeys(line.clause_codes + ["COMMON-MOCK-VERIFY-01"]))
    return line


def _apply_fees(order: Order, line: Line):
    fees = []
    if line.hard_reject:
        return fees
    for kind, amount, title in (
        ("change_fee", D(order.change_fee), "改签费"),
        ("refund_fee", D(order.refund_fee), "退票费"),
    ):
        if amount <= 0:
            continue
        if order.change_approved:
            fees.append(
                Line(
                    item=kind,
                    title=title,
                    gross=amount,
                    claimable=amount,
                    payment_path=order.payment_mode,
                    order_id=order.id,
                    reason="变更已补批，费用可计入。",
                    clause_codes=["COMMON-CHANGE-01"],
                )
            )
        else:
            fees.append(
                Line(
                    item=kind,
                    title=title,
                    gross=amount,
                    self_pay=amount,
                    payment_path="none",
                    order_id=order.id,
                    reason_code="CHANGE_NOT_APPROVED",
                    reason="未经批准的改签或退票费用个人自理。",
                    clause_codes=["COMMON-CHANGE-01"],
                )
            )
    return fees


def _covered_hotel_nights(app: Application, orders: list[Order], invoices_by_order: dict) -> int:
    covered = 0
    for order in orders:
        if order.type != "hotel":
            continue
        if app.project_provides_lodging and app.trip_type == "onsite":
            continue
        if _conference_hotel(app, order):
            continue
        inv = invoices_by_order.get(order.id)
        problem, _ = _invoice_problem(app, order, inv)
        if problem:
            continue
        covered += _nights_of(order)
    return covered


def _should_cascade(app: Application, covered: int, nights: int) -> bool:
    if nights <= 0:
        return False
    if app.trip_type == "training":
        return False
    if app.trip_type == "client_visit" and nights == 0:
        return False
    if app.trip_type == "onsite" and app.project_provides_lodging:
        return False
    if app.trip_type in {"onsite", "client_visit"} and covered < nights:
        return True
    return False


def _serialize_line(line: Line) -> dict:
    return {
        "item": line.item,
        "title": line.title,
        "order_id": line.order_id,
        "claimable": money(line.claimable),
        "self_pay": money(line.self_pay),
        "rejected": money(line.rejected),
        "payment_path": line.payment_path,
        "reason_code": line.reason_code,
        "reason": line.reason,
        "clause_codes": line.clause_codes,
        "simulated_verification": line.simulated_verification,
        "scope": line.scope,
    }


def calculate(ctx: CalcContext) -> dict:
    app = ctx.application
    if app is None:
        return _refuse(None, "UNAUTHORIZED_TRAVEL", "没有出差申请，整单不可报。", ["COMMON-APPROVAL-01"])
    if app.policy is None:
        return _not_calculated(app, "STANDARD_UNDEFINED", "未绑定制度版本，拒绝核定。", ["COMMON-STANDARD-01"])
    if app.status != "approved":
        return _refuse(
            app,
            "APPLICATION_NOT_APPROVED",
            "出差申请尚未审批通过，整单不可报。",
            ["COMMON-APPROVAL-01", "COMMON-VERSION-01"],
        )
    gaps = missing_fields(app)
    if gaps:
        return _not_calculated(
            app,
            "MISSING_REQUIRED_FIELDS",
            "关键字段缺失：" + "、".join(gaps) + "。这些内容不进入核定。",
            ["COMMON-DIRTY-01", "COMMON-VERSION-01"],
            excluded=[{"reasons": gaps}],
        )
    if app.has_unapproved_change and not app.change_approved:
        return _refuse(
            app,
            "CHANGE_NOT_APPROVED",
            "行程变更尚未补批，报销判为异常。不能按变更后的行程核定，也不能按两段完整出差发放补助。",
            ["ONSITE-MIDRETURN-01", "COMMON-CHANGE-01", "COMMON-VERSION-01"],
        )
    if app.trip_type == "training":
        event = app.event
        if (
            app.destination != event.city
            or app.start != event.report_date
            or app.end != event.return_date
            or event.event_start < app.start
            or event.event_end > app.end
        ):
            return _refuse(
                app,
                "TRAINING_MISMATCH",
                "会议通知的地点或日期与申请单不一致，拒绝核定。",
                ["TRAIN-MATCH-01", "TRAIN-EVENT-01", "COMMON-VERSION-01"],
            )

    policy = app.policy
    tier = policy.city_tiers.get(app.destination)
    if not tier:
        return _not_calculated(
            app,
            "STANDARD_UNDEFINED",
            f"城市“{app.destination}”没有城市等级，拒绝核定。",
            ["COMMON-STANDARD-01", "COMMON-VERSION-01"],
        )
    for traveler in app.travelers:
        for item in ("hotel", "meal", "local"):
            if policy.standard(traveler.rank, tier, item) is None:
                return _not_calculated(
                    app,
                    "STANDARD_UNDEFINED",
                    f"未定义差标：职级 {traveler.rank}、城市等级 {tier}、费用 {item}。拒绝核定。",
                    ["COMMON-STANDARD-01", "COMMON-VERSION-01"],
                )

    conflict = find_onsite_conflict(app, ctx.other_applications)
    if conflict:
        code, reason, cls = conflict
        return _refuse(app, code, reason, cls + ["COMMON-VERSION-01"])

    numbers = [inv.number for inv in ctx.invoices]
    if len(numbers) != len(set(numbers)) or any(inv.status != "available" for inv in ctx.invoices):
        return _refuse(
            app,
            "INVOICE_ALREADY_CLAIMED",
            "票据已被使用或在本单中重复出现，后单拒绝。",
            ["COMMON-INVOICE-DUP-01", "COMMON-MOCK-VERIFY-01", "COMMON-VERSION-01"],
        )
    if any(order.already_reimbursed for order in ctx.orders):
        return _refuse(
            app,
            "ORDER_ALREADY_REIMBURSED",
            "同一行程订单已被其他报销单占用，后单拒绝。",
            ["COMMON-INVOICE-DUP-01", "COMMON-VERSION-01"],
        )

    used = ["COMMON-VERSION-01", "COMMON-MOCK-VERIFY-01"]
    if app.trip_type == "onsite":
        used.append("ONSITE-PROJECT-01")
        used.append("ONSITE-WEEKEND-01")
    elif app.trip_type == "client_visit":
        used.extend(["VISIT-REQUIRED-01", "VISIT-COST-01"])
        if required_nights(app) == 0:
            used.append("VISIT-SAMEDAY-01")
    elif app.trip_type == "training":
        used.extend(["TRAIN-EVENT-01", "TRAIN-EDGE-01"])
        if app.event.organizer_provides_lodging:
            used.append("TRAIN-LODGING-01")
        else:
            used.append("TRAIN-SELF-01")

    excluded = []
    usable_orders = []
    for order in ctx.orders:
        reasons = []
        if not order.traveler_id:
            reasons.append("无出行人")
        if order.start is None:
            reasons.append("无日期")
        if order.amount is None:
            reasons.append("无金额")
        if not order.application_id:
            reasons.append("无申请单号")
        if order.type in {"flight", "train"} and not order.cabin:
            reasons.append("无舱位或席别")
        if reasons:
            excluded.append(
                {
                    "kind": "order",
                    "id": order.id,
                    "reasons": reasons,
                    "sample": {
                        "id": order.id,
                        "type": order.type,
                        "amount": None if order.amount is None else money(order.amount),
                    },
                }
            )
            continue
        if order.traveler_id not in _traveler_ids(app):
            usable_orders.append(order)
            continue
        usable_orders.append(order)

    traveler_map = {t.id: t for t in app.travelers}
    invoices_by_order = {}
    for inv in ctx.invoices:
        if inv.order_id and inv.order_id not in invoices_by_order:
            invoices_by_order[inv.order_id] = inv

    lines: list[Line] = []
    undefined = False
    benchmark_missing = False
    for order in usable_orders:
        traveler = traveler_map.get(order.traveler_id)
        if traveler is None:
            lines.append(
                Line(
                    item=order.type,
                    title=ITEM_TITLE.get(order.type, order.type),
                    gross=D(order.amount),
                    rejected=D(order.amount),
                    order_id=order.id,
                    reason_code="THREE_WAY_MISMATCH",
                    reason="订单出行人不在申请单范围内。",
                    clause_codes=["COMMON-THREEWAY-01"],
                    hard_reject=True,
                )
            )
            continue
        if order.start < app.start or order.start > app.end or (order.end and order.end > app.end):
            lines.append(
                Line(
                    item=order.type,
                    title=ITEM_TITLE.get(order.type, order.type),
                    gross=D(order.amount),
                    rejected=D(order.amount),
                    order_id=order.id,
                    reason_code="THREE_WAY_MISMATCH",
                    reason="订单日期不在申请单范围内。",
                    clause_codes=["COMMON-THREEWAY-01"],
                    hard_reject=True,
                )
            )
            continue
        priced = _price_stay_or_ticket(app, order, traveler, tier)
        if priced == "UNDEFINED":
            undefined = True
            break
        if priced == "BENCHMARK":
            benchmark_missing = True
            break
        if priced == "DIRTY":
            excluded.append({"kind": "order", "id": order.id, "reasons": ["无舱位或席别"]})
            continue
        _apply_invoice(app, order, priced, invoices_by_order.get(order.id))
        if order.is_supplement and "COMMON-SUPPLEMENT-01" not in priced.clause_codes:
            priced.clause_codes.append("COMMON-SUPPLEMENT-01")
        lines.append(priced)
        for fee_line in _apply_fees(order, priced):
            lines.append(fee_line)
            used.extend(fee_line.clause_codes)
        used.extend(priced.clause_codes)

    if undefined:
        return _not_calculated(
            app,
            "STANDARD_UNDEFINED",
            "订单对应的舱位或住宿差标未定义，拒绝核定。",
            ["COMMON-STANDARD-01", "COMMON-VERSION-01"],
        )
    if benchmark_missing:
        return _not_calculated(
            app,
            "BENCHMARK_MISSING",
            "超标舱位没有合规舱位对照价格，不能编造可报金额。",
            ["COMMON-OVER-01", "COMMON-STANDARD-01", "COMMON-VERSION-01"],
        )

    nights = required_nights(app)
    covered = _covered_hotel_nights(app, usable_orders, invoices_by_order)
    cascade = _should_cascade(app, covered, nights)
    paid_days, excluded_days = allowance_plan(app)
    meal_total = ZERO
    local_total = ZERO
    for traveler in app.travelers:
        meal_rate = D(policy.standard(traveler.rank, tier, "meal")["amount"])
        local_rate = D(policy.standard(traveler.rank, tier, "local")["amount"])
        for day in paid_days:
            if day["meal"]:
                meal_total += meal_rate
            if day["local"]:
                local_total += local_rate

    weekend_note = (
        f"本版本开关：周末{'发放' if policy.onsite_weekend_allowance else '不发放'}；"
        f"待命日{'发放' if policy.standby_day_allowance else '不发放'}。"
    )
    meal_reason = ""
    local_reason = ""
    meal_codes = []
    local_codes = []
    if app.trip_type == "onsite":
        meal_codes.append("ONSITE-WEEKEND-01")
        local_codes.append("ONSITE-WEEKEND-01")
        meal_reason = weekend_note
        local_reason = weekend_note
        if app.project_provides_meals:
            meal_codes.append("ONSITE-PROVIDED-01")
            meal_reason += "项目部提供用餐，伙食补助停发。"
        if app.change_approved and app.segments:
            meal_codes.append("ONSITE-MIDRETURN-01")
            local_codes.append("ONSITE-MIDRETURN-01")
    if app.trip_type == "client_visit" and (app.client_meal_dates or app.client_transport_dates):
        meal_codes.append("VISIT-HOST-01")
        local_codes.append("VISIT-HOST-01")
        meal_reason = "客户接待用餐或提供交通的日期，对应补助停发。"
    if app.trip_type == "training":
        meal_codes.append("TRAIN-EDGE-01")
        local_codes.append("TRAIN-EDGE-01")
        meal_reason = "只发放报到日和返程日补助，会期不发。"
        if app.event.organizer_provides_lodging:
            meal_reason += "举办方统一安排食宿，不因无住宿发票拒绝往返交通。"
    if app.trip_type == "client_visit" and nights == 0:
        meal_codes.append("VISIT-SAMEDAY-01")
        local_codes.append("VISIT-SAMEDAY-01")
        meal_reason = (meal_reason + " " if meal_reason else "") + "当天往返，无住宿发票不影响当天补助。"

    if cascade:
        clause = "ONSITE-NOHOTEL-01" if app.trip_type == "onsite" else "VISIT-NOHOTEL-01"
        used.append(clause)
        reason = "实际发生过夜住宿，但没有覆盖住宿夜数的有效住宿发票。住宿费、城市间交通费、伙食补助和市内交通补助一并不可发。"
        for line in lines:
            intercity = line.item in {"flight", "train", "hotel"} or (line.item == "car" and line.scope == "intercity")
            fee = line.item in {"change_fee", "refund_fee"}
            if intercity or fee:
                line.rejected = line.gross
                line.claimable = ZERO
                line.self_pay = ZERO
                line.payment_path = "none"
                line.reason_code = "CASCADE_NO_LODGING_INVOICE"
                line.reason = reason
                line.hard_reject = True
                line.clause_codes = list(dict.fromkeys(line.clause_codes + [clause]))
        meal_total = ZERO
        local_total = ZERO
        meal_reason = reason
        local_reason = reason
        meal_codes.append(clause)
        local_codes.append(clause)

    lines.append(
        Line(
            item="meal_allowance",
            title="伙食补助",
            gross=meal_total,
            claimable=meal_total,
            payment_path="personal" if meal_total else "none",
            reason=meal_reason.strip(),
            clause_codes=list(dict.fromkeys(meal_codes)),
        )
    )
    lines.append(
        Line(
            item="local_allowance",
            title="市内交通补助",
            gross=local_total,
            claimable=local_total,
            payment_path="personal" if local_total else "none",
            reason=local_reason.strip(),
            clause_codes=list(dict.fromkeys(local_codes)),
        )
    )
    used.extend(meal_codes)
    used.extend(local_codes)

    personal = ZERO
    self_pay = ZERO
    company = ZERO
    allowance = ZERO
    for line in lines:
        self_pay += line.self_pay
        if line.item in {"meal_allowance", "local_allowance"}:
            allowance += line.claimable
            personal += line.claimable
        elif line.payment_path == "company":
            company += line.claimable
            used.append("COMMON-COMPANY-PAY-01")
        elif line.payment_path == "personal":
            personal += line.claimable

    warnings = []
    budget_exceeded = personal + company > D(app.estimated_budget)
    if budget_exceeded:
        warnings.append("报销与统付合计超出该申请已批预算，须财务明确确认后才能进入支付，不能静默通过。")
        used.append("COMMON-BUDGET-01")

    refusal_code = "CASCADE_NO_LODGING_INVOICE" if cascade else None
    refusal_reason = None
    if cascade:
        refusal_reason = "过夜行程缺少有效住宿发票，交通费和补助一并不可发，个人可报金额为 0。"
    ok = refusal_code is None
    serialized = [_serialize_line(line) for line in lines]
    rejected_items = [
        line for line in serialized if D(line["rejected"]) > 0 or (line["reason_code"] and line["item"] not in {"meal_allowance", "local_allowance"})
    ]
    # 补助停发已体现在 excluded days，不重复塞进拒报金额。
    rejected_items = [line for line in serialized if D(line["rejected"]) > 0 or line["reason_code"] in {"OVER_STANDARD_SELF_PAY", "CHANGE_NOT_APPROVED"}]

    return {
        "calculated": True,
        "ok": ok,
        "trip_type": app.trip_type,
        "trip_type_label": TRIP_LABELS[app.trip_type],
        "policy_version": policy.code,
        "personal_claimable": money(personal),
        "self_pay": money(self_pay),
        "allowance": money(allowance),
        "company_paid": money(company),
        "refusal_code": refusal_code,
        "refusal_reason": refusal_reason,
        "clauses": _clause_objs(policy, used),
        "lines": serialized,
        "rejected_items": rejected_items,
        "warnings": warnings,
        "budget_exceeded": budget_exceeded,
        "payment_allowed": ok and not budget_exceeded,
        "allowance_days": [_iso(d["date"]) for d in paid_days if d["meal"] or d["local"]],
        "allowance_excluded_days": excluded_days,
        "excluded": excluded,
        "excluded_count": len(excluded),
        "simulated_verification_notice": policy.text("COMMON-MOCK-VERIFY-01"),
        "weekend_rule_note": weekend_note if app.trip_type == "onsite" else "",
    }


def validate_order(order: Order, app: Application, spent: Decimal) -> dict:
    policy = app.policy
    if policy is None:
        return _order_result(False, "STANDARD_UNDEFINED", "申请未绑定制度，不能校验订单。", app, None, None)
    if app.status != "approved":
        return _order_result(False, "APPLICATION_NOT_APPROVED", "申请未通过，不能预订或补单。", app, None, None, ["COMMON-APPROVAL-01"])
    if not order.traveler_id or order.start is None or order.amount is None or not order.application_id:
        return _order_result(False, "MISSING_REQUIRED_FIELDS", "订单缺少出行人、日期、金额或申请单号，不进入校验。", app, None, None, ["COMMON-DIRTY-01"])
    if order.traveler_id not in _traveler_ids(app):
        return _order_result(False, "TRAVELER_NOT_ON_APPLICATION", "出行人不在申请单范围内。", app, None, None, ["COMMON-THREEWAY-01"])
    if order.seat_count and order.seat_count > app.headcount:
        return _order_result(False, "HEADCOUNT_EXCEEDED", "订单人数超出申请人数。", app, None, None)
    if order.start < app.start or order.start > app.end or (order.end and order.end > app.end):
        return _order_result(False, "OUTSIDE_APPLICATION", "订单日期不在申请区间内。", app, None, None, ["COMMON-THREEWAY-01"])
    if D(spent) + D(order.amount) > D(app.estimated_budget):
        return _order_result(
            False,
            "BUDGET_EXCEEDED",
            "订单金额加上已占用金额超出申请预算，已拦截。",
            app,
            None,
            None,
            ["COMMON-BUDGET-01"],
        )
    traveler = next(t for t in app.travelers if t.id == order.traveler_id)
    tier = policy.city_tiers.get(app.destination)
    if tier is None:
        return _order_result(False, "STANDARD_UNDEFINED", "目的地没有城市等级。", app, None, None, ["COMMON-STANDARD-01"])
    if _conference_hotel(app, order):
        return _order_result(
            False,
            "CONFERENCE_HOTEL_REJECTED",
            "举办方统一安排食宿，会期住宿订单被拒绝。",
            app,
            ZERO,
            D(order.amount),
            ["TRAIN-LODGING-01"],
        )
    if order.type == "hotel" and app.trip_type == "onsite" and app.project_provides_lodging:
        return _order_result(
            False,
            "LODGING_PROVIDED",
            "项目部已安排住宿，不能再记入可报销住宿订单。",
            app,
            ZERO,
            D(order.amount),
            ["ONSITE-PROVIDED-01"],
        )
    priced = _price_stay_or_ticket(app, order, traveler, tier)
    if priced == "UNDEFINED":
        return _order_result(False, "STANDARD_UNDEFINED", "未定义对应差标，拒绝预订。", app, None, None, ["COMMON-STANDARD-01"])
    if priced == "DIRTY":
        return _order_result(False, "MISSING_REQUIRED_FIELDS", "缺少舱位或席别，订单不进入校验。", app, None, None, ["COMMON-DIRTY-01"])
    if priced == "BENCHMARK":
        return _order_result(
            False,
            "BENCHMARK_MISSING",
            "超标舱位没有合规舱位对照价格，不能编造可报金额。",
            app,
            None,
            None,
            ["COMMON-OVER-01", "COMMON-STANDARD-01"],
        )
    codes = list(priced.clause_codes)
    if order.is_supplement and "COMMON-SUPPLEMENT-01" not in codes:
        codes.append("COMMON-SUPPLEMENT-01")
    if priced.reason_code == "OVER_STANDARD_BLOCKED":
        return _order_result(False, "OVER_STANDARD_BLOCKED", priced.reason, app, ZERO, priced.rejected, codes)
    if priced.reason_code == "LOCAL_TRANSPORT_PACKAGED":
        return _order_result(False, "LOCAL_TRANSPORT_PACKAGED", priced.reason, app, ZERO, priced.rejected, codes)
    if priced.reason_code == "OVER_STANDARD_SELF_PAY" or (
        priced.reason_code == "OVER_STANDARD_APPROVED"
    ):
        if priced.reason_code == "OVER_STANDARD_SELF_PAY" and not (order.over_standard_reason or "").strip():
            if policy.over_standard_action == "submit_approval" and order.over_standard_status != "approved":
                return _order_result(
                    False,
                    "OVER_STANDARD_REASON_REQUIRED",
                    "超标订单必须填写超标原因后才能提交超标审批。",
                    app,
                    priced.claimable,
                    priced.self_pay,
                    codes,
                )
        return _order_result(
            True,
            priced.reason_code,
            priced.reason,
            app,
            priced.claimable,
            priced.self_pay,
            codes,
            over_standard=True,
            pending=order.over_standard_status != "approved",
        )
    return _order_result(True, None, "未超出差标。", app, priced.claimable, ZERO, codes, over_standard=False, pending=False)


def _order_result(ok, code, message, app, compliant, self_pay, clauses=None, over_standard=False, pending=False):
    policy = app.policy
    codes = clauses or []
    return {
        "ok": ok,
        "error_code": code,
        "message": message,
        "trip_type": app.trip_type,
        "policy_version": policy.code if policy else None,
        "over_standard": over_standard,
        "pending_approval": pending,
        "compliant_amount": None if compliant is None else money(compliant),
        "self_pay": None if self_pay is None else money(self_pay),
        "clauses": _clause_objs(policy, codes) if policy else _clause_objs(None, codes),
    }


def classify_import_rows(rows: list[dict], existing_keys: set[str], key_fields: list[str]) -> dict:
    seen = set(existing_keys)
    accepted = []
    duplicate = 0
    dirty = []
    for index, row in enumerate(rows):
        reasons = []
        if not row.get("traveler"):
            reasons.append("无出行人")
        if not row.get("date"):
            reasons.append("无日期")
        if row.get("amount") in (None, ""):
            reasons.append("无金额")
        if not row.get("application_code"):
            reasons.append("无申请单号")
        trip = row.get("trip_type")
        if trip == "onsite" and not row.get("project_code"):
            reasons.append("驻场无项目")
        if trip == "client_visit" and not row.get("client_name"):
            reasons.append("拜访无对象")
        if trip == "training" and not row.get("event_name"):
            reasons.append("培训无会议信息")
        if reasons:
            dirty.append(
                {
                    "index": index,
                    "reasons": reasons,
                    "sample": {key: row.get(key) for key in ("traveler", "date", "amount", "application_code", "type")},
                }
            )
            continue
        key = "|".join(str(row.get(field, "")) for field in key_fields)
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        accepted.append(row)
    return {
        "accepted_count": len(accepted),
        "duplicate_count": duplicate,
        "dirty_count": len(dirty),
        "dirty_samples": dirty[:5],
        "accepted": accepted,
    }


def aggregate_costs(rows: list[dict], *, start: date, end: date, group_by: str, known_departments: set[str], known_projects: set[str], department_id: str | None = None, project_id: str | None = None):
    if start is None or end is None or start > end:
        raise BusinessError("PERIOD_INVALID", "统计区间无效，拒绝汇总。")
    if group_by not in {"department", "project", "trip_type"}:
        raise BusinessError("ATTRIBUTION_UNMATCHED", "未知的归集维度，拒绝汇总。")
    if department_id and department_id not in known_departments:
        raise BusinessError("ATTRIBUTION_UNMATCHED", "部门费用归属无法匹配，拒绝给出汇总金额。")
    if project_id and project_id not in known_projects:
        raise BusinessError("ATTRIBUTION_UNMATCHED", "项目或成本中心无法匹配，拒绝给出汇总金额。")
    buckets = {}
    total = ZERO
    for row in rows:
        if row["date"] < start or row["date"] > end:
            continue
        if department_id and row.get("department_id") != department_id:
            continue
        if project_id and row.get("project_id") != project_id:
            continue
        if group_by == "department":
            key = row.get("department_id") or ""
            if key not in known_departments:
                raise BusinessError("ATTRIBUTION_UNMATCHED", "存在无法匹配部门的费用，拒绝汇总。")
        elif group_by == "project":
            key = row.get("project_id") or ""
            if row.get("cost_owner_type") == "project" and key not in known_projects:
                raise BusinessError("ATTRIBUTION_UNMATCHED", "存在无法匹配项目的费用，拒绝汇总。")
        else:
            key = row.get("trip_type") or ""
            if key not in TRIP_LABELS:
                raise BusinessError("ATTRIBUTION_UNMATCHED", "存在无法识别出差类型的费用，拒绝汇总。")
        amount = D(row["amount"])
        slot = buckets.setdefault(key, {"key": key, "amount": ZERO, "personal": ZERO, "company": ZERO, "ids": []})
        slot["amount"] += amount
        if row.get("source") == "company":
            slot["company"] += amount
        else:
            slot["personal"] += amount
        if row.get("reimbursement_id"):
            slot["ids"].append(row["reimbursement_id"])
        total += amount
    out = []
    for slot in buckets.values():
        out.append(
            {
                "key": slot["key"],
                "amount": money(slot["amount"]),
                "personal": money(slot["personal"]),
                "company": money(slot["company"]),
                "reimbursement_ids": slot["ids"],
            }
        )
    return {"group_by": group_by, "total": money(total), "buckets": out}


def explain_result(result: dict, proposed_amount=None) -> dict:
    """只转述已经算出的结果。传入金额一律拒绝，避免绕过规则引擎。"""
    from app.metrics import metrics

    if proposed_amount is not None:
        metrics.incr_model_failure()
        return {
            "ok": False,
            "error_code": "MODEL_CANNOT_SET_AMOUNT",
            "message": "报销金额只能由规则引擎计算。说明环节不能接收或改写金额。",
            "personal_claimable": None,
            "text": "",
        }
    parts = [
        f"出差类型：{result.get('trip_type_label') or result.get('trip_type') or '未识别'}。",
        f"制度版本：{result.get('policy_version') or '无'}。",
    ]
    if result.get("calculated") and result.get("personal_claimable") is not None:
        parts.append(
            f"规则引擎核定个人可报 {result['personal_claimable']} 元，自理 {result['self_pay']} 元，"
            f"补助 {result['allowance']} 元，公司统付 {result['company_paid']} 元。"
        )
    else:
        parts.append("规则引擎未给出可报金额。")
    if result.get("refusal_reason"):
        parts.append(f"拒报说明：{result['refusal_reason']}")
    if result.get("warnings"):
        parts.append("提示：" + "；".join(result["warnings"]))
    parts.append("依据条款：")
    for clause in result.get("clauses") or []:
        parts.append(f"{clause['code']} {clause['text']}")
    parts.append(result.get("simulated_verification_notice") or render_clause("COMMON-MOCK-VERIFY-01"))
    parts.append("本说明只转述规则引擎已经算出的结果，不重新计算金额，也不新增差标。")
    return {
        "ok": True,
        "error_code": None,
        "message": "",
        "personal_claimable": result.get("personal_claimable"),
        "text": "\n".join(parts),
    }
