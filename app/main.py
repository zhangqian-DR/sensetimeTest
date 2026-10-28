"""FastAPI应用工厂；导入模块不创建或修改数据库。"""
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .api import router
from .auth import resolve_user
from .scheduler import BackgroundScheduler

logger = logging.getLogger(__name__)


def create_app(settings=None, database=None, service=None):
    if settings is None:
        from .config import load_settings
        settings = load_settings()

    @asynccontextmanager
    async def lifespan(application):
        nonlocal database, service
        if database is None:
            from .database import create_database
            database = create_database(settings)
        database.initialize()
        # 管理员全局控制必须真正输出审计，不能被默认WARNING级别静默过滤。
        audit = logging.getLogger('bill_followup.audit')
        old_level, old_propagate = audit.level, audit.propagate
        audit_handler = logging.StreamHandler()
        audit_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        audit.addHandler(audit_handler)
        audit.setLevel(logging.INFO)
        audit.propagate = False
        try:
            if service is None:
                from .clock import make_clock
                from .service import BillingService
                service = BillingService(database, make_clock(database, settings))
            application.state.database = database
            application.state.service = service
            service.recover()
            scheduler = BackgroundScheduler(service.evaluate, settings.check_interval_seconds)
            application.state.scheduler = scheduler
            scheduler.start()
            try:
                yield
            finally:
                scheduler.stop()
        finally:
            audit.removeHandler(audit_handler)
            audit_handler.close()
            audit.setLevel(old_level)
            audit.propagate = old_propagate
            database.dispose()

    application = FastAPI(title='Bill Follow-up — simulated demonstration', version='0.2.0', lifespan=lifespan,
                          description='CNY / Asia-Shanghai. Notifications and identities are simulated. Use a user test token for business actions; demo administrator token only for global controls.')
    application.state.settings = settings
    application.include_router(router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # 正常业务校验在留存后执行；这里补记JSON解析失败或请求体结构错误。
        authorization = request.headers.get('authorization', '')
        token = authorization[7:] if authorization.lower().startswith('bearer ') else None
        user = resolve_user(settings, token)
        is_business_write = request.method in ('POST', 'PUT') and not request.url.path.startswith('/demo/')
        if is_business_write and user is None:
            return JSONResponse(status_code=401, content={'detail': 'Valid local test user token required'}, headers={'WWW-Authenticate': 'Bearer'})
        if user and is_business_write:
            raw = (await request.body())[:16384].decode('utf-8', errors='replace')
            try:
                await run_in_threadpool(application.state.service.receive, user, 'INVALID_INPUT', {'raw_body': raw, 'validation_error': 'Invalid JSON or body shape'})
            except Exception as audit_error:
                if type(audit_error).__name__ != 'DomainError':
                    logger.exception('Unable to retain invalid business input')
        return JSONResponse(status_code=422, content={'detail': 'Invalid JSON or request body; provide the documented object fields'})

    return application


app = create_app()