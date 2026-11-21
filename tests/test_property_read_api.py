"""GET /api/property/<id>：只读取回爬虫存的原始字段，不调大模型、不翻译。

日宜找房清理存量买房时用它取阳台面积和税费原值（detail-assets 会在缺标题时现场生成，不适合批量读）。
"""

import pytest

from app import create_app
from app.models import Property, db


@pytest.fixture
def app():
    app = create_app('testing')
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        prop = Property(task_id='t1', url='https://suumo.jp/ms/chuko/chiba/sc_chibashimidori/nc_20413980/',
                        data={'_property_type': '买卖-公寓塔楼',
                              '_source_url': 'https://suumo.jp/ms/chuko/chiba/sc_chibashimidori/nc_20413980/',
                              'detail.other_areas': [{'name': 'バルコニー面積', 'area': 11.2}],
                              'analysis.registration_tax': 70000, 'analysis.acquisition_tax': 280000})
        prop.translated = {'detail.other_areas': [{'name': '阳台面积', 'area': 11.2}]}
        db.session.add(prop)
        db.session.commit()
        app.config['PROP_ID'] = prop.id
    return app


def test_returns_original_fields(app):
    client = app.test_client()
    pid = app.config['PROP_ID']
    resp = client.get(f'/api/property/{pid}')

    assert resp.status_code == 200
    prop = resp.get_json()['property']
    assert prop['_id'] == pid
    assert prop['_url'].endswith('/nc_20413980/')
    assert prop['detail.other_areas'] == [{'name': 'バルコニー面積', 'area': 11.2}]  # 原文，不是译文
    assert prop['analysis.acquisition_tax'] == 280000


def test_missing_property_is_404(app):
    resp = app.test_client().get('/api/property/999999')
    assert resp.status_code == 404
