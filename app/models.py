"""持久化事实、原始接收、追加处理历史和模拟外部效果。"""
import time
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, text
from .database import Base


def uuid_text():
    return str(uuid4())


def now_ms():
    return time.time_ns() // 1_000_000


def pk():
    return Column(String, primary_key=True, default=uuid_text)


def timestamp():
    return Column(Integer, nullable=False, default=now_ms)


def user_column():
    return Column(String, ForeignKey('app_users.id'), nullable=False)


class User(Base):
    __tablename__ = 'app_users'
    id = Column(String, primary_key=True)
    display_name = Column(String, nullable=False)
    created_at = timestamp()


class UserSettings(Base):
    __tablename__ = 'user_settings'
    user_id = Column(String, ForeignKey('app_users.id'), primary_key=True)
    paused = Column(Boolean, nullable=False, default=False)
    paused_at = Column(Integer)
    resumed_at = Column(Integer)
    updated_at = timestamp()


class AppMeta(Base):
    __tablename__ = 'app_meta'
    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = timestamp()


class EventReceipt(Base):
    __tablename__ = 'event_receipts'
    id = pk()
    user_id = user_column()
    event_id = Column(String)
    event_type = Column(String, nullable=False)
    source = Column(String, nullable=False, default='API')
    payload_json = Column(Text, nullable=False)
    payload_hash = Column(String, nullable=False)
    occurred_at = Column(Integer)
    received_at = timestamp()
    trace_id = Column(String, nullable=False, default=uuid_text)
    is_canonical = Column(Boolean, nullable=False, default=False)
    canonical_receipt_id = Column(String)
    processing_state = Column(String, nullable=False, default='RECEIVED')
    applied_at = Column(Integer)
    __table_args__ = (
        UniqueConstraint('user_id', 'id'),
        ForeignKeyConstraint(['user_id', 'canonical_receipt_id'], ['event_receipts.user_id', 'event_receipts.id']),
        CheckConstraint('is_canonical = 0 OR event_id IS NOT NULL'),
        Index('uq_receipt_canonical', 'user_id', 'event_id', unique=True, sqlite_where=text('is_canonical = 1')),
        Index('ix_receipts_user_time', 'user_id', 'received_at'),
        Index('ix_receipts_recovery', 'processing_state', 'received_at'),
    )


class Transaction(Base):
    __tablename__ = 'transactions'
    id = pk()
    user_id = user_column()
    tx_no = Column(String, nullable=False)
    merchant_id = Column(String, nullable=False)
    merchant_name = Column(String)
    amount_fen = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default='CNY')
    occurred_at = Column(Integer, nullable=False)
    source_receipt_id = Column(String, nullable=False)
    created_at = timestamp()
    __table_args__ = (
        UniqueConstraint('user_id', 'id'), UniqueConstraint('user_id', 'tx_no'),
        ForeignKeyConstraint(['user_id', 'source_receipt_id'], ['event_receipts.user_id', 'event_receipts.id']),
        CheckConstraint('amount_fen > 0'), CheckConstraint("currency = 'CNY'"),
        Index('ix_tx_candidate', 'user_id', 'merchant_id', 'amount_fen', 'occurred_at'),
    )


class Refund(Base):
    __tablename__ = 'refunds'
    id = pk()
    user_id = user_column()
    refund_no = Column(String, nullable=False)
    tx_no = Column(String)
    expected_amount_fen = Column(Integer)
    confirmed_at = Column(Integer)
    confirmation_receipt_id = Column(String)
    created_at = timestamp()
    updated_at = timestamp()
    __table_args__ = (
        UniqueConstraint('user_id', 'id'), UniqueConstraint('user_id', 'refund_no'),
        ForeignKeyConstraint(['user_id', 'confirmation_receipt_id'], ['event_receipts.user_id', 'event_receipts.id']),
        CheckConstraint('expected_amount_fen IS NULL OR expected_amount_fen > 0'),
        CheckConstraint('(expected_amount_fen IS NULL AND confirmed_at IS NULL) OR (expected_amount_fen IS NOT NULL AND confirmed_at IS NOT NULL)'),
    )


class RefundArrival(Base):
    __tablename__ = 'refund_arrivals'
    id = pk()
    user_id = user_column()
    refund_id = Column(String, nullable=False)
    arrival_no = Column(String, nullable=False)
    amount_fen = Column(Integer, nullable=False)
    arrived_at = Column(Integer, nullable=False)
    source_receipt_id = Column(String, nullable=False)
    created_at = timestamp()
    __table_args__ = (
        UniqueConstraint('user_id', 'id'), UniqueConstraint('user_id', 'arrival_no'),
        ForeignKeyConstraint(['user_id', 'refund_id'], ['refunds.user_id', 'refunds.id']),
        ForeignKeyConstraint(['user_id', 'source_receipt_id'], ['event_receipts.user_id', 'event_receipts.id']),
        CheckConstraint('amount_fen > 0'), Index('ix_arrival_refund', 'user_id', 'refund_id'),
    )


