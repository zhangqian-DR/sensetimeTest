"""数据库连接与会话管理。

使用嵌入式 SQLite（无需安装/启动外部数据库服务）。
启动时 init_db() 自动建表（幂等）；测试可使用独立的内存库。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

# SQLite 需要 check_same_thread=False，以便 FastAPI 多线程下复用连接
engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def init_db() -> None:
    """自动建表，幂等（CREATE TABLE IF NOT EXISTS）。"""
    from . import models  # noqa: F401  确保模型已注册
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖：提供请求级会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
