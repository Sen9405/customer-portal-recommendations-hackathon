import json
from datetime import date
from types import SimpleNamespace

from recommendation.history_research import TOOLS, from_history, refresh_public_evidence
from recommendation.public_research import Evidence, PublicCandidate, ResearchResult
from recommendation.profile_pipeline import ProfilePipeline


def test_follow_on_evidence_counts_customers_not_transitions():
    rows = [('self', 'client', '2026-08-24', 'potato')]
    for user, items in [('peer-a', ('potato', 'carrot', 'potato', 'carrot')),
                        ('peer-b', ('potato', 'carrot')), ('peer-c', ('potato', 'carrot')),
                        ('same-basket-only', ('potato',))]:
        for j, item in enumerate(items):
            rows.append((f'{user}-{j}', user, f'2026-08-{3 + j * 7:02}', item))
    rows.append(('same-basket-only-0', 'same-basket-only', '2026-08-03', 'carrot'))
    pipeline = ProfilePipeline(rows, date(2026, 8, 31))
    first = pipeline.profile('client')['candidates'][0]
    assert first['item_id'] == 'carrot'
    assert first['evidence']['coorder'][0]['order_count'] == 1
    assert first['evidence']['sequential'] == [{'last_week_item':'potato', 'transition_count':4, 'other_client_count':3}]


def test_fixed_public_batch_and_history_conditionals():
    anchor = 'kaspersky-endpoint-security-windows'
    sid, surl, tid, turl = TOOLS[anchor]
    offers = {i:{'offer_id':i,'url':url,'name':'Public '+i,'availability':'true','params':'[]'}
              for i,url in [(sid,surl),(tid,turl)]}
    calls = []

    class Research:
        def run(self, a, *, allow_network):
            assert allow_network is True
            calls.append(a)
            ref = Evidence('https://example.test/vendor','Documented family relation','2026-11-27')
            return ResearchResult(a,'completed',(PublicCandidate('candidate','Public candidate','documented_integration','confirmed','documented_family_relation',(ref,)),))

    evidence = refresh_public_evidence(Research())
    assert calls == list(TOOLS)
    history = {'with_signal':{'train-source'},'unrelated':{'another'},'already':{'train-source','train-target'}}
    aliases = {'train-source':sid,'train-target':tid}
    rec = SimpleNamespace(by_id=offers,pipeline=SimpleNamespace(history=history),
                          _unique=lambda item: offers.get(aliases.get(item, '')),metadata={'feed_date_as_supplied':'2026-11-26'})
    titles = {'train-source':'Observed train purchase'}
    assert not from_history(rec,'with_signal',titles,'B2C',evidence)
    for user in ('unrelated','already','empty'):
        assert not from_history(rec,user,titles,'B2B',evidence)
    finding, = from_history(rec,'with_signal',titles,'B2B',evidence)
    assert finding['from'] == 'Observed train purchase' and finding['status'] == 'needs_check'
    assert finding['catalog_url'] == turl and finding['url'] == 'https://example.test/vendor'
    assert finding['source_fact'] == 'Documented family relation'
    assert finding['offer_checked_for_declared_scenario'] is False
    pending, = from_history(rec,'with_signal',titles,'B2B',{})
    assert pending['source_status'] == 'unavailable' and pending['url'] is None
    assert calls == list(TOOLS)
    offers[tid]['params'] = json.dumps([{'name':'Пользователь','value':'физлицо'}])
    assert not from_history(rec,'with_signal',titles,'B2B',evidence)
