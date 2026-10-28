"""Explicit SQLite lifecycle; importing this module never creates a database."""
from pathlib import Path
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

Base = declarative_base()
SCHEMA_VERSION = '2'


class Database:
    def __init__(self, settings):
        self.settings = settings
        path = settings.db_path
        if path != ':memory:':
            Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        options = {'connect_args': {'check_same_thread': False, 'timeout': 10}}
        if path == ':memory:':
            options['poolclass'] = StaticPool
        self.engine = create_engine('sqlite:///' + path, **options)
        @event.listens_for(self.engine, 'connect')
        def configure(connection, _):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.execute('PRAGMA busy_timeout=10000')
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.close()
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self):
        from .models import AppMeta, User, UserSettings
        tables = inspect(self.engine).get_table_names()
        if tables and 'app_meta' not in tables:
            raise ValueError('Incompatible database schema; use a new database, old data is preserved')
        if 'app_meta' in tables:
            with self.session_factory() as session:
                version = session.get(AppMeta, 'schema_version')
                mode = session.get(AppMeta, 'time_mode')
                if version is None or version.value != SCHEMA_VERSION:
                    raise ValueError('Incompatible schema_version; use a new database')
                if mode is None or mode.value != self.settings.time_mode:
                    raise ValueError('Database time_mode does not match configured mode')
        Base.metadata.create_all(self.engine)
        with self.session_factory.begin() as session:
            if session.get(AppMeta, 'schema_version') is None:
                session.add_all([AppMeta(key='schema_version', value=SCHEMA_VERSION), AppMeta(key='time_mode', value=self.settings.time_mode)])
            for user_id in sorted({'user-a', 'user-b'} | set(self.settings.test_tokens.values())):
                if session.get(User, user_id) is None:
                    session.add(User(id=user_id, display_name=user_id))
                    session.flush()
                    session.add(UserSettings(user_id=user_id))

    def dispose(self):
        self.engine.dispose()


def create_database(settings):
    return Database(settings)