from datetime import date

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import issue_token, read_token, verify_password
from app.config import SAMPLE_DIR
from app.db import get_db
from app.errors import BusinessError
from app.evaluations import list_cases, run_all
from app.metrics import metrics
from app.models import (
    Application,
    ApplicationTraveler,
    ApprovalTask,
    AuditLog,
    Department,
    Event,
    Invoice,
    Order,
    PolicyVersion,
    Project,
    Reimbursement,
    User,
)
from app.money import money
from app.services import (
    can_view_application,
    create_application,
    create_change,
    create_invoice,
    create_order,
    create_policy_version,
    decide_task,
    explain_stored,
    import_orders,
    preview_reimbursement,
    reconcile,
    require,
    set_verification,
    submit_application,
    submit_reimbursement,
    traveler_rows,
)

router = APIRouter(prefix="/api")


class LoginIn(BaseModel):
    username: str
    password: str


class ApplicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trip_type: str
    traveler_ids: list[str]
    reason: str
    origin: str
    destination: str
    start_date: date
    end_date: date
    transport_mode: str = "train"
    estimated_budget: str
    cost_owner_type: str
    department_id: str | None = None
    project_id: str | None = None
    client_name: str | None = None
    visit_purpose: str | None = None
    event_id: str | None = None
    project_provides_lodging: bool = False
    project_provides_meals: bool = False
    client_meal_dates: list[date] = []
    client_transport_dates: list[date] = []
    scenario: str = ""


class ChangeIn(BaseModel):
    reason: str
    start_date: date | None = None
    end_date: date | None = None
    destination: str | None = None
    origin: str | None = None
    segments: list[dict] | None = None
    estimated_budget: str | None = None


class DecisionIn(BaseModel):
    approve: bool
    comment: str = ""
    acknowledge_budget: bool = False
    approved_amount: str | None = None


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: str
    traveler_id: str
    type: str
    amount: str
    start_date: date
    end_date: date | None = None
    origin: str | None = None
    destination: str | None = None
    city: str | None = None
    cabin: str | None = None
    benchmark_amount: str | None = None
    nights: int | None = None
    scope: str | None = None
    payment_mode: str = "personal"
    is_supplement: bool = False
    external_ref: str | None = None
    change_fee: str = "0"
    refund_fee: str = "0"
    change_approved: bool = False
    over_standard_reason: str | None = None
    seat_count: int | None = None


class ImportIn(BaseModel):
    rows: list[dict]


class InvoiceIn(BaseModel):
    number: str
    category: str
    amount: str
    order_id: str | None = None
    traveler_id: str | None = None
    invoice_date: date | None = None
    city: str | None = None
    nights: int | None = None
    verification_result: str = "pass"
    verification_message: str = "模拟查验"


class VerifyIn(BaseModel):
    result: str
    message: str = "模拟查验"


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: str
    hypothetical_policy_version_id: str | None = None


class ExplainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposed_amount: str | None = None


class PolicyIn(BaseModel):
    note: str = ""
    onsite_weekend_allowance: bool | None = None
    onsite_split_gap_days: int | None = None
    over_standard_action: str | None = None
    standards: list[dict] = []
    activate: bool = True


class EventIn(BaseModel):
    name: str
    organizer: str
    city: str
    report_date: date
    event_start: date
    event_end: date
    return_date: date
    organizer_provides_lodging: bool = False
    organizer_provides_meals: bool = False
    organizer_provides_local: bool = False


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise BusinessError("AUTH_REQUIRED", "请先登录。", 401)
    user_id = read_token(authorization[7:])
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise BusinessError("AUTH_REQUIRED", "登录已失效。", 401)
    return user


def _num(value):
    return None if value is None else money(value)


def _user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "rank": user.rank,
        "department_id": user.department_id,
    }


