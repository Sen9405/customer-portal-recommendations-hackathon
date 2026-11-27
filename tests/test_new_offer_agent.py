import json
from types import SimpleNamespace
import unittest

from recommendation.new_offer_agent import paths_for, propose
from recommendation.public_research import PublicResearch
from recommendation.v5_new_offer_eval import coverage


class NewOfferTests(unittest.TestCase):
    def setUp(self):
        self.anchor = 'kaspersky-edr-current-requirements'
        self.source = {'offer_id':'4853','name':'Public source','url':'https://store.softline.ru/source/'}
        self.target = {'offer_id':'new','name':'Kaspersky EDR для бизнеса Оптимальный',
                       'url':'https://store.softline.ru/new/','availability':'true','params':'[]'}
        self.rec = SimpleNamespace(pipeline=SimpleNamespace(history={'secret-user':{'secret-item'}}),
            _unique=lambda i:self.source if i=='secret-item' else None, items={}, by_id={'new':self.target}, metadata={})
        self.facts = {self.anchor:{'candidates':[{'status':'confirmed','evidence':[{'url':'https://support.example/doc','quote':'Exact citation'}]}]}}
        self.calls = []

    def model(self, _, payload):
        self.assertNotIn('secret-', payload)
        self.calls.append(json.loads(payload))
        return json.dumps({'tool':self.anchor} if len(self.calls)==1 else {'candidate':'c1'})

    def test_runs_tools_and_marks_new_offer_conditional(self):
        res = propose(self.rec,'secret-user',[],'B2B',self.facts,self.model)
        self.assertEqual((res['status'],res['candidate']['offer_id']),('needs_check','new'))
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[1]['quote'],'Exact citation')

    def test_stops_when_evidence_or_new_offer_missing(self):
        self.assertEqual(propose(self.rec,'secret-user',[],'B2B',{},self.model)['status'],'source_not_confirmed')
        self.calls.clear()
        self.assertEqual(propose(self.rec,'secret-user',[self.target],'B2B',self.facts,self.model)['status'],'no_new_offer')
        self.assertEqual(len(self.calls),1)
        self.calls.clear()
        self.assertEqual(propose(self.rec,'secret-user',[],'B2C',self.facts,self.model)['status'],'no_signal')
        self.assertFalse(self.calls)

    def test_expanded_rule_requires_source_audience_identity_and_verified_pool(self):
        tool = 'kes-standard-to-advanced'
        vendor = '«Лаборатория Касперского»'
        self.source.update(offer_id='std', name='Kaspersky Endpoint Security для бизнеса Стандартный', vendor=vendor)
        self.target.update(name='Kaspersky Endpoint Security для бизнеса Расширенный', vendor=vendor)
        renewal = dict(self.target, offer_id='renew', name=self.target['name'] + ' (Продление)')
        unmapped = dict(self.target, offer_id='unmapped')
        calls = []
        self.rec.by_vendor = {vendor.casefold(): [self.target, renewal, unmapped]}
        self.assertFalse(paths_for(self.rec, 'secret-user', [], 'B2B')[2])
        self.assertFalse(paths_for(self.rec, 'secret-user', [], 'B2C', expanded=True)[2])
        mapping = {'new': 4, 'renew': 5}
        inputs = (self.rec, 'secret-user', [1, 2, 3], {'secret-item'}, 'B2B', mapping)
        self.assertFalse(coverage(*inputs, {}, expanded=True)[1])
        facts = {tool: self.facts[self.anchor]}
        offers, allowed, _, _ = coverage(*inputs, facts, expanded=True)
        self.assertEqual(allowed, {tool: {'new'}})

        def model(_, payload):
            self.assertNotIn('secret-', payload)
            calls.append(json.loads(payload))
            return json.dumps({'tool': tool} if len(calls) == 1 else {'candidate': 'c1'})

        result = propose(self.rec, 'secret-user', offers, 'B2B', facts, model,
                         expanded=True, allowed_by_tool=allowed)
        self.assertEqual(result['candidate']['offer_id'], 'new')
        self.assertEqual(list(calls[1]['public_offers']), ['c1'])
        self.rec.pipeline.history['secret-user'].add('owned-advanced')
        self.rec._unique = lambda i: self.source if i == 'secret-item' else self.target
        self.assertFalse(coverage(*inputs, facts, expanded=True)[1])

    def test_fixed_primary_page_reads_visible_div_not_script_or_hidden_text(self):
        tool = 'kes-standard-to-advanced'
        quote = ('Включает в себя все возможности и преимущества Kaspersky Endpoint Security для бизнеса '
                 'Стандартный, а также дополнительные возможности и технологии защиты.')
        for markup, status in ((f'<div>{quote}<br>другой текст</div>', 'confirmed'),
                               (f'<script>{quote}</script><div hidden>{quote}</div>', 'needs_check')):
            result = PublicResearch(fetch=lambda _url, _timeout: markup.encode()).run(tool, allow_network=True)
            self.assertEqual(result.candidates[0].status, status)
            self.assertEqual(len(result.trace), 1)
            if status == 'confirmed':
                self.assertEqual(result.candidates[0].evidence[0].quote, quote)
