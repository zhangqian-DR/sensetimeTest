"""领域模型（字段由业务规则反推）。

金额统一 Numeric(12,2)，服务层用 Decimal 处理；时间为带时区 DateTime。
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from .database import Base


class AppUser(Base):
    """账单归属用户。"""
    __tablename__ = "app_users"
    id = Column(String, primary_key=True)
    name = Column(String)


class Merchant(Base):
    __tablename__ = "merchants"
    id = Column(String, primary_key=True)
    name = Column(String)


class Txn(Base):
    """交易：疑似重复检测的基本输入。"""
    __tablename__ = "txns"
    id = Column(Integer, primary_key=True, autoincrement=True)
    tx_no = Column(String, unique=True, nullable=False, index=True)  # 外部交易编号
    user_id = Column(String, index=True, nullable=False)
    merchant_id = Column(String, index=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    occurred_at = Column(DateTime, nullable=False)


class Refund(Base):
    """退款：以 requested_at（申请确认时间）为 72h 计时起点。"""
    __tablename__ = "refunds"
    id = Column(Integer, primary_key=True, autoincrement=True)
    refund_no = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(String, index=True, nullable=False)
    tx_no = Column(String, nullable=True)
    expected_amount = Column(Numeric(12, 2), nullable=False)
    requested_at = Column(DateTime, nullable=False)


class RefundArrival(Base):
    """退款到账：可分多笔、可乱序、可先于申请到达。dedup_key 去重。"""
    __tablename__ = "refund_arrivals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    refund_no = Column(String, index=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    arrived_at = Column(DateTime, nullable=False)
    dedup_key = Column(String, unique=True, nullable=False)


class Issue(Base):
    """问题：系统核心产物。type=DUPLICATE / REFUND。"""
    __tablename__ = "issues"
    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_key = Column(String, unique=True, nullable=False, index=True)
    issue_type = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    user_id = Column(String, index=True, nullable=False)
    reason = Column(String)
    cluster_id = Column(String, index=True, nullable=True)  # 三笔两两满足时的关联
    refund_no = Column(String, nullable=True)
    # 提醒调度相关
    snooze_until = Column(DateTime, nullable=True)
    nudge_count = Column(Integer, default=0, nullable=False)
    first_reminder_at = Column(DateTime, nullable=True)
    last_reminder_at = Column(DateTime, nullable=True)
    # 结案相关
    resolution = Column(String, nullable=True)
    resolution_basis = Column(String, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    # 时间戳
    created_at = Column(DateTime)
    updated_at = Column(DateTime)


class IssueTxn(Base):
    """问题-交易关联：重复类问题关联的具体交易。"""
    __tablename__ = "issue_txns"
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), index=True)
    tx_no = Column(String, index=True)
    __table_args__ = (UniqueConstraint("issue_id", "tx_no", name="uq_issue_txn"),)


class Reminder(Base):
    """提醒（outbox）：写入即视为"已发送"，可查询，不真实推送。"""
    __tablename__ = "reminders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), index=True)
    kind = Column(String, nullable=False)  # FIRST / NUDGE1 / NUDGE2 / SNOOZE
    scheduled_at = Column(DateTime)
    sent_at = Column(DateTime)
    content = Column(Text)
    # 同一问题的同一种提醒只允许一条，保证重复 tick/重放不重复发
    __table_args__ = (UniqueConstraint("issue_id", "kind", name="uq_issue_reminder_kind"),)


class ActionHistory(Base):
    """处理历史：完整审计与结案依据。"""
    __tablename__ = "action_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    at = Column(DateTime, nullable=False)
    detail = Column(Text)


class UserSettings(Base):
    """用户级设置：暂停开关。"""
    __tablename__ = "user_settings"
    user_id = Column(String, primary_key=True)
    paused = Column(Boolean, default=False, nullable=False)
    paused_at = Column(DateTime, nullable=True)
    resumed_at = Column(DateTime, nullable=True)


class AppMeta(Base):
    """key-value 存储：用于持久化 simulated 虚拟时间，重启读取已保存时间。"""
    __tablename__ = "app_meta"
    key = Column(String, primary_key=True)
    value = Column(String)
