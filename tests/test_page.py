"""页面托管是新增后端入口，先验证静态资源不影响API鉴权。"""
from fastapi.testclient import TestClient
from app.config import load_settings
from app.main import create_app


def test_page_and_assets_are_served_from_application(tmp_path):
    settings=load_settings({'APP_DB_PATH':str(tmp_path/'page.db')})
    with TestClient(create_app(settings)) as client:
        response=client.get('/')
        assert response.status_code==200
        assert 'text/html' in response.headers['content-type']
        assert '/static/app.js' in response.text
        assert '/static/styles.css' in response.text
        assert client.get('/static/app.js').status_code==200
        assert client.get('/static/styles.css').status_code==200
        assert client.get('/issues').status_code==401


def test_static_mount_does_not_expose_project_files(tmp_path):
    settings=load_settings({'APP_DB_PATH':str(tmp_path/'page.db')})
    with TestClient(create_app(settings)) as client:
        assert client.get('/static/../config.py').status_code==404
        assert client.get('/static/%2e%2e/config.py').status_code==404