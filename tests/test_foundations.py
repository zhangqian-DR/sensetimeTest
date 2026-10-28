import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError


def settings_for(tmp_path, **overrides):
    from app.config import load_settings
    env = {'APP_DB_PATH': str(tmp_path / 'test.db')}
    env.update(overrides)
    return load_settings(env)


@pytest.mark.parametrize('env', [
    {'APP_TIME_MODE': 'typo'},
    {'APP_CHECK_INTERVAL_SECONDS': '-1'},
    {'APP_SIMULATED_START': '2026-10-28T14:00:00'},
    {'APP_TIMEZONE': 'UTC'},
])
def test_invalid_configuration_rejected(env):
    from app.config import load_settings
    with pytest.raises(ValueError):
        load_settings(env)


def test_twelve_tables_and_seed_users(tmp_path):
    from app.database import create_database
    from app.models import User
    db = create_database(settings_for(tmp_path))
    db.initialize()
    assert len(inspect(db.engine).get_table_names()) == 12
    with db.session_factory() as session:
        assert set(session.scalars(select(User.id))) == {'user-a', 'user-b'}
    db.dispose()


def test_clock_persists_and_rejects_backwards(tmp_path):
    from app.database import create_database
    from app.clock import make_clock
    cfg = settings_for(tmp_path)
    db = create_database(cfg)
    db.initialize()
    clock = make_clock(db, cfg)
    target = clock.now_ms() + 86400000
    assert clock.advance_to(target) == target
    db.dispose()
    reopened = create_database(cfg)
    reopened.initialize()
    assert make_clock(reopened, cfg).now_ms() == target
    with pytest.raises(ValueError):
        make_clock(reopened, cfg).advance_to(target - 1)
    reopened.dispose()


def test_database_mode_mismatch_rejected(tmp_path):
    from app.database import create_database
    db = create_database(settings_for(tmp_path))
    db.initialize()
    db.dispose()
    other = create_database(settings_for(tmp_path, APP_TIME_MODE='real'))
    with pytest.raises(ValueError, match='mode'):
        other.initialize()
    other.dispose()


def receipt(user, event_id):
    from app.models import EventReceipt
    return EventReceipt(user_id=user, event_id=event_id, event_type='TXN', payload_json='{}', payload_hash='hash', is_canonical=True)


def test_transaction_unique_scoped_and_cross_user_receipt_fk(tmp_path):
    from app.database import create_database
    from app.models import Transaction
    db = create_database(settings_for(tmp_path))
    db.initialize()
    with db.session_factory.begin() as session:
        a, b = receipt('user-a', 'e'), receipt('user-b', 'e')
        session.add_all([a, b])
        session.flush()
        session.add_all([
            Transaction(user_id='user-a', tx_no='same', merchant_id='shop', amount_fen=3000, occurred_at=1, source_receipt_id=a.id),
            Transaction(user_id='user-b', tx_no='same', merchant_id='shop', amount_fen=3000, occurred_at=1, source_receipt_id=b.id),
        ])
        aid = a.id
    with pytest.raises(IntegrityError):
        with db.session_factory.begin() as session:
            session.add(Transaction(user_id='user-b', tx_no='bad', merchant_id='shop', amount_fen=3000, occurred_at=1, source_receipt_id=aid))
    db.dispose()


def test_canonical_receipt_unique_but_replays_retained(tmp_path):
    from app.database import create_database
    db = create_database(settings_for(tmp_path))
    db.initialize()
    with db.session_factory.begin() as session:
        session.add(receipt('user-a', 'e'))
    replay = receipt('user-a', 'e')
    replay.is_canonical = False
    with db.session_factory.begin() as session:
        session.add(replay)
    with pytest.raises(IntegrityError):
        with db.session_factory.begin() as session:
            session.add(receipt('user-a', 'e'))
    db.dispose()


def test_real_clock_cannot_be_advanced(tmp_path):
    from app.database import create_database
    from app.clock import make_clock
    cfg = settings_for(tmp_path, APP_TIME_MODE='real')
    db = create_database(cfg)
    db.initialize()
    with pytest.raises(ValueError):
        make_clock(db, cfg).advance_to(0)
    db.dispose()

def test_only_one_active_reminder_per_issue(tmp_path):
    from app.database import create_database
    from app.models import Issue, ReminderTask
    db = create_database(settings_for(tmp_path))
    db.initialize()
    with db.session_factory.begin() as session:
        issue = Issue(user_id='user-a', issue_type='DUPLICATE', identity_key='pair', reason='suspected')
        session.add(issue)
        session.flush()
        issue_id = issue.id
        session.add(ReminderTask(user_id='user-a', issue_id=issue_id, kind='INITIAL', task_key='INITIAL', due_at=1))
    with pytest.raises(IntegrityError):
        with db.session_factory.begin() as session:
            session.add(ReminderTask(user_id='user-a', issue_id=issue_id, kind='USER_SCHEDULED', task_key='USER:1', due_at=2))
    db.dispose()


def test_duplicate_issue_cannot_claim_verified_refund(tmp_path):
    from app.database import create_database
    from app.models import Issue
    db = create_database(settings_for(tmp_path))
    db.initialize()
    with pytest.raises(IntegrityError):
        with db.session_factory.begin() as session:
            session.add(Issue(user_id='user-a', issue_type='DUPLICATE', identity_key='pair', reason='suspected', status='RESOLVED', resolution_basis='FULL_REFUND_VERIFIED', resolved_at=1))
    db.dispose()
