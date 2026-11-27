"""Public-family evidence only; synthetic offers are not SKU attestations."""
import pytest

from recommendation.public_evidence_agent import evidence_for_offers


CASES = [
    ('Microsoft 365 Family', 'microsoft-365-family-us', 'Microsoft 365 Family is a subscription for 1-6 people'),
    ('Office для дома и бизнеса 2024', 'microsoft-office-home-business-2024-tm', 'Единовременная покупка для 1 ПК или компьютера Mac'),
    ('Kaspersky Standard', 'kaspersky-standard-product', 'Расширенная защита, включающая Анти-Фишинг и Сетевой экран'),
    ('Dr.Web Security Space', 'drweb-security-space-product', 'Комплексная защита вашего компьютера от всех типов интернет-угроз'),
]


@pytest.mark.parametrize('name,source,quote', CASES)
def test_independent_public_family_reference(name, source, quote):
    result = evidence_for_offers([{'offer_id': 'synthetic', 'name': name.upper()}])
    ref = next(ref for ref in result['synthetic'] if ref['id'] == source)
    assert ref['excerpt'] == quote
    assert ref['url'].startswith('https://')
    assert ref['observed_at'] and ref['source_version'] and ref['document_updated'] is None
    assert 'пересечение' in ref['limitation'] and 'потребности' in ref['limitation']
    assert 'SKU' in ref['limitation']


@pytest.mark.parametrize('name', [
    'Microsoft 365 Personal', 'Office home and business 2021',
    'Office home and business 20240', 'notkaspersky standard',
    'Dr.Web Enterprise Security Suite', 'unrelated office protection',
])
def test_unrelated_names_do_not_get_new_sources(name):
    result = evidence_for_offers([{'offer_id': 'synthetic', 'name': name}])
    new_ids = {row[1] for row in CASES}
    assert not (new_ids & {ref['id'] for ref in result.get('synthetic', [])})


def test_region_and_security_caveats():
    refs = evidence_for_offers([{'offer_id': str(i), 'name': row[0]} for i, row in enumerate(CASES)])
    limitations = [refs[str(i)][0]['limitation'] for i in range(4)]
    assert 'США' in limitations[0] and 'регион' in limitations[0]
    assert 'Туркменистана' in limitations[1] and 'регион' in limitations[1]
    for text in limitations[2:]:
        assert 'Совместную установку' in text and 'совместимости' in text
