from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    cost_center_code: Mapped[str] = mapped_column(String(64))


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    cost_center_code: Mapped[str] = mapped_column(String(64))
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"))


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(32))
    rank: Mapped[str | None] = mapped_column(String(32), nullable=True)
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    password_salt: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    note: Mapped[str] = mapped_column(Text, default="")
    over_standard_action: Mapped[str] = mapped_column(String(32), default="submit_approval")
    onsite_weekend_allowance: Mapped[bool] = mapped_column(Boolean, default=False)
    onsite_split_gap_days: Mapped[int] = mapped_column(default=1)
    standby_day_allowance: Mapped[bool] = mapped_column(Boolean, default=True)
    dedup_fields: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    standards: Mapped[list["PolicyStandard"]] = relationship(back_populates="version")
    cities: Mapped[list["PolicyCity"]] = relationship(back_populates="version")
    clauses: Mapped[list["PolicyClause"]] = relationship(back_populates="version")


class PolicyStandard(Base):
    __tablename__ = "policy_standards"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    rank: Mapped[str] = mapped_column(String(32))
    city_tier: Mapped[str] = mapped_column(String(16))
    item: Mapped[str] = mapped_column(String(32))
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    allowed: Mapped[list | None] = mapped_column(JSON, nullable=True)
    version: Mapped[PolicyVersion] = relationship(back_populates="standards")


class PolicyCity(Base):
    __tablename__ = "policy_cities"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    city: Mapped[str] = mapped_column(String(64))
    tier: Mapped[str] = mapped_column(String(16))
    version: Mapped[PolicyVersion] = relationship(back_populates="cities")


class PolicyClause(Base):
    __tablename__ = "policy_clauses"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    code: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    version: Mapped[PolicyVersion] = relationship(back_populates="clauses")


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    organizer: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(64))
    report_date: Mapped[datetime] = mapped_column(Date)
    event_start: Mapped[datetime] = mapped_column(Date)
    event_end: Mapped[datetime] = mapped_column(Date)
    return_date: Mapped[datetime] = mapped_column(Date)
    organizer_provides_lodging: Mapped[bool] = mapped_column(Boolean, default=False)
    organizer_provides_meals: Mapped[bool] = mapped_column(Boolean, default=False)
    organizer_provides_local: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class BudgetPool(Base):
    __tablename__ = "budget_pools"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[str] = mapped_column(String(64))
    total: Mapped[float] = mapped_column(Numeric(14, 2))
    reserved: Mapped[float] = mapped_column(Numeric(14, 2), default=0)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    trip_type: Mapped[str] = mapped_column(String(32))
    scenario: Mapped[str] = mapped_column(String(100), default="")
    reason: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(64))
    destination: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[datetime] = mapped_column(Date)
    end_date: Mapped[datetime] = mapped_column(Date)
    headcount: Mapped[int] = mapped_column()
    transport_mode: Mapped[str] = mapped_column(String(32), default="train")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    estimated_budget: Mapped[float] = mapped_column(Numeric(14, 2))
    consumed_budget: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    cost_owner_type: Mapped[str] = mapped_column(String(32))
    department_id: Mapped[str | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    cost_center_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    visit_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str | None] = mapped_column(ForeignKey("events.id"), nullable=True)
    project_provides_lodging: Mapped[bool] = mapped_column(Boolean, default=False)
    project_provides_meals: Mapped[bool] = mapped_column(Boolean, default=False)
    client_meal_dates: Mapped[list] = mapped_column(JSON, default=list)
    client_transport_dates: Mapped[list] = mapped_column(JSON, default=list)
    standby_dates: Mapped[list] = mapped_column(JSON, default=list)
    segments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    has_unapproved_change: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    rejection_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_pool_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationTraveler(Base):
    __tablename__ = "application_travelers"
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    step: Mapped[str] = mapped_column(String(32))
    sequence: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default="pending")
    assignee_role: Mapped[str] = mapped_column(String(32))
    department_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    approved_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    traveler_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    payment_mode: Mapped[str] = mapped_column(String(32), default="personal")
    amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    origin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    destination: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cabin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    benchmark_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    nights: Mapped[int | None] = mapped_column(nullable=True)
    scope: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_supplement: Mapped[bool] = mapped_column(Boolean, default=False)
    external_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    dedup_key: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    change_fee: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    refund_fee: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    change_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    over_standard_status: Mapped[str] = mapped_column(String(32), default="ok")
    over_standard_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_reimburse_amount: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    compliance_status: Mapped[str] = mapped_column(String(32), default="ready")
    compliance_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compliance_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reimbursement_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    number: Mapped[str] = mapped_column(String(64), unique=True)
    category: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    amount: Mapped[float] = mapped_column(Numeric(14, 2))
    traveler_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nights: Mapped[int | None] = mapped_column(nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_tax_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_result: Mapped[str] = mapped_column(String(32), default="pass")
    verification_message: Mapped[str] = mapped_column(Text, default="模拟查验")
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="available")
    reimbursement_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reimbursement(Base):
    __tablename__ = "reimbursements"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    applicant_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    status: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    personal_claimable: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    self_pay: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    allowance: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    company_paid: Mapped[float | None] = mapped_column(Numeric(14, 2), nullable=True)
    budget_exceeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OnsiteHold(Base):
    __tablename__ = "onsite_holds"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str] = mapped_column(String(64))
    day: Mapped[datetime] = mapped_column(Date)
    application_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("user_id", "project_id", "day", name="uq_onsite_day"),)


class OnsiteMutex(Base):
    __tablename__ = "onsite_mutex"
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(default=0)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(100), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    conclusion_summary: Mapped[str] = mapped_column(Text, default="")
