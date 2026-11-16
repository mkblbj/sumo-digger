"""detail-assets 的 fields / translate 选项，以及部署核对用的 /api/version。

日宜找房要给 1,500 多条缺标题的房源补标题和标签。detail-assets 默认还会翻译整条物件、
生成三段介绍，调用量和耗时翻几倍；这里验证可以只生成要的字段、跳过翻译，
且不传参数时行为不变（草稿「重新生成 AI 文案」仍走默认）。
"""

import os
import subprocess

import pytest

from app import create_app
from app import routes
from app.models import Property, Settings, db


@pytest.fixture
def app():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        settings = Settings.get()
        settings.llm_api_key = 'k'
        db.session.commit()
        prop = Property(task_id='t1', url='https://suumo.jp/chintai/jnc_000110992013/?bc=100536194791',
                        data={'_property_type': '租房', 'basic.address': '千葉県千葉市稲毛区稲毛東６'})
        db.session.add(prop)
        db.session.commit()
        app.config['PROP_ID'] = prop.id
    return app


@pytest.fixture
def calls(monkeypatch):
    seen = {'generated': [], 'translated': 0}

    def fake_generate(prop, summary_type):
        seen['generated'].append(summary_type)
        return {'title': ['稲毛東６近站租房1K公寓'], 'summary': ['介绍'], 'tags': ['近车站']}[summary_type]

    class FakeTranslator:
        def __init__(self, llm_client=None):
            pass

        def translate_property(self, data):
            seen['translated'] += 1
            return None

    import app.services.translator as translator_module
    monkeypatch.setattr(routes, '_generate_property_summary_items', fake_generate)
    monkeypatch.setattr(translator_module, 'TranslatorService', FakeTranslator)
    return seen


def post_assets(app, body=None):
    client = app.test_client()
    url = f"/api/property/{app.config['PROP_ID']}/detail-assets"
    return client.post(url, json=body) if body is not None else client.post(url)


def test_default_keeps_old_behaviour(app, calls):
    res = post_assets(app)

    assert res.status_code == 200
    assert calls['generated'] == ['title', 'summary', 'tags']
    assert calls['translated'] == 1
    data = res.get_json()
    assert data['title_items'] == ['稲毛東６近站租房1K公寓']
    assert data['summary_items'] == ['介绍']


def test_only_title_and_tags_without_translation(app, calls):
    res = post_assets(app, {'fields': ['title', 'tags'], 'translate': False})

    assert res.status_code == 200
    assert calls['generated'] == ['title', 'tags']
    assert calls['translated'] == 0
    data = res.get_json()
    assert data['title_items'] == ['稲毛東６近站租房1K公寓']
    assert data['tag_items'] == ['近车站']
    assert data['summary_items'] == []
    assert data['property']['_meta']['url'] == 'https://suumo.jp/chintai/jnc_000110992013/?bc=100536194791', '调用方据此核对来源'


def test_unknown_field_is_rejected(app, calls):
    res = post_assets(app, {'fields': ['title', 'price']})

    assert res.status_code == 400
    assert calls['generated'] == []


@pytest.mark.parametrize('body', [['title'], 'title', 5])
def test_non_object_json_body_is_rejected(app, calls, body):
    res = post_assets(app, body)

    assert res.status_code == 400
    assert calls['generated'] == []


@pytest.mark.parametrize('flag', ['translate', 'force_translate'])
@pytest.mark.parametrize('value', ['false', 0, None])
def test_flags_must_be_boolean(app, calls, flag, value):
    res = post_assets(app, {'fields': ['title'], flag: value})

    assert res.status_code == 400, '不是布尔值时拒绝，免得悄悄多跑一次翻译'
    assert calls['translated'] == 0
    assert calls['generated'] == []


def test_empty_fields_is_rejected(app, calls):
    res = post_assets(app, {'fields': []})

    assert res.status_code == 400
    assert calls['generated'] == []


def test_malformed_json_is_rejected(app, calls):
    url = f"/api/property/{app.config['PROP_ID']}/detail-assets"
    res = app.test_client().post(url, data='{bad', content_type='application/json')

    assert res.status_code == 400
    assert calls['generated'] == []


def test_empty_json_body_and_non_json_body_keep_default(app, calls):
    url = f"/api/property/{app.config['PROP_ID']}/detail-assets"
    client = app.test_client()

    assert client.post(url, data='', content_type='application/json').status_code == 200
    assert client.post(url, data='a=1', content_type='application/x-www-form-urlencoded').status_code == 200
    assert calls['generated'] == ['title', 'summary', 'tags'] * 2


def test_version_reports_running_commit(app):
    res = app.test_client().get('/api/version')

    assert res.status_code == 200
    commit = res.get_json()['commit']
    root = os.path.dirname(os.path.dirname(os.path.abspath(routes.__file__)))
    expected = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True).stdout.strip()
    assert commit == expected


def test_version_unknown_when_code_is_not_the_repo_root(tmp_path):
    parent = tmp_path / 'other-repo'
    child = parent / 'sumo-digger'
    child.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(parent)], check=True)

    assert routes._running_commit(str(child)) == 'unknown', '代码目录不是仓库根时，不能拿别的仓库的提交冒充'
