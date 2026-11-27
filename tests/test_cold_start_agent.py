import json
from types import SimpleNamespace

from recommendation.cold_start_agent import investigate
from recommendation.public_research import NANOCAD_PUBLIC_DOCS


def test_document_does_not_override_feed_availability_or_architecture():
    row: dict[str, str | None] = {'name':'Платформа nanoCAD (конфигурация Standart Mech)', 'offer_id':'17',
           'url':'https://store.softline.ru/nanosoft/example-17/', 'availability':'false', 'params':'[]'}
    cat = SimpleNamespace(by_vendor={'нанософт разработка':[row]}, metadata={'feed_date_as_supplied':'2026-11-26 11:45'})

    def model(_, payload):
        payload = json.loads(payload)
        return '{"source":"mech_product"}' if 'sources' in payload else '{"outcome":"mech","evidence":"e1"}'

    def fetch(url, timeout):
        assert url == NANOCAD_PUBLIC_DOCS['mech_product']
        return '<p>Профессиональная базовая САПР для машиностроительного профиля</p>'.encode()

    assert investigate('mechanical','B2B',None,cat,model,fetch)['status'] == 'no_eligible_offer'

    # Actual index always supplies the field; test the null used for missing XML attributes.
    row['availability'] = None
    assert investigate('mechanical','B2B',None,cat,model,fetch)['offer'] is None
    row['availability'] = 'true'
    assert investigate('mechanical','B2B','armv8',cat,model,fetch)['offer'] is None
    result = investigate('mechanical','B2B',None,cat,model,fetch)
    assert result['status'] == 'candidate'
    assert result['offer']['availability'] == 'true'
    assert result['snapshot_date'] == '2026-11-26 11:45'
    cat.metadata.clear()
    assert investigate('mechanical','B2B',None,cat,None,None)['status'] == 'catalog_snapshot_undated'
