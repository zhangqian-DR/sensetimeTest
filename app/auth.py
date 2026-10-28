"""本地演示身份认证；Token值不得进入应用日志。"""
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, scheme_name='BearerAuth')


def resolve_user(settings, token):
    return settings.test_tokens.get(token)


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    token = credentials.credentials if credentials and credentials.scheme.lower() == 'bearer' else None
    user_id = resolve_user(request.app.state.settings, token)
    if user_id is None:
        raise HTTPException(status_code=401, detail='Valid local test user token required', headers={'WWW-Authenticate': 'Bearer'})
    return user_id


def demo_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    settings = request.app.state.settings
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise HTTPException(401, 'Demo administrator token required')
    if credentials.credentials != settings.demo_admin_token:
        raise HTTPException(403, 'Only the demo administrator may control global demonstration state')
    if settings.time_mode != 'simulated' or not settings.enable_demo_controls:
        raise HTTPException(403, 'Demonstration controls require simulated mode and explicit configuration')
    return 'demo-admin'