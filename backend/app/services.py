"""申请、审批、订单、报销。金额计算只调用规则引擎。"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.config import COMPANY_NAME, COMPANY_TAX_NO
from app.engine import (
    Application as EngineApp,
    CalcContext,
    EventInfo,
    InvoiceDoc,
    Order as EngineOrder,
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
from app.metrics import metrics
from app.models import (
    Application,
    ApplicationTraveler,
    ApprovalTask,
    AuditLog,
    BudgetPool,
    Department,
    Event,
    Invoice,
    OnsiteHold,
    OnsiteMutex,
    Order,
    PolicyCity,
    PolicyClause,
    PolicyStandard,
    PolicyVersion,
    Project,
    Reimbursement,
    User,
)
from app.money import D, money

PENDING = {"pending_dept", "pending_finance", "change_pending"}
ACTIVE_REIMBURSE = {"processing", "pending_dept", "pending_finance", "approved"}
VERIFY_RESULTS = {"pass", "not_found", "already_used", "header_mismatch", "amount_mismatch"}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def audit(db, actor, entity_type, entity_id, action, policy_version, success, inputs, conclusion):
    name = getattr(actor, "display_name", "") if actor else ""
    db.add(
        AuditLog(
            id=new_id("aud"),
            actor_id=getattr(actor, "id", None) if actor else None,
            actor_name=name,
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            policy_version=policy_version,
            success=success,
            input_summary=str(inputs)[:2000],
            conclusion_summary=str(conclusion)[:2000],
        )
    )


def require(user: User, roles: set[str]):
    if user.role not in roles:
        raise BusinessError("ROLE_FORBIDDEN", f"当前角色不能执行此操作（{user.role}）。", 403)


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def stamp() -> datetime:
    return datetime.now(timezone.utc)


def _naive(value: datetime | None) -> datetime:
    if value is None:
        return datetime(2026, 1, 1)
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def active_policy(db) -> PolicyVersion:
    row = db.scalar(select(PolicyVersion).where(PolicyVersion.status == "active"))
    if row is None:
        raise BusinessError("STANDARD_UNDEFINED", "没有生效中的差旅制度，拒绝继续。")
    return row


def policy_by_id(db, policy_id: str) -> PolicyVersion:
    row = db.get(PolicyVersion, policy_id)
    if row is None:
        raise BusinessError("STANDARD_UNDEFINED", "制度版本不存在。", 404)
    return row


def to_policy(db, row: PolicyVersion) -> Policy:
    standards = {}
    for item in db.scalars(select(PolicyStandard).where(PolicyStandard.policy_version_id == row.id)):
        if item.allowed:
            standards[(item.rank, item.city_tier, item.item)] = {"allowed": list(item.allowed)}
        else:
            standards[(item.rank, item.city_tier, item.item)] = {"amount": D(item.amount)}
    cities = {
        item.city: item.tier
        for item in db.scalars(select(PolicyCity).where(PolicyCity.policy_version_id == row.id))
    }
    clauses = {
        item.code: item.text
        for item in db.scalars(select(PolicyClause).where(PolicyClause.policy_version_id == row.id))
    }
    return Policy(
        code=row.code,
        over_standard_action=row.over_standard_action,
        onsite_weekend_allowance=row.onsite_weekend_allowance,
        onsite_split_gap_days=row.onsite_split_gap_days,
        standby_day_allowance=row.standby_day_allowance,
        standards=standards,
        city_tiers=cities,
        clauses=clauses,
        dedup_fields=list(row.dedup_fields or []),
    )


def _event(db, event_id: str | None):
    if not event_id:
        return None
    row = db.get(Event, event_id)
    if row is None:
        return None
    return EventInfo(
        id=row.id,
        name=row.name,
        organizer=row.organizer,
        city=row.city,
        report_date=as_date(row.report_date),
        event_start=as_date(row.event_start),
        event_end=as_date(row.event_end),
        return_date=as_date(row.return_date),
        organizer_provides_lodging=row.organizer_provides_lodging,
        organizer_provides_meals=row.organizer_provides_meals,
        organizer_provides_local=row.organizer_provides_local,
    )


def _segments(raw):
    if not raw:
        return None
    return [
        Segment(
            start=as_date(item["start"]),
            end=as_date(item["end"]),
            origin=item.get("origin") or "",
            destination=item.get("destination") or "",
            kind=item.get("kind") or "onsite",
        )
        for item in raw
    ]


def traveler_rows(db, application_id: str) -> list[User]:
    ids = db.scalars(
        select(ApplicationTraveler.user_id).where(ApplicationTraveler.application_id == application_id)
    ).all()
    return [db.get(User, item) for item in ids]


def to_application(db, row: Application, policy: Policy | None = None) -> EngineApp:
    users = traveler_rows(db, row.id)
    pending_child = db.scalar(
        select(Application.id).where(
            Application.parent_id == row.id,
            Application.status.in_(tuple(PENDING | {"pending_dept", "pending_finance"})),
        )
    )
    project_name = db.get(Project, row.project_id).name if row.project_id else None
    department_name = db.get(Department, row.department_id).name if row.department_id else None
    return EngineApp(
        id=row.id,
        code=row.code,
        status=row.status,
        trip_type=row.trip_type,
        travelers=[Traveler(user.id, user.display_name, user.rank) for user in users],
        reason=row.reason,
        origin=row.origin,
        destination=row.destination,
        start=as_date(row.start_date),
        end=as_date(row.end_date),
        headcount=row.headcount,
        estimated_budget=D(row.estimated_budget),
        project_id=row.project_id,
        project_name=project_name,
        department_id=row.department_id,
        department_name=department_name,
        cost_owner_type=row.cost_owner_type,
        cost_center_code=row.cost_center_code,
        client_name=row.client_name,
        visit_purpose=row.visit_purpose,
        event=_event(db, row.event_id),
        project_provides_lodging=row.project_provides_lodging,
        project_provides_meals=row.project_provides_meals,
        client_meal_dates=[as_date(item) for item in (row.client_meal_dates or [])],
        client_transport_dates=[as_date(item) for item in (row.client_transport_dates or [])],
        standby_dates=[as_date(item) for item in (row.standby_dates or [])],
        segments=_segments(row.segments),
        change_of=row.parent_id,
        change_approved=row.change_approved,
        has_unapproved_change=bool(row.has_unapproved_change or pending_child),
        policy=policy if policy is not None else to_policy(db, policy_by_id(db, row.policy_version_id)),
        created_at=_naive(row.created_at),
        applicant_id=row.applicant_id,
    )


def other_onsite(db, row: Application) -> list[EngineApp]:
    traveler_ids = db.scalars(
        select(ApplicationTraveler.user_id).where(ApplicationTraveler.application_id == row.id)
    ).all()
    if not traveler_ids or not row.project_id:
        return []
    others = db.scalars(
        select(Application)
        .join(ApplicationTraveler, ApplicationTraveler.application_id == Application.id)
        .where(
            Application.id != row.id,
            Application.trip_type == "onsite",
            Application.project_id == row.project_id,
            ApplicationTraveler.user_id.in_(traveler_ids),
        )
    ).all()
    policy_cache = {}
    built = []
    for other in others:
        if other.policy_version_id not in policy_cache:
            policy_cache[other.policy_version_id] = to_policy(db, policy_by_id(db, other.policy_version_id))
        built.append(to_application(db, other, policy_cache[other.policy_version_id]))
    return built


def to_order(row: Order, own_reimbursement_id: str | None = None, ignore_claims: bool = False) -> EngineOrder:
    already = bool(row.reimbursement_id) and row.reimbursement_id != own_reimbursement_id
    if ignore_claims:
        already = False
    return EngineOrder(
        id=row.id,
        type=row.type,
        traveler_id=row.traveler_id or "",
        amount=D(row.amount) if row.amount is not None else None,
        start=as_date(row.start_date) if row.start_date else None,
        end=as_date(row.end_date) if row.end_date else None,
        origin=row.origin,
        destination=row.destination,
        cabin=row.cabin,
        benchmark_amount=D(row.benchmark_amount) if row.benchmark_amount is not None else None,
        payment_mode=row.payment_mode,
        application_id=row.application_id,
        is_supplement=row.is_supplement,
        change_fee=D(row.change_fee or 0),
        refund_fee=D(row.refund_fee or 0),
        change_approved=row.change_approved,
        over_standard_status=row.over_standard_status,
        approved_reimburse_amount=D(row.approved_reimburse_amount) if row.approved_reimburse_amount is not None else None,
        over_standard_reason=row.over_standard_reason,
        city=row.city,
        nights=row.nights,
        scope=row.scope,
        external_ref=row.external_ref,
        already_reimbursed=already,
    )


def to_invoice(row: Invoice, own_reimbursement_id: str | None = None, ignore_claims: bool = False) -> InvoiceDoc:
    status = row.status
    if ignore_claims or (own_reimbursement_id and row.reimbursement_id == own_reimbursement_id):
        status = "available"
    return InvoiceDoc(
        id=row.id,
        number=row.number,
        category=row.category,
        amount=D(row.amount),
        verification=row.verification_result,
        traveler_id=row.traveler_id,
        date=as_date(row.invoice_date) if row.invoice_date else None,
        city=row.city,
        order_id=row.order_id,
        status=status,
        nights=row.nights,
    )


def can_view_application(user: User, row: Application, traveler_ids: list[str]):
    if user.role in {"admin", "finance", "booker"}:
        return
    if user.role == "dept_approver":
        if row.department_id != user.department_id:
            raise BusinessError("ROLE_FORBIDDEN", "不能查看其他部门的单据。", 403)
        return
    if user.id == row.applicant_id or user.id in traveler_ids:
        return
    raise BusinessError("ROLE_FORBIDDEN", "只能查看自己的单据。", 403)


def _pool_for(db, cost_owner_type: str, project_id: str | None, department_id: str | None) -> BudgetPool:
    if cost_owner_type == "project":
        pool = db.scalar(select(BudgetPool).where(BudgetPool.owner_type == "project", BudgetPool.owner_id == project_id))
    else:
        pool = db.scalar(
            select(BudgetPool).where(BudgetPool.owner_type == "department", BudgetPool.owner_id == department_id)
        )
    if pool is None:
        raise BusinessError("ATTRIBUTION_UNMATCHED", "费用归属没有对应的预算池，拒绝占用。")
    return pool


def _reserve(db, pool_id: str, amount: Decimal):
    result = db.execute(
        update(BudgetPool)
        .where(BudgetPool.id == pool_id, BudgetPool.reserved + amount <= BudgetPool.total)
        .values(reserved=BudgetPool.reserved + amount)
    )
    if result.rowcount != 1:
        raise BusinessError("BUDGET_EXCEEDED", "预算不足，申请不能提交。")


def _release(db, pool_id: str, amount: Decimal):
    db.execute(
        update(BudgetPool)
        .where(BudgetPool.id == pool_id, BudgetPool.reserved >= amount)
        .values(reserved=BudgetPool.reserved - amount)
    )


def _consume_application(db, application_id: str, amount: Decimal):
    result = db.execute(
        update(Application)
        .where(
            Application.id == application_id,
            Application.consumed_budget + amount <= Application.estimated_budget,
        )
        .values(consumed_budget=Application.consumed_budget + amount)
    )
    if result.rowcount != 1:
        raise BusinessError("BUDGET_EXCEEDED", "订单金额超出该申请剩余预算，已拦截。")


def _date_span(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _lock_onsite(db, user_ids: list[str], project_id: str):
    for user_id in user_ids:
        try:
            with db.begin_nested():
                db.add(OnsiteMutex(user_id=user_id, project_id=project_id, version=0))
                db.flush()
        except IntegrityError:
            pass
        db.execute(
            update(OnsiteMutex)
            .where(OnsiteMutex.user_id == user_id, OnsiteMutex.project_id == project_id)
            .values(version=OnsiteMutex.version + 1)
        )


def _insert_holds(db, application: Application, user_ids: list[str], days: list[date]):
    for user_id in user_ids:
        for day in days:
            db.add(
                OnsiteHold(
                    id=new_id("hold"),
                    user_id=user_id,
                    project_id=application.project_id,
                    day=day,
                    application_id=application.id,
                )
            )
    db.flush()


def _reject_application(db, actor, row: Application, code: str, reason: str):
    row.status = "rejected"
    row.rejection_code = code
    row.rejection_reason = reason
    if row.budget_pool_id and row.submitted_at:
        _release(db, row.budget_pool_id, D(row.estimated_budget))
        row.budget_pool_id = None
    db.query(OnsiteHold).filter(OnsiteHold.application_id == row.id).delete()
    audit(db, actor, "application", row.id, "reject", _policy_code(db, row.policy_version_id), False, code, reason)
    db.commit()


def _policy_code(db, policy_id: str | None) -> str | None:
    if not policy_id:
        return None
    row = db.get(PolicyVersion, policy_id)
    return row.code if row else None


def create_application(db, actor: User, data: dict) -> Application:
    require(actor, {"traveler", "admin"})
    policy = active_policy(db)
    traveler_ids = data.get("traveler_ids") or [actor.id]
    if actor.role == "traveler" and actor.id not in traveler_ids and actor.id != data.get("applicant_id", actor.id):
        raise BusinessError("ROLE_FORBIDDEN", "不能代其他出行人提交申请。", 403)
    travelers = []
    for traveler_id in traveler_ids:
        user = db.get(User, traveler_id)
        if user is None or not user.rank:
            raise BusinessError("MISSING_REQUIRED_FIELDS", "出行人必须是已维护职级的员工。")
        travelers.append(user)
    trip_type = data.get("trip_type")
    if trip_type not in {"onsite", "client_visit", "training"}:
        raise BusinessError("MISSING_REQUIRED_FIELDS", "出差类型必须是项目驻场、客户拜访或培训开会。")
    if not data.get("cost_owner_type"):
        raise BusinessError("MISSING_REQUIRED_FIELDS", "必须显式选择费用归属。")
    project = db.get(Project, data.get("project_id")) if data.get("project_id") else None
    department = db.get(Department, data.get("department_id")) if data.get("department_id") else None
    if data["cost_owner_type"] == "project":
        if project is None:
            raise BusinessError("MISSING_REQUIRED_FIELDS", "项目驻场或项目归属必须选择具体项目。")
        cost_center = project.cost_center_code
        if department is None:
            department = db.get(Department, travelers[0].department_id)
    elif data["cost_owner_type"] == "department":
        if department is None:
            raise BusinessError("MISSING_REQUIRED_FIELDS", "归属部门必须在申请单上显式选择。")
        cost_center = department.cost_center_code
        project = None
    else:
        raise BusinessError("MISSING_REQUIRED_FIELDS", "费用归属无效。")
    if trip_type == "onsite" and data["cost_owner_type"] != "project":
        raise BusinessError("MISSING_REQUIRED_FIELDS", "项目驻场的费用归属必须落到项目或成本中心。")
    if trip_type == "client_visit" and (not data.get("client_name") or not data.get("visit_purpose")):
        raise BusinessError("MISSING_REQUIRED_FIELDS", "客户拜访必须填写拜访对象和拜访事由。")
    event = None
    if trip_type == "training":
        event = db.get(Event, data.get("event_id"))
        if event is None:
            raise BusinessError("MISSING_REQUIRED_FIELDS", "培训开会必须关联会议或培训信息。")
    row = Application(
        id=data.get("id") or new_id("app"),
        code=data.get("code") or f"SQ{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4]}",
        applicant_id=actor.id if actor.role == "traveler" else data.get("applicant_id") or travelers[0].id,
        trip_type=trip_type,
        scenario=data.get("scenario") or "",
        reason=data.get("reason") or "",
        origin=data["origin"],
        destination=data["destination"],
        start_date=as_date(data["start_date"]),
        end_date=as_date(data["end_date"]),
        headcount=len(travelers),
        transport_mode=data.get("transport_mode") or "train",
        estimated_budget=D(data["estimated_budget"]),
        consumed_budget=Decimal("0.00"),
        cost_owner_type=data["cost_owner_type"],
        department_id=department.id if department else None,
        project_id=project.id if project else None,
        cost_center_code=cost_center,
        client_name=data.get("client_name"),
        visit_purpose=data.get("visit_purpose"),
        event_id=event.id if event else None,
        project_provides_lodging=bool(data.get("project_provides_lodging")),
        project_provides_meals=bool(data.get("project_provides_meals")),
        client_meal_dates=list(data.get("client_meal_dates") or []),
        client_transport_dates=list(data.get("client_transport_dates") or []),
        standby_dates=list(data.get("standby_dates") or []),
        segments=data.get("segments"),
        parent_id=data.get("parent_id"),
        has_unapproved_change=bool(data.get("parent_id")),
        status="draft",
        policy_version_id=policy.id,
    )
    if row.end_date < row.start_date:
        raise BusinessError("PERIOD_INVALID", "结束日期早于开始日期。")
    db.add(row)
    for user in travelers:
        db.add(ApplicationTraveler(application_id=row.id, user_id=user.id))
    audit(db, actor, "application", row.id, "create", policy.code, True, data.get("trip_type"), "草稿已保存，尚未占用预算")
    db.commit()
    return row


def submit_application(db, actor: User, application_id: str) -> Application:
    row = db.get(Application, application_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "申请不存在。", 404)
    traveler_ids = [item.id for item in traveler_rows(db, row.id)]
    if actor.role == "traveler" and actor.id != row.applicant_id and actor.id not in traveler_ids:
        raise BusinessError("ROLE_FORBIDDEN", "不能提交别人的申请。", 403)
    if actor.role not in {"traveler", "admin"}:
        raise BusinessError("ROLE_FORBIDDEN", "当前角色不能提交申请。", 403)
    if row.status not in {"draft", "rejected"}:
        raise BusinessError("APPLICATION_STATUS", f"当前状态 {row.status} 不能提交。")
    view = to_application(db, row)
    from app.engine import missing_fields

    gaps = missing_fields(view)
    if gaps:
        _reject_application(db, actor, row, "MISSING_REQUIRED_FIELDS", "关键字段缺失：" + "、".join(gaps))
        raise BusinessError("MISSING_REQUIRED_FIELDS", "关键字段缺失：" + "、".join(gaps))
    if row.trip_type == "training":
        event = db.get(Event, row.event_id)
        if (
            row.destination != event.city
            or as_date(row.start_date) != as_date(event.report_date)
            or as_date(row.end_date) != as_date(event.return_date)
        ):
            _reject_application(db, actor, row, "TRAINING_MISMATCH", "会议通知的地点或日期与申请不一致。")
            raise BusinessError("TRAINING_MISMATCH", "会议通知的地点或日期与申请不一致。")
    if row.trip_type == "onsite":
        _lock_onsite(db, traveler_ids, row.project_id)
        db.flush()
        conflict_view = to_application(db, row)
        from app.engine import find_onsite_conflict

        conflict = find_onsite_conflict(conflict_view, other_onsite(db, row))
        if conflict:
            code, reason, _ = conflict
            _reject_application(db, actor, row, code, reason)
            raise BusinessError(code, reason, 409)
    pool = _pool_for(db, row.cost_owner_type, row.project_id, row.department_id)
    try:
        _reserve(db, pool.id, D(row.estimated_budget))
        if row.trip_type == "onsite":
            occupied = set()
            if row.parent_id:
                occupied = {
                    as_date(item.day)
                    for item in db.scalars(select(OnsiteHold).where(OnsiteHold.application_id == row.parent_id))
                }
            fresh = [day for day in _date_span(as_date(row.start_date), as_date(row.end_date)) if day not in occupied]
            _insert_holds(db, row, traveler_ids, fresh)
        row.status = "pending_dept"
        row.submitted_at = stamp()
        row.budget_pool_id = pool.id
        row.rejection_code = None
        row.rejection_reason = None
        _open_tasks(db, "application", row.id, row.department_id, ("dept", "finance"))
        metrics.incr_application()
        audit(
            db,
            actor,
            "application",
            row.id,
            "submit",
            _policy_code(db, row.policy_version_id),
            True,
            {"budget": money(row.estimated_budget), "trip_type": row.trip_type},
            "已占用预算，等待部门审批",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.get(Application, application_id)
        _reject_application(db, actor, row, "ONSITE_OVERLAP", "同一人同一项目的驻场日期重叠，后单拒绝。")
        raise BusinessError("ONSITE_OVERLAP", "同一人同一项目的驻场日期重叠，后单拒绝。", 409)
    return row


def _open_tasks(db, entity_type: str, entity_id: str, department_id: str | None, steps: tuple[str, str]):
    db.query(ApprovalTask).filter(
        ApprovalTask.entity_type == entity_type,
        ApprovalTask.entity_id == entity_id,
        ApprovalTask.status == "pending",
    ).delete()
    role = {"dept": "dept_approver", "finance": "finance", "over_dept": "dept_approver", "over_finance": "finance"}
    for index, step in enumerate(steps, start=1):
        db.add(
            ApprovalTask(
                id=new_id("task"),
                entity_type=entity_type,
                entity_id=entity_id,
                step=step,
                sequence=index,
                status="pending",
                assignee_role=role[step],
                department_id=department_id,
            )
        )


def decide_task(db, actor: User, task_id: str, approve: bool, comment: str = "", acknowledge_budget: bool = False, approved_amount=None):
    task = db.get(ApprovalTask, task_id)
    if task is None:
        raise BusinessError("NOT_FOUND", "审批任务不存在。", 404)
    if task.status != "pending":
        raise BusinessError("APPROVAL_STATUS", "该审批已经处理。")
    if actor.role != task.assignee_role:
        raise BusinessError("ROLE_FORBIDDEN", "越权：当前角色不能处理这个审批节点。", 403)
    if actor.role == "dept_approver" and task.department_id and task.department_id != actor.department_id:
        raise BusinessError("ROLE_FORBIDDEN", "越权：不能审批其他部门的单据。", 403)
    earlier = db.scalar(
        select(ApprovalTask).where(
            ApprovalTask.entity_type == task.entity_type,
            ApprovalTask.entity_id == task.entity_id,
            ApprovalTask.sequence < task.sequence,
            ApprovalTask.status != "approved",
        )
    )
    if earlier is not None:
        raise BusinessError("APPROVAL_ORDER", "财务复审必须在部门审批通过之后。")
    task.status = "approved" if approve else "rejected"
    task.decided_by = actor.id
    task.decided_at = stamp()
    task.comment = comment or ""
    if approved_amount is not None:
        task.approved_amount = D(approved_amount)
    if task.entity_type == "application":
        _decide_application(db, actor, task, approve, comment)
    elif task.entity_type == "reimbursement":
        _decide_reimbursement(db, actor, task, approve, comment, acknowledge_budget)
    elif task.entity_type == "order":
        _decide_order(db, actor, task, approve, comment, approved_amount)
    else:
        raise BusinessError("APPROVAL_STATUS", "未知审批对象。")
    db.commit()
    return task


def _decide_application(db, actor, task, approve, comment):
    row = db.get(Application, task.entity_id)
    policy_code = _policy_code(db, row.policy_version_id)
    if not approve:
        row.status = "rejected"
        row.rejection_code = "APPROVAL_REJECTED"
        row.rejection_reason = comment or "审批未通过"
        if row.budget_pool_id:
            _release(db, row.budget_pool_id, D(row.estimated_budget))
        db.query(OnsiteHold).filter(OnsiteHold.application_id == row.id).delete()
        db.query(ApprovalTask).filter(
            ApprovalTask.entity_type == "application",
            ApprovalTask.entity_id == row.id,
            ApprovalTask.status == "pending",
        ).update({"status": "cancelled"})
        audit(db, actor, "application", row.id, "approval_reject", policy_code, False, task.step, row.rejection_reason)
        return
    nxt = db.scalar(
        select(ApprovalTask).where(
            ApprovalTask.entity_type == "application",
            ApprovalTask.entity_id == row.id,
            ApprovalTask.sequence > task.sequence,
            ApprovalTask.status == "pending",
        ).order_by(ApprovalTask.sequence)
    )
    if nxt is not None:
        row.status = "pending_finance"
        audit(db, actor, "application", row.id, "approval_dept", policy_code, True, task.step, "部门已通过，等待财务复审")
        return
    if row.parent_id:
        _activate_change(db, row)
    else:
        row.status = "approved"
    audit(db, actor, "application", row.id, "approval_final", policy_code, True, task.step, "申请已通过，可以预订和报销")


def _activate_change(db, child: Application):
    parent = db.get(Application, child.parent_id)
    traveler_ids = [item.id for item in traveler_rows(db, child.id)]
    _lock_onsite(db, traveler_ids, child.project_id)
    db.query(OnsiteHold).filter(OnsiteHold.application_id.in_([parent.id, child.id])).delete(synchronize_session=False)
    db.flush()
    try:
        _insert_holds(db, child, traveler_ids, list(_date_span(as_date(child.start_date), as_date(child.end_date))))
    except IntegrityError:
        raise BusinessError("ONSITE_OVERLAP", "补批后的驻场日期与其他申请重叠，变更不能生效。", 409)
    parent.status = "superseded"
    child.status = "approved"
    child.change_approved = True
    child.has_unapproved_change = False


def _decide_reimbursement(db, actor, task, approve, comment, acknowledge_budget):
    row = db.get(Reimbursement, task.entity_id)
    result = row.result_json or {}
    policy_code = _policy_code(db, row.policy_version_id)
    if approve and result.get("budget_exceeded") and not acknowledge_budget:
        task.status = "pending"
        task.decided_by = None
        task.decided_at = None
        raise BusinessError("BUDGET_EXCEEDED", "报销超出已批预算。财务必须显式确认后才能通过，不能静默通过。")
    if approve and result.get("budget_exceeded") and not (comment or "").strip():
        task.status = "pending"
        task.decided_by = None
        raise BusinessError("BUDGET_EXCEEDED", "超预算通过必须填写审核意见。")
    if not approve:
        row.status = "rejected"
        _unlock_claim(db, row.id)
        db.query(ApprovalTask).filter(
            ApprovalTask.entity_type == "reimbursement",
            ApprovalTask.entity_id == row.id,
            ApprovalTask.status == "pending",
        ).update({"status": "cancelled"})
        audit(db, actor, "reimbursement", row.id, "approval_reject", policy_code, False, task.step, comment or "退回")
        return
    nxt = db.scalar(
        select(ApprovalTask).where(
            ApprovalTask.entity_type == "reimbursement",
            ApprovalTask.entity_id == row.id,
            ApprovalTask.sequence > task.sequence,
            ApprovalTask.status == "pending",
        )
    )
    row.status = "pending_finance" if nxt is not None else "approved"
    audit(
        db,
        actor,
        "reimbursement",
        row.id,
        "approval",
        policy_code,
        True,
        {"step": task.step, "acknowledge_budget": acknowledge_budget},
        f"报销状态变为 {row.status}",
    )


def _decide_order(db, actor, task, approve, comment, approved_amount):
    row = db.get(Order, task.entity_id)
    policy_code = _policy_code(db, row.policy_version_id)
    if not approve:
        row.over_standard_status = "rejected"
        row.compliance_status = "ready"
        row.compliance_message = comment or "超标审批未通过，报销时超标部分自理。"
        db.query(ApprovalTask).filter(
            ApprovalTask.entity_type == "order",
            ApprovalTask.entity_id == row.id,
            ApprovalTask.status == "pending",
        ).update({"status": "cancelled"})
        audit(db, actor, "order", row.id, "over_reject", policy_code, True, task.step, row.compliance_message)
        return
    nxt = db.scalar(
        select(ApprovalTask).where(
            ApprovalTask.entity_type == "order",
            ApprovalTask.entity_id == row.id,
            ApprovalTask.sequence > task.sequence,
            ApprovalTask.status == "pending",
        )
    )
    if nxt is not None:
        audit(db, actor, "order", row.id, "over_dept", policy_code, True, task.step, "超标申请已过部门，等待财务")
        return
    if approved_amount is None:
        task.status = "pending"
        task.decided_by = None
        raise BusinessError("APPROVED_AMOUNT_REQUIRED", "超标审批必须给出批准口径金额，不能由系统猜测。")
    row.over_standard_status = "approved"
    row.approved_reimburse_amount = D(approved_amount)
    row.compliance_status = "ready"
    row.compliance_message = f"超标审批通过，批准口径 {money(approved_amount)}。"
    audit(db, actor, "order", row.id, "over_approve", policy_code, True, {"approved_amount": money(approved_amount)}, row.compliance_message)


def _unlock_claim(db, reimbursement_id: str):
    db.execute(
        update(Invoice)
        .where(Invoice.reimbursement_id == reimbursement_id)
        .values(status="available", reimbursement_id=None)
    )
    db.execute(update(Order).where(Order.reimbursement_id == reimbursement_id).values(reimbursement_id=None))


def create_order(db, actor: User, data: dict) -> Order:
    require(actor, {"booker", "traveler", "admin", "finance"})
    application = db.get(Application, data.get("application_id"))
    if application is None:
        raise BusinessError("NOT_FOUND", "申请不存在，订单不能脱离申请单。", 404)
    if actor.role == "traveler":
        ids = [item.id for item in traveler_rows(db, application.id)]
        if actor.id not in ids and actor.id != application.applicant_id:
            raise BusinessError("ROLE_FORBIDDEN", "不能为别人的申请录入订单。", 403)
    view = to_application(db, application)
    order_id = data.get("id") or new_id("ord")
    engine_order = EngineOrder(
        id=order_id,
        type=data["type"],
        traveler_id=data.get("traveler_id") or "",
        amount=D(data["amount"]) if data.get("amount") not in (None, "") else None,
        start=as_date(data["start_date"]) if data.get("start_date") else None,
        end=as_date(data["end_date"]) if data.get("end_date") else None,
        origin=data.get("origin"),
        destination=data.get("destination"),
        cabin=data.get("cabin"),
        benchmark_amount=D(data["benchmark_amount"]) if data.get("benchmark_amount") not in (None, "") else None,
        payment_mode=data.get("payment_mode") or "personal",
        application_id=application.id,
        is_supplement=bool(data.get("is_supplement")),
        change_fee=D(data.get("change_fee") or 0),
        refund_fee=D(data.get("refund_fee") or 0),
        change_approved=bool(data.get("change_approved")),
        over_standard_status="ok",
        over_standard_reason=data.get("over_standard_reason"),
        city=data.get("city") or application.destination,
        nights=data.get("nights"),
        scope=data.get("scope"),
        external_ref=data.get("external_ref"),
        seat_count=data.get("seat_count"),
    )
    spent = D(application.consumed_budget or 0)
    result = validate_order(engine_order, view, spent)
    policy_code = view.policy.code if view.policy else None
    dedup = _dedup_key(view.policy, data, application.code)
    if dedup and db.scalar(select(Order.id).where(Order.dedup_key == dedup, Order.compliance_status != "rejected")):
        audit(db, actor, "order", order_id, "dedup", policy_code, False, dedup, "重复订单已剔除")
        db.commit()
        raise BusinessError("DUPLICATE_ORDER", "按去重键识别为重复订单，未重复入账。")
    row = Order(
        id=order_id,
        application_id=application.id,
        traveler_id=data.get("traveler_id"),
        type=data["type"],
        payment_mode=data.get("payment_mode") or "personal",
        amount=engine_order.amount,
        start_date=engine_order.start,
        end_date=engine_order.end,
        origin=data.get("origin"),
        destination=data.get("destination"),
        city=engine_order.city,
        cabin=data.get("cabin"),
        benchmark_amount=engine_order.benchmark_amount,
        nights=data.get("nights"),
        scope=data.get("scope"),
        is_supplement=bool(data.get("is_supplement")),
        external_ref=data.get("external_ref"),
        dedup_key=dedup,
        change_fee=engine_order.change_fee,
        refund_fee=engine_order.refund_fee,
        change_approved=bool(data.get("change_approved")),
        over_standard_reason=data.get("over_standard_reason"),
        policy_version_id=application.policy_version_id,
        compliance_code=result.get("error_code"),
        compliance_message=result.get("message"),
        created_by=actor.id,
    )
    if not result["ok"]:
        row.compliance_status = "rejected"
        row.over_standard_status = "blocked" if result["error_code"] == "OVER_STANDARD_BLOCKED" else "ok"
        db.add(row)
        if result["error_code"] in {"THREE_WAY_MISMATCH", "OUTSIDE_APPLICATION", "TRAVELER_NOT_ON_APPLICATION", "AMOUNT_MISMATCH"}:
            metrics.incr_three_way()
        metrics.incr_rule_reject()
        audit(db, actor, "order", row.id, "validate", policy_code, False, data.get("type"), result["message"])
        db.commit()
        raise BusinessError(result["error_code"], result["message"])
    try:
        _consume_application(db, application.id, D(row.amount))
    except BusinessError:
        db.rollback()
        raise
    if result.get("over_standard") and result.get("pending_approval"):
        row.compliance_status = "pending_over_approval"
        row.over_standard_status = "pending"
        db.add(row)
        _open_tasks(db, "order", row.id, application.department_id, ("over_dept", "over_finance"))
        conclusion = "超标订单已提交审批，未按全额直接入账"
    else:
        row.compliance_status = "ready"
        row.over_standard_status = "ok"
        db.add(row)
        conclusion = result["message"]
    audit(db, actor, "order", row.id, "create", policy_code, True, {"amount": money(row.amount), "supplement": row.is_supplement}, conclusion)
    db.commit()
    return row


def _dedup_key(policy: Policy, data: dict, application_code: str) -> str:
    fields = policy.dedup_fields or ["traveler", "type", "date", "amount", "application_code"]
    source = {
        "traveler": data.get("traveler_id") or data.get("traveler") or "",
        "type": data.get("type") or "",
        "date": str(data.get("start_date") or data.get("date") or ""),
        "amount": str(data.get("amount") or ""),
        "application_code": application_code,
        "external_ref": data.get("external_ref") or "",
    }
    return "|".join(str(source.get(field, "")) for field in fields)


def import_orders(db, actor: User, rows: list[dict]) -> dict:
    require(actor, {"booker", "admin", "finance"})
    policy = active_policy(db)
    existing = set(db.scalars(select(Order.dedup_key).where(Order.dedup_key.is_not(None))).all())
    classified = classify_import_rows(rows, existing, policy.dedup_fields or ["traveler", "type", "date", "amount", "application_code"])
    saved = []
    rejected = []
    for row in classified["accepted"]:
        application = db.scalar(select(Application).where(Application.code == row.get("application_code")))
        if application is None:
            rejected.append({"code": "NOT_FOUND", "message": "申请单号无法匹配", "sample": row})
            continue
        traveler = None
        if row.get("traveler_id"):
            traveler = db.get(User, row["traveler_id"])
        elif row.get("traveler"):
            traveler = db.scalar(select(User).where(User.display_name == row["traveler"]))
        payload = {
            "application_id": application.id,
            "traveler_id": traveler.id if traveler else None,
            "type": row.get("type"),
            "amount": row.get("amount"),
            "start_date": row.get("date") or row.get("start_date"),
            "end_date": row.get("end_date"),
            "cabin": row.get("cabin"),
            "payment_mode": row.get("payment_mode") or "personal",
            "is_supplement": True,
            "benchmark_amount": row.get("benchmark_amount"),
            "over_standard_reason": row.get("over_standard_reason"),
            "nights": row.get("nights"),
            "city": row.get("city"),
            "external_ref": row.get("external_ref"),
        }
        try:
            order = create_order(db, actor, payload)
            saved.append(order.id)
        except BusinessError as exc:
            rejected.append({"code": exc.code, "message": exc.message, "sample": {"application_code": row.get("application_code"), "amount": row.get("amount")}})
    audit(
        db,
        actor,
        "order",
        "import",
        "import",
        policy.code,
        True,
        {"rows": len(rows)},
        f"受理 {len(saved)}，去重 {classified['duplicate_count']}，脏数据 {classified['dirty_count']}，校验拒绝 {len(rejected)}",
    )
    db.commit()
    return {
        "accepted_count": len(saved),
        "duplicate_count": classified["duplicate_count"],
        "dirty_count": classified["dirty_count"],
        "dirty_samples": classified["dirty_samples"],
        "rejected_count": len(rejected),
        "rejected": rejected,
        "order_ids": saved,
        "notice": "导入只写入本系统的出行订单，没有对接航司、酒店或网约车。",
    }


def create_invoice(db, actor: User, data: dict) -> Invoice:
    require(actor, {"traveler", "booker", "finance", "admin"})
    if data.get("verification_result", "pass") not in VERIFY_RESULTS:
        raise BusinessError("MOCK_VERIFICATION_FAILED", "模拟查验结果必须是通过、查无此票、发票已用、抬头或税号不符、金额不符之一。")
    order = db.get(Order, data.get("order_id")) if data.get("order_id") else None
    row = Invoice(
        id=data.get("id") or new_id("inv"),
        number=data["number"],
        category=data["category"],
        amount=D(data["amount"]),
        traveler_id=data.get("traveler_id") or (order.traveler_id if order else None),
        invoice_date=as_date(data["invoice_date"]) if data.get("invoice_date") else None,
        city=data.get("city"),
        order_id=data.get("order_id"),
        nights=data.get("nights"),
        buyer_name=data.get("buyer_name") or COMPANY_NAME,
        buyer_tax_no=data.get("buyer_tax_no") or COMPANY_TAX_NO,
        verification_result=data.get("verification_result") or "pass",
        verification_message=data.get("verification_message") or "模拟查验",
        simulated=True,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise BusinessError("INVOICE_ALREADY_CLAIMED", "票据号码重复，不能再次入池。", 409)
    audit(db, actor, "invoice", row.id, "create", None, True, row.number, f"模拟查验：{row.verification_result}")
    db.commit()
    return row


def set_verification(db, actor: User, invoice_id: str, result: str, message: str | None):
    require(actor, {"finance", "admin"})
    if result not in VERIFY_RESULTS:
        raise BusinessError("MOCK_VERIFICATION_FAILED", "不支持的模拟查验结果。")
    row = db.get(Invoice, invoice_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "票据不存在。", 404)
    if row.status != "available":
        raise BusinessError("INVOICE_ALREADY_CLAIMED", "票据已核销，不能再改查验结果。", 409)
    row.verification_result = result
    row.verification_message = message or "模拟查验"
    row.simulated = True
    audit(db, actor, "invoice", row.id, "mock_verify", None, True, result, "这是模拟查验，未对接税务机关")
    db.commit()
    return row


def build_context(
    db,
    application: Application,
    policy: Policy | None = None,
    own_reimbursement_id: str | None = None,
    ignore_claims: bool = False,
) -> CalcContext:
    view = to_application(db, application, policy)
    orders = db.scalars(
        select(Order).where(Order.application_id == application.id, Order.compliance_status.in_(["ready", "pending_over_approval"]))
    ).all()
    invoices = []
    if orders:
        invoices = db.scalars(select(Invoice).where(Invoice.order_id.in_([item.id for item in orders]))).all()
    return CalcContext(
        application=view,
        orders=[to_order(item, own_reimbursement_id, ignore_claims) for item in orders],
        invoices=[to_invoice(item, own_reimbursement_id, ignore_claims) for item in invoices],
        other_applications=other_onsite(db, application),
    )


def preview_reimbursement(db, actor: User, application_id: str, hypothetical_policy_id: str | None = None) -> dict:
    application = db.get(Application, application_id)
    if application is None:
        raise BusinessError("NOT_FOUND", "申请不存在。", 404)
    traveler_ids = [item.id for item in traveler_rows(db, application.id)]
    can_view_application(actor, application, traveler_ids)
    policy = None
    hypothetical = False
    if hypothetical_policy_id and hypothetical_policy_id != application.policy_version_id:
        policy = to_policy(db, policy_by_id(db, hypothetical_policy_id))
        hypothetical = True
    started = time.perf_counter()
    result = calculate(build_context(db, application, policy, ignore_claims=True))
    elapsed = (time.perf_counter() - started) * 1000
    metrics.observe_reimbursement(application.trip_type, elapsed, result)
    result["hypothetical"] = hypothetical
    result["stored"] = False
    if hypothetical:
        result["warnings"] = list(result.get("warnings") or []) + ["这是按另一制度版本做的试算，不会改写已保存的报销结果。"]
    return result


def _persist_rejected(db, actor, application, result, status_code="rejected"):
    policy = policy_by_id(db, application.policy_version_id)
    row = Reimbursement(
        id=new_id("rb"),
        code=f"RB{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4]}",
        application_id=application.id,
        applicant_id=actor.id,
        policy_version_id=policy.id,
        status="rejected",
        result_json=result,
        personal_claimable=D(result["personal_claimable"]) if result.get("personal_claimable") is not None else None,
        self_pay=D(result["self_pay"]) if result.get("self_pay") is not None else None,
        allowance=D(result["allowance"]) if result.get("allowance") is not None else None,
        company_paid=D(result["company_paid"]) if result.get("company_paid") is not None else None,
        budget_exceeded=bool(result.get("budget_exceeded")),
    )
    db.add(row)
    audit(
        db,
        actor,
        "reimbursement",
        row.id,
        "calculate_reject",
        policy.code,
        False,
        {"application": application.code},
        result.get("refusal_reason") or result.get("refusal_code"),
    )
    db.commit()
    return row


def submit_reimbursement(db, actor: User, application_id: str) -> Reimbursement:
    require(actor, {"traveler", "admin"})
    application = db.get(Application, application_id)
    if application is None:
        raise BusinessError("NOT_FOUND", "申请不存在。", 404)
    traveler_ids = [item.id for item in traveler_rows(db, application.id)]
    if actor.role == "traveler" and actor.id not in traveler_ids and actor.id != application.applicant_id:
        raise BusinessError("ROLE_FORBIDDEN", "只能提交自己的报销。", 403)
    if db.scalar(
        select(Reimbursement.id).where(
            Reimbursement.application_id == application.id,
            Reimbursement.status.in_(tuple(ACTIVE_REIMBURSE)),
        )
    ):
        result = {
            "calculated": True,
            "ok": False,
            "trip_type": application.trip_type,
            "trip_type_label": "",
            "policy_version": _policy_code(db, application.policy_version_id),
            "personal_claimable": "0.00",
            "self_pay": "0.00",
            "allowance": "0.00",
            "company_paid": "0.00",
            "refusal_code": "INVOICE_ALREADY_CLAIMED",
            "refusal_reason": "该申请的票据或报销单已被占用，后单拒绝。",
            "clauses": [],
            "lines": [],
            "rejected_items": [],
            "warnings": [],
            "budget_exceeded": False,
            "payment_allowed": False,
        }
        saved = _persist_rejected(db, actor, application, result)
        raise BusinessError("INVOICE_ALREADY_CLAIMED", "该申请已有报销占用票据，后单拒绝。", 409)
    pending_over = db.scalar(
        select(Order.id).where(
            Order.application_id == application.id,
            Order.compliance_status == "pending_over_approval",
        )
    )
    if pending_over:
        raise BusinessError("OVER_STANDARD_PENDING", "还有未完成的超标审批，不能核定报销。")
    orders = db.scalars(
        select(Order).where(
            Order.application_id == application.id,
            Order.compliance_status == "ready",
            Order.reimbursement_id.is_(None),
        )
    ).all()
    invoices = []
    if orders:
        invoices = db.scalars(select(Invoice).where(Invoice.order_id.in_([item.id for item in orders]))).all()
    policy = policy_by_id(db, application.policy_version_id)
    row = Reimbursement(
        id=new_id("rb"),
        code=f"RB{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:4]}",
        application_id=application.id,
        applicant_id=actor.id if actor.role == "traveler" else application.applicant_id,
        policy_version_id=policy.id,
        status="processing",
    )
    db.add(row)
    db.flush()
    for invoice in invoices:
        locked = db.execute(
            update(Invoice)
            .where(Invoice.id == invoice.id, Invoice.status == "available")
            .values(status="claimed", reimbursement_id=row.id)
        )
        if locked.rowcount != 1:
            db.rollback()
            conflict = {
                "calculated": True,
                "ok": False,
                "trip_type": application.trip_type,
                "policy_version": policy.code,
                "personal_claimable": "0.00",
                "self_pay": "0.00",
                "allowance": "0.00",
                "company_paid": "0.00",
                "refusal_code": "INVOICE_ALREADY_CLAIMED",
                "refusal_reason": "票据已被其他报销单核销，本单没有抢到。",
                "clauses": [],
                "lines": [],
                "rejected_items": [],
                "warnings": [],
                "budget_exceeded": False,
                "payment_allowed": False,
            }
            _persist_rejected(db, actor, application, conflict)
            raise BusinessError("INVOICE_ALREADY_CLAIMED", "票据已被其他报销单核销，本单没有抢到。", 409)
    for order in orders:
        locked = db.execute(
            update(Order)
            .where(Order.id == order.id, Order.reimbursement_id.is_(None))
            .values(reimbursement_id=row.id)
        )
        if locked.rowcount != 1:
            db.rollback()
            raise BusinessError("ORDER_ALREADY_REIMBURSED", "同一行程订单已被其他报销单占用。", 409)
    started = time.perf_counter()
    result = calculate(build_context(db, application, own_reimbursement_id=row.id))
    elapsed = (time.perf_counter() - started) * 1000
    metrics.observe_reimbursement(application.trip_type, elapsed, result)
    if not result.get("calculated") or not result.get("ok"):
        db.rollback()
        saved = _persist_rejected(db, actor, application, result)
        return saved
    stored = db.get(Reimbursement, row.id)
    stored.status = "pending_dept"
    stored.result_json = result
    stored.personal_claimable = D(result["personal_claimable"])
    stored.self_pay = D(result["self_pay"])
    stored.allowance = D(result["allowance"])
    stored.company_paid = D(result["company_paid"])
    stored.budget_exceeded = bool(result["budget_exceeded"])
    _open_tasks(db, "reimbursement", stored.id, application.department_id, ("dept", "finance"))
    audit(
        db,
        actor,
        "reimbursement",
        stored.id,
        "submit",
        policy.code,
        True,
        {"application": application.code, "policy": policy.code},
        f"个人可报 {result['personal_claimable']}，自理 {result['self_pay']}，补助 {result['allowance']}",
    )
    db.commit()
    return stored


def explain_stored(db, actor: User, reimbursement_id: str, proposed_amount=None) -> dict:
    row = db.get(Reimbursement, reimbursement_id)
    if row is None:
        raise BusinessError("NOT_FOUND", "报销单不存在。", 404)
    application = db.get(Application, row.application_id)
    can_view_application(actor, application, [item.id for item in traveler_rows(db, application.id)])
    if row.result_json is None:
        raise BusinessError("STANDARD_UNDEFINED", "这张报销单没有已保存的核定结果。")
    explained = explain_result(row.result_json, proposed_amount=proposed_amount)
    if not explained["ok"]:
        audit(db, actor, "reimbursement", row.id, "explain_rejected", row.result_json.get("policy_version"), False, "proposed_amount", explained["message"])
        db.commit()
        raise BusinessError(explained["error_code"], explained["message"])
    return explained


def create_policy_version(db, actor: User, patch: dict) -> PolicyVersion:
    require(actor, {"admin"})
    base = active_policy(db)
    count = len(db.scalars(select(PolicyVersion.id)).all())
    row = PolicyVersion(
        id=new_id("pol"),
        code=patch.get("code") or f"v{count + 1}",
        status="draft",
        note=patch.get("note") or "",
        over_standard_action=patch.get("over_standard_action") or base.over_standard_action,
        onsite_weekend_allowance=base.onsite_weekend_allowance if patch.get("onsite_weekend_allowance") is None else patch["onsite_weekend_allowance"],
        onsite_split_gap_days=patch.get("onsite_split_gap_days") or base.onsite_split_gap_days,
        standby_day_allowance=base.standby_day_allowance if patch.get("standby_day_allowance") is None else patch["standby_day_allowance"],
        dedup_fields=list(base.dedup_fields or []),
        created_by=actor.id,
    )
    db.add(row)
    db.flush()
    overrides = {
        (item["rank"], item["city_tier"], item["item"]): item
        for item in patch.get("standards") or []
    }
    for item in db.scalars(select(PolicyStandard).where(PolicyStandard.policy_version_id == base.id)):
        key = (item.rank, item.city_tier, item.item)
        changed = overrides.get(key)
        db.add(
            PolicyStandard(
                id=new_id("std"),
                policy_version_id=row.id,
                rank=item.rank,
                city_tier=item.city_tier,
                item=item.item,
                amount=D(changed["amount"]) if changed and changed.get("amount") is not None else item.amount,
                allowed=changed.get("allowed") if changed and changed.get("allowed") is not None else item.allowed,
            )
        )
    for item in db.scalars(select(PolicyCity).where(PolicyCity.policy_version_id == base.id)):
        db.add(PolicyCity(id=new_id("city"), policy_version_id=row.id, city=item.city, tier=item.tier))
    for item in db.scalars(select(PolicyClause).where(PolicyClause.policy_version_id == base.id)):
        db.add(PolicyClause(id=new_id("clause"), policy_version_id=row.id, code=item.code, text=item.text))
    if patch.get("activate", True):
        db.query(PolicyVersion).filter(PolicyVersion.status == "active").update({"status": "archived"})
        row.status = "active"
    audit(db, actor, "policy", row.id, "publish", row.code, True, patch.get("note"), "新制度版本已保存，历史核定不改写")
    db.commit()
    return row


def reconcile(db, user: User, group_by: str, start: date, end: date, department_id: str | None, project_id: str | None):
    require(user, {"finance", "admin", "dept_approver"})
    if user.role == "dept_approver":
        department_id = user.department_id
    known_departments = set(db.scalars(select(Department.id)).all())
    known_projects = set(db.scalars(select(Project.id)).all())
    rows = []
    reimbursements = db.scalars(select(Reimbursement).where(Reimbursement.status == "approved")).all()
    for item in reimbursements:
        application = db.get(Application, item.application_id)
        if item.personal_claimable:
            rows.append(
                {
                    "date": as_date(application.end_date),
                    "department_id": application.department_id,
                    "project_id": application.project_id,
                    "cost_owner_type": application.cost_owner_type,
                    "trip_type": application.trip_type,
                    "amount": item.personal_claimable,
                    "source": "personal",
                    "reimbursement_id": item.id,
                }
            )
        if item.company_paid:
            rows.append(
                {
                    "date": as_date(application.end_date),
                    "department_id": application.department_id,
                    "project_id": application.project_id,
                    "cost_owner_type": application.cost_owner_type,
                    "trip_type": application.trip_type,
                    "amount": item.company_paid,
                    "source": "company",
                    "reimbursement_id": item.id,
                }
            )
    try:
        result = aggregate_costs(
            rows,
            start=start,
            end=end,
            group_by=group_by,
            known_departments=known_departments,
            known_projects=known_projects,
            department_id=department_id,
            project_id=project_id,
        )
    except BusinessError:
        audit(db, user, "reconcile", group_by, "aggregate", None, False, {"department_id": department_id, "project_id": project_id}, "归属或区间无法匹配，未给出金额")
        db.commit()
        raise
    names = {}
    for dept in db.scalars(select(Department)):
        names[dept.id] = dept.name
    for project in db.scalars(select(Project)):
        names[project.id] = project.name
    for bucket in result["buckets"]:
        bucket["label"] = names.get(bucket["key"], bucket["key"])
    return result


def create_change(db, actor: User, application_id: str, data: dict) -> Application:
    parent = db.get(Application, application_id)
    if parent is None:
        raise BusinessError("NOT_FOUND", "原申请不存在。", 404)
    if parent.status != "approved":
        raise BusinessError("APPLICATION_NOT_APPROVED", "只有已通过的申请可以发起行程变更。")
    payload = {
        "trip_type": parent.trip_type,
        "traveler_ids": [item.id for item in traveler_rows(db, parent.id)],
        "reason": data.get("reason") or "行程变更",
        "origin": data.get("origin") or parent.origin,
        "destination": data.get("destination") or parent.destination,
        "start_date": data.get("start_date") or parent.start_date,
        "end_date": data.get("end_date") or parent.end_date,
        "estimated_budget": data.get("estimated_budget") or parent.estimated_budget,
        "cost_owner_type": parent.cost_owner_type,
        "department_id": parent.department_id,
        "project_id": parent.project_id,
        "client_name": parent.client_name,
        "visit_purpose": parent.visit_purpose,
        "event_id": parent.event_id,
        "project_provides_lodging": parent.project_provides_lodging,
        "project_provides_meals": parent.project_provides_meals,
        "client_meal_dates": parent.client_meal_dates,
        "client_transport_dates": parent.client_transport_dates,
        "segments": data.get("segments"),
        "parent_id": parent.id,
        "scenario": "行程变更待补批",
        "applicant_id": parent.applicant_id,
    }
    child = create_application(db, actor, payload)
    child = db.get(Application, child.id)
    child.policy_version_id = parent.policy_version_id
    child.has_unapproved_change = True
    db.commit()
    return child