def _application(db: Session, row: Application) -> dict:
    travelers = traveler_rows(db, row.id)
    return {
        "id": row.id,
        "code": row.code,
        "trip_type": row.trip_type,
        "scenario": row.scenario,
        "status": row.status,
        "reason": row.reason,
        "origin": row.origin,
        "destination": row.destination,
        "start_date": row.start_date.isoformat(),
        "end_date": row.end_date.isoformat(),
        "headcount": row.headcount,
        "transport_mode": row.transport_mode,
        "estimated_budget": _num(row.estimated_budget),
        "consumed_budget": _num(row.consumed_budget),
        "cost_owner_type": row.cost_owner_type,
        "department_id": row.department_id,
        "project_id": row.project_id,
        "cost_center_code": row.cost_center_code,
        "client_name": row.client_name,
        "visit_purpose": row.visit_purpose,
        "event_id": row.event_id,
        "project_provides_lodging": row.project_provides_lodging,
        "project_provides_meals": row.project_provides_meals,
        "policy_version_id": row.policy_version_id,
        "parent_id": row.parent_id,
        "rejection_code": row.rejection_code,
        "rejection_reason": row.rejection_reason,
        "applicant_id": row.applicant_id,
        "travelers": [_user(item) for item in travelers],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _order(row: Order) -> dict:
    return {
        "id": row.id,
        "application_id": row.application_id,
        "traveler_id": row.traveler_id,
        "type": row.type,
        "payment_mode": row.payment_mode,
        "amount": _num(row.amount),
        "start_date": row.start_date.isoformat() if row.start_date else None,
        "end_date": row.end_date.isoformat() if row.end_date else None,
        "cabin": row.cabin,
        "benchmark_amount": _num(row.benchmark_amount),
        "nights": row.nights,
        "is_supplement": row.is_supplement,
        "change_fee": _num(row.change_fee),
        "change_approved": row.change_approved,
        "over_standard_status": row.over_standard_status,
        "over_standard_reason": row.over_standard_reason,
        "approved_reimburse_amount": _num(row.approved_reimburse_amount),
        "compliance_status": row.compliance_status,
        "compliance_code": row.compliance_code,
        "compliance_message": row.compliance_message,
        "policy_version_id": row.policy_version_id,
        "reimbursement_id": row.reimbursement_id,
        "external_ref": row.external_ref,
    }


def _reimbursement(row: Reimbursement) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "application_id": row.application_id,
        "status": row.status,
        "policy_version_id": row.policy_version_id,
        "personal_claimable": _num(row.personal_claimable),
        "self_pay": _num(row.self_pay),
        "allowance": _num(row.allowance),
        "company_paid": _num(row.company_paid),
        "budget_exceeded": row.budget_exceeded,
        "result": row.result_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@router.get("/health")
def health():
    return {"status": "ok", "service": "travel-compliance"}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(body.password, user.password_salt, user.password_hash):
        raise BusinessError("AUTH_FAILED", "账号或口令不正确。演示口令见登录页说明。", 401)
    return {"token": issue_token(user.id), "user": _user(user), "demo_only": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _user(user)


@router.get("/meta")
def meta(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return {
        "user": _user(user),
        "departments": [
            {"id": item.id, "name": item.name, "cost_center_code": item.cost_center_code}
            for item in db.scalars(select(Department))
        ],
        "projects": [
            {"id": item.id, "name": item.name, "cost_center_code": item.cost_center_code, "department_id": item.department_id}
            for item in db.scalars(select(Project))
        ],
        "events": [
            {
                "id": item.id,
                "name": item.name,
                "organizer": item.organizer,
                "city": item.city,
                "report_date": item.report_date.isoformat(),
                "event_start": item.event_start.isoformat(),
                "event_end": item.event_end.isoformat(),
                "return_date": item.return_date.isoformat(),
                "organizer_provides_lodging": item.organizer_provides_lodging,
                "organizer_provides_meals": item.organizer_provides_meals,
                "organizer_provides_local": item.organizer_provides_local,
            }
            for item in db.scalars(select(Event))
        ],
        "users": [_user(item) for item in db.scalars(select(User))],
        "cities": ["上海", "杭州", "成都", "江城"],
        "trip_types": [
            {"id": "onsite", "label": "项目驻场"},
            {"id": "client_visit", "label": "客户拜访"},
            {"id": "training", "label": "培训开会"},
        ],
    }


@router.get("/policies")
def policies(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.scalars(select(PolicyVersion).order_by(PolicyVersion.created_at)).all()
    return [
        {
            "id": item.id,
            "code": item.code,
            "status": item.status,
            "note": item.note,
            "over_standard_action": item.over_standard_action,
            "onsite_weekend_allowance": item.onsite_weekend_allowance,
            "onsite_split_gap_days": item.onsite_split_gap_days,
            "standby_day_allowance": item.standby_day_allowance,
        }
        for item in rows
    ]


@router.get("/policies/{policy_id}")
def policy_detail(policy_id: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    row = db.get(PolicyVersion, policy_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "制度版本不存在。", 404)
    from app.services import to_policy

    view = to_policy(db, row)
    standards = []
    for (rank, tier, item), payload in sorted(view.standards.items(), key=lambda pair: (pair[0][2], pair[0][0], pair[0][1])):
        standards.append(
            {
                "rank": rank,
                "city_tier": tier,
                "item": item,
                "amount": money(payload["amount"]) if "amount" in payload else None,
                "allowed": payload.get("allowed"),
            }
        )
    return {
        "id": row.id,
        "code": row.code,
        "status": row.status,
        "note": row.note,
        "over_standard_action": row.over_standard_action,
        "onsite_weekend_allowance": row.onsite_weekend_allowance,
        "onsite_split_gap_days": row.onsite_split_gap_days,
        "cities": view.city_tiers,
        "standards": standards,
        "clauses": [{"code": code, "text": text} for code, text in view.clauses.items()],
    }


@router.post("/policies")
def publish_policy(body: PolicyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = create_policy_version(db, user, body.model_dump())
    return {"id": row.id, "code": row.code, "status": row.status}


@router.get("/applications")
def applications(trip_type: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Application).order_by(Application.created_at)).all()
    visible = []
    for row in rows:
        if trip_type and row.trip_type != trip_type:
            continue
        try:
            can_view_application(user, row, [item.id for item in traveler_rows(db, row.id)])
        except BusinessError:
            continue
        visible.append(_application(db, row))
    return visible


@router.post("/applications")
def post_application(body: ApplicationIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    data = body.model_dump()
    data["start_date"] = body.start_date.isoformat()
    data["end_date"] = body.end_date.isoformat()
    data["client_meal_dates"] = [item.isoformat() for item in body.client_meal_dates]
    data["client_transport_dates"] = [item.isoformat() for item in body.client_transport_dates]
    row = create_application(db, user, data)
    return _application(db, row)


@router.get("/applications/{application_id}")
def application_detail(application_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "申请不存在。", 404)
    people = traveler_rows(db, row.id)
    can_view_application(user, row, [item.id for item in people])
    orders = db.scalars(select(Order).where(Order.application_id == row.id)).all()
    reimbursements = db.scalars(select(Reimbursement).where(Reimbursement.application_id == row.id)).all()
    invoice_rows = []
    if orders:
        invoice_rows = db.scalars(select(Invoice).where(Invoice.order_id.in_([item.id for item in orders]))).all()
    return {
        "application": _application(db, row),
        "orders": [_order(item) for item in orders],
        "invoices": [
            {
                "id": item.id,
                "number": item.number,
                "category": item.category,
                "amount": _num(item.amount),
                "verification_result": item.verification_result,
                "verification_message": item.verification_message,
                "simulated": item.simulated,
                "status": item.status,
                "order_id": item.order_id,
                "reimbursement_id": item.reimbursement_id,
            }
            for item in invoice_rows
        ],
        "reimbursements": [_reimbursement(item) for item in reimbursements],
    }


@router.post("/applications/{application_id}/submit")
def post_submit(application_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = submit_application(db, user, application_id)
    return _application(db, row)


@router.post("/applications/{application_id}/changes")
def post_change(application_id: str, body: ChangeIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = create_change(db, user, application_id, body.model_dump())
    return _application(db, row)


@router.get("/approvals")
def approvals(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role not in {"dept_approver", "finance", "admin"}:
        raise BusinessError("ROLE_FORBIDDEN", "当前角色没有审批待办。", 403)
    rows = db.scalars(select(ApprovalTask).where(ApprovalTask.status == "pending").order_by(ApprovalTask.sequence)).all()
    result = []
    for row in rows:
        if user.role == "admin":
            pass
        elif row.assignee_role != user.role:
            continue
        elif user.role == "dept_approver" and row.department_id and row.department_id != user.department_id:
            continue
        result.append(
            {
                "id": row.id,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "step": row.step,
                "sequence": row.sequence,
                "assignee_role": row.assignee_role,
                "department_id": row.department_id,
                "status": row.status,
            }
        )
    return result


@router.post("/approvals/{task_id}/decision")
def post_decision(task_id: str, body: DecisionIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    task = decide_task(
        db,
        user,
        task_id,
        body.approve,
        body.comment,
        body.acknowledge_budget,
        body.approved_amount,
    )
    return {"id": task.id, "status": task.status, "step": task.step}


@router.get("/orders")
def orders(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Order).order_by(Order.created_at)).all()
    visible = []
    for row in rows:
        application = db.get(Application, row.application_id)
        try:
            can_view_application(user, application, [item.user_id for item in db.scalars(select(ApplicationTraveler).where(ApplicationTraveler.application_id == application.id))])
        except BusinessError:
            continue
        visible.append(_order(row))
    return visible


@router.post("/orders")
def post_order(body: OrderIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = create_order(db, user, body.model_dump(mode="json"))
    return _order(row)


@router.post("/orders/import")
def post_import(body: ImportIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return import_orders(db, user, body.rows)


@router.get("/samples/order-import")
def sample_import(_: User = Depends(current_user)):
    path = SAMPLE_DIR / "order_import.json"
    if not path.exists():
        raise BusinessError("NOT_FOUND", "示例导入文件不存在。", 404)
    return FileResponse(path, media_type="application/json")


@router.post("/invoices")
def post_invoice(body: InvoiceIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = create_invoice(db, user, body.model_dump(mode="json"))
    return {"id": row.id, "number": row.number, "verification_result": row.verification_result, "simulated": True}


@router.post("/invoices/{invoice_id}/verification")
def post_verification(invoice_id: str, body: VerifyIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = set_verification(db, user, invoice_id, body.result, body.message)
    return {
        "id": row.id,
        "verification_result": row.verification_result,
        "verification_message": row.verification_message,
        "simulated": True,
        "notice": "这是演示环境注入的模拟查验，未对接税务机关。",
    }


@router.get("/reimbursements")
def reimbursements(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Reimbursement).order_by(Reimbursement.created_at)).all()
    visible = []
    for row in rows:
        application = db.get(Application, row.application_id)
        try:
            can_view_application(user, application, [item.id for item in traveler_rows(db, application.id)])
        except BusinessError:
            continue
        visible.append(_reimbursement(row))
    return visible


@router.get("/reimbursements/{reimbursement_id}")
def reimbursement_detail(reimbursement_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Reimbursement, reimbursement_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "报销单不存在。", 404)
    application = db.get(Application, row.application_id)
    can_view_application(user, application, [item.id for item in traveler_rows(db, application.id)])
    return _reimbursement(row)


@router.post("/reimbursements/preview")
def post_preview(body: PreviewIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return preview_reimbursement(db, user, body.application_id, body.hypothetical_policy_version_id)


@router.post("/reimbursements")
def post_reimbursement(body: PreviewIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if body.hypothetical_policy_version_id:
        raise BusinessError("MODEL_CANNOT_SET_AMOUNT", "正式报销只能使用申请绑定的制度版本。")
    row = submit_reimbursement(db, user, body.application_id)
    return _reimbursement(row)


@router.post("/reimbursements/{reimbursement_id}/explain")
def post_explain(reimbursement_id: str, body: ExplainIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return explain_stored(db, user, reimbursement_id, body.proposed_amount)


@router.get("/reconcile")
def get_reconcile(
    group_by: str = "trip_type",
    start: date = date(2026, 1, 1),
    end: date = date(2026, 12, 31),
    department_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    return reconcile(db, user, group_by, start, end, department_id, project_id)


@router.get("/failures")
def failures(db: Session = Depends(get_db), user: User = Depends(current_user)):
    apps = []
    for row in db.scalars(select(Application).where(Application.status == "rejected")):
        try:
            can_view_application(user, row, [item.id for item in traveler_rows(db, row.id)])
        except BusinessError:
            continue
        apps.append({"id": row.id, "code": row.code, "trip_type": row.trip_type, "code_name": row.rejection_code, "reason": row.rejection_reason, "scenario": row.scenario})
    orders = []
    for row in db.scalars(select(Order).where(Order.compliance_status == "rejected")):
        application = db.get(Application, row.application_id)
        try:
            can_view_application(user, application, [item.id for item in traveler_rows(db, application.id)])
        except BusinessError:
            continue
        orders.append({"id": row.id, "application_id": row.application_id, "type": row.type, "code": row.compliance_code, "reason": row.compliance_message})
    claims = []
    for row in db.scalars(select(Reimbursement)):
        result = row.result_json or {}
        rejected = row.status == "rejected" or result.get("refusal_code") or result.get("rejected_items")
        if not rejected:
            continue
        application = db.get(Application, row.application_id)
        try:
            can_view_application(user, application, [item.id for item in traveler_rows(db, application.id)])
        except BusinessError:
            continue
        claims.append(
            {
                "id": row.id,
                "code": row.code,
                "application_id": row.application_id,
                "trip_type": application.trip_type,
                "status": row.status,
                "refusal_code": result.get("refusal_code"),
                "refusal_reason": result.get("refusal_reason"),
                "rejected_items": result.get("rejected_items") or [],
            }
        )
    return {"applications": apps, "orders": orders, "reimbursements": claims}


@router.get("/audit")
def audit_logs(db: Session = Depends(get_db), user: User = Depends(current_user)):
    require(user, {"admin"})
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at.desc())).all()
    return [
        {
            "id": item.id,
            "actor_id": item.actor_id,
            "actor_name": item.actor_name,
            "at": item.at.isoformat() if item.at else None,
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "action": item.action,
            "policy_version": item.policy_version,
            "success": item.success,
            "input_summary": item.input_summary,
            "conclusion_summary": item.conclusion_summary,
        }
        for item in rows[:300]
    ]


@router.get("/admin/metrics")
def admin_metrics(user: User = Depends(current_user)):
    require(user, {"admin"})
    return metrics.snapshot()


@router.get("/admin/evaluations/cases")
def evaluation_cases(user: User = Depends(current_user)):
    require(user, {"admin"})
    return list_cases()


@router.post("/admin/evaluations/run")
def evaluation_run(user: User = Depends(current_user)):
    require(user, {"admin"})
    return run_all()


@router.post("/admin/invoice-race")
def invoice_race(user: User = Depends(current_user)):
    require(user, {"admin"})
    from app.race_demo import run_invoice_race

    return run_invoice_race()


@router.post("/events")
def post_event(body: EventIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require(user, {"admin"})
    from app.services import new_id

    row = Event(id=new_id("evt"), created_by=user.id, **body.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}