class Issue(Base):
    __tablename__ = 'issues'
    id = pk()
    user_id = user_column()
    issue_type = Column(String, nullable=False)
    identity_key = Column(String, nullable=False)
    status = Column(String, nullable=False, default='OPEN')
    followup_mode = Column(String, nullable=False, default='AWAITING_REPLY')
    reason = Column(Text, nullable=False)
    refund_id = Column(String)
    auto_followup_count = Column(Integer, nullable=False, default=0)
    initial_sent_at = Column(Integer)
    last_sent_at = Column(Integer)
    not_before_at = Column(Integer)
    schedule_generation = Column(Integer, nullable=False, default=0)
    requested_remind_at = Column(Integer)
    resolution_basis = Column(String)
    resolution_detail_json = Column(Text)
    resolved_at = Column(Integer)
    closed_at = Column(Integer)
    created_at = timestamp()
    updated_at = timestamp()
    __table_args__ = (
        UniqueConstraint('user_id', 'id'), UniqueConstraint('user_id', 'issue_type', 'identity_key'),
        ForeignKeyConstraint(['user_id', 'refund_id'], ['refunds.user_id', 'refunds.id']),
        CheckConstraint('auto_followup_count BETWEEN 0 AND 2'),
        CheckConstraint("issue_type IN ('DUPLICATE','REFUND_OVERDUE')"),
        CheckConstraint("status IN ('OPEN','CONFIRMED_PROBLEM','NORMAL','RESOLVED','CLOSED')"),
        CheckConstraint("followup_mode IN ('AWAITING_REPLY','WAITING_FACT','USER_SCHEDULED','STOPPED')"),
        CheckConstraint("status != 'RESOLVED' OR (resolution_basis IS NOT NULL AND resolved_at IS NOT NULL)"),
        CheckConstraint("issue_type != 'DUPLICATE' OR resolution_basis IS NULL OR resolution_basis = 'USER_CONFIRMED'"),
        Index('ix_issue_user_status', 'user_id', 'status'),
    )


class IssueTransaction(Base):
    __tablename__ = 'issue_transactions'
    user_id = user_column()
    issue_id = Column(String, primary_key=True)
    transaction_id = Column(String, primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'issue_id'], ['issues.user_id', 'issues.id']),
        ForeignKeyConstraint(['user_id', 'transaction_id'], ['transactions.user_id', 'transactions.id']),
        Index('ix_issue_tx_transaction', 'user_id', 'transaction_id'),
    )


class ReminderTask(Base):
    __tablename__ = 'reminder_tasks'
    id = pk()
    user_id = user_column()
    issue_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    task_key = Column(String, nullable=False)
    due_at = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default='PENDING')
    operation_id = Column(String, nullable=False, unique=True, default=uuid_text)
    content_json = Column(Text)
    submitted_at = Column(Integer)
    sent_at = Column(Integer)
    confirmed_at = Column(Integer)
    counted_at = Column(Integer)
    source_receipt_id = Column(String)
    error_code = Column(String)
    error_detail = Column(Text)
    created_at = timestamp()
    updated_at = timestamp()
    __table_args__ = (
        UniqueConstraint('user_id', 'id'), UniqueConstraint('user_id', 'issue_id', 'task_key'),
        ForeignKeyConstraint(['user_id', 'issue_id'], ['issues.user_id', 'issues.id']),
        ForeignKeyConstraint(['user_id', 'source_receipt_id'], ['event_receipts.user_id', 'event_receipts.id']),
        CheckConstraint("kind IN ('INITIAL','AUTO_FOLLOWUP','USER_SCHEDULED')"),
        CheckConstraint("status IN ('PENDING','SUBMITTING','SENT','FAILED','UNKNOWN','CANCELLED')"),
        Index('uq_task_active', 'user_id', 'issue_id', unique=True, sqlite_where=text("status IN ('PENDING','SUBMITTING','UNKNOWN')")),
        Index('ix_task_due', 'status', 'due_at'),
    )


class NotificationOperation(Base):
    __tablename__ = 'notification_operations'
    operation_id = Column(String, primary_key=True)
    user_id = user_column()
    task_id = Column(String, nullable=False, unique=True)
    behavior = Column(String, nullable=False)
    provider_state = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    content_json = Column(Text, nullable=False)
    submitted_at = timestamp()
    delivered_at = Column(Integer)
    error_detail = Column(Text)
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'task_id'], ['reminder_tasks.user_id', 'reminder_tasks.id']),
        CheckConstraint("provider_state IN ('PENDING','SENT','FAILED')"),
    )


class ProcessingHistory(Base):
    __tablename__ = 'processing_history'
    id = pk()
    user_id = user_column()
    receipt_id = Column(String)
    issue_id = Column(String)
    task_id = Column(String)
    trigger = Column(String, nullable=False)
    result = Column(String, nullable=False)
    reason_code = Column(String, nullable=False)
    detail_json = Column(Text, nullable=False, default='{}')
    before_json = Column(Text)
    after_json = Column(Text)
    processed_at = timestamp()
    __table_args__ = (
        ForeignKeyConstraint(['user_id', 'receipt_id'], ['event_receipts.user_id', 'event_receipts.id']),
        ForeignKeyConstraint(['user_id', 'issue_id'], ['issues.user_id', 'issues.id']),
        ForeignKeyConstraint(['user_id', 'task_id'], ['reminder_tasks.user_id', 'reminder_tasks.id']),
        Index('ix_history_issue', 'user_id', 'issue_id', 'processed_at'),
        Index('ix_history_receipt', 'user_id', 'receipt_id'),
    )