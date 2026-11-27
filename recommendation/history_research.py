"""Local history → conditional hypothesis; external lookup is never history-driven."""
from dataclasses import asdict
import re

from recommendation.contest_recommender import _offer_audience, documented_demo_upgrade


TOOLS = {
    'kaspersky-endpoint-security-windows': ('4853', 'https://store.softline.ru/kaspersky/kl4867lakfs-4853/',
                                            '298271', 'https://store.softline.ru/kaspersky/kl4708rakfs-298271/'),
    'cryptopro-csp-server-upgrade': ('25267', 'https://store.softline.ru/kripto-pro/-25267/',
                                     '197895', 'https://store.softline.ru/kripto-pro/-197895/'),
}


def refresh_public_evidence(research):
    """Fixed public batch, deliberately cannot see a customer or catalog."""
    return {anchor: asdict(research.run(anchor, allow_network=True)) for anchor in TOOLS}


def prioritize_examples(data, evidence):
    """Put one real matching example first for the closed demo; never change rankings."""
    if data.contest is None:
        return
    for index, option in enumerate(data.options):
        user = data.handles[option['id']]
        group = (data.pipeline.customers.get(user) or {}).get('group')
        if from_history(data.contest, user, data.names, group, evidence):
            data.options.insert(0, data.options.pop(index))
            break


def from_history(contest, user, names, audience, evidence):
    """Return local hypotheses, not assertions about installed software or need."""
    if audience != 'B2B':
        return []
    seen = contest.pipeline.history.get(user, ())
    owned = {offer['offer_id'] for item in seen if (offer := contest._unique(item))}
    titles = [names.get(item, '') for item in seen]
    findings = []
    for anchor, (source_id, source_url, target_id, target_url) in TOOLS.items():
        source = contest.by_id.get(source_id)
        target = contest.by_id.get(target_id)
        if (source_id not in owned or target_id in owned or not source or source['url'] != source_url
                or not target or target['url'] != target_url or _offer_audience(target, audience, None) is None):
            continue
        upgrade = anchor == 'cryptopro-csp-server-upgrade'
        if upgrade and any(re.search(r'(?:криптопро|cryptopro).*\bcsp\b.*\b5[.,]0\b', t, re.I) for t in titles):
            continue
        if not upgrade and any(re.search(r'kaspersky.*(?:\bedr\b|endpoint detection and response)', t, re.I) for t in titles):
            continue
        observed = next((names.get(i) for i in seen if (o := contest._unique(i)) and o['offer_id'] == source_id and names.get(i)), None)
        public = evidence.get(anchor, {})
        candidate = next((c for c in public.get('candidates', ()) if c['status'] in ('confirmed', 'rejected')), None)
        refs = candidate['evidence'] if candidate else ()
        card = documented_demo_upgrade(contest, public) if upgrade and public else None
        condition = ('Если ещё используется CSP 4.0 на Windows и нужен переход на 5.0, проверьте действующую серверную лицензию и условия производителя.'
                     if upgrade else 'Если нужна функция EDR и она ещё не входит в имеющуюся лицензию, проверьте версию Windows-компонента, право на дополнение и точный артикул. История этого не доказывает.')
        findings.append({'from': observed or source['name'], 'to': target['name'], 'offer_id': target_id,
                         'customer_goal': 'Перейти на новую версию' if upgrade else 'Выявлять и расследовать атаки',
                         'relation': 'Возможное обновление' if upgrade else 'Возможное дополнение EDR',
                         'status': 'rejected' if candidate and candidate['status'] == 'rejected' else 'needs_check',
                         'fact': condition, 'source_fact': ' '.join(r['quote'] for r in refs),
                         'url': refs[0]['url'] if refs else None, 'catalog_url': target_url,
                         'snapshot_date': contest.metadata.get('feed_date_as_supplied'),
                         'offer_checked_for_declared_scenario': bool(card),
                         'source_status': candidate['status'] if candidate else 'unavailable'})
    return findings