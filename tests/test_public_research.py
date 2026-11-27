from dataclasses import asdict
import json
import subprocess
from unittest.mock import patch

import pytest

from recommendation.public_research import ANCHORS, PublicResearch

INDEX = 'https://helpcenter.veeam.com/docs/one/userguide/index.html'
LEAF = 'https://helpcenter.veeam.com/docs/one/userguide/about_one.html'
LINK = '<a href="about_one.html">About Veeam ONE</a>'
POS = ('Veeam ONE enables real-time monitoring, business documentation and management '
       'reporting for Veeam Backup & Replication, Veeam Backup for Microsoft 365.')
NEG = 'Veeam ONE does not support monitoring Veeam Backup & Replication.'


def service(pages, **kwargs):
    calls = []

    def fetch(url, timeout):
        assert timeout > 0
        calls.append(url)
        result = pages[url]
        if isinstance(result, Exception):
            raise result
        return result.encode()

    return PublicResearch(fetch=fetch, **kwargs), calls


def test_opt_in_and_public_ids_only():
    r, calls = service({})
    assert 'veeam-backup-replication' in ANCHORS
    assert r.run('veeam-backup-replication').outcome == 'disabled'
    assert r.run('https://not-a-public-id.example/', allow_network=True).outcome == 'unsupported_anchor'
    assert r.run(['customer', 'history'], allow_network=True).outcome == 'unsupported_anchor'
    assert r.run('veeam-backup-replication', allow_network='true').outcome == 'disabled'
    assert calls == []


def test_discover_link_then_verify_literal_quote_and_cache_only_public_pages():
    r, calls = service({INDEX: LINK, LEAF: '<p>Vendor overview. ' + POS.replace('&', '<b>&amp;</b>') + '</p>'})
    result = r.run('veeam-backup-replication', allow_network=True)
    assert result.outcome == 'completed'
    candidate, = result.candidates
    assert (candidate.product_id, candidate.product_name, candidate.relation, candidate.status) == (
        'veeam-one', 'Veeam ONE', 'monitoring', 'confirmed')
    evidence, = candidate.evidence
    assert evidence.url == LEAF and evidence.quote == POS and evidence.retrieved_at
    assert result.trace[1].stage == 'verify'
    assert calls == [INDEX, LEAF]
    result2 = r.run('veeam-backup-replication', allow_network=True)
    assert calls == [INDEX, LEAF]
    assert all(step.outcome == 'cached' for step in result2.trace)
    assert json.loads(json.dumps(asdict(result)))['candidates'][0]['status'] == 'confirmed'
    assert r.run('veeam-backup-replication').candidates == ()


def test_off_scope_navigation_and_irrelevant_links_are_not_discovery():
    links = ['http://helpcenter.veeam.com/docs/one/userguide/about_one.html',
             '//other.example/docs/one/userguide/about_one.html',
             '//helpcenter.veeam.com@other.example/docs/one/userguide/about_one.html',
             '//helpcenter.veeam.com:443/docs/one/userguide/about_one.html',
             '/unconfigured/about_one.html', 'about_one.html?identifier=123',
             'about_one.html#fragment', '%61bout_one.html', '../userguide/about_one.html',
             'https://helpcenter.veeam.com/docs/one/userguide/other.html']
    index = ''.join(f'<a href="{link}">About Veeam ONE</a>' for link in links)
    index += '<script>\'<a href="about_one.html">About Veeam ONE</a>\'</script>'
    index += '<a href="about_one.html">Generic navigation</a>'
    r, calls = service({INDEX: index})
    result = r.run('veeam-backup-replication', allow_network=True)
    assert result.outcome == 'no_findings' and result.candidates == ()
    assert calls == [INDEX]


@pytest.mark.parametrize(('leaf', 'status', 'quote'), [
    ('<p>' + NEG + '</p>', 'rejected', NEG),
    ('<script>' + POS + '</script><p>Veeam ONE is a solution.</p>', 'needs_check', None),
    ('<p>Contrary to the claim that ' + POS + '</p>', 'needs_check', None),
    ('<p>' + POS + '</p><p>' + NEG + '</p>', 'needs_check', None),
    (TimeoutError(), 'needs_check', None),
])
def test_evidence_excludes_absence_synthetic_and_ambiguous_quotes(leaf, status, quote):
    r, _ = service({INDEX: LINK, LEAF: leaf})
    candidate, = r.run('veeam-backup-replication', allow_network=True).candidates
    assert candidate.status == status
    if quote:
        assert candidate.evidence[0].quote == quote
    else:
        assert not candidate.evidence


def test_failed_index_yields_no_invented_candidate_and_is_not_cached():
    r, calls = service({INDEX: TimeoutError()})
    for _ in range(2):
        result = r.run('veeam-backup-replication', allow_network=True)
        assert (result.outcome, result.candidates, result.trace[0].outcome) == (
            'source_unavailable', (), 'unavailable')
    assert calls == [INDEX, INDEX]


def test_production_transport_disables_proxy_redirects_and_respects_body_bound():
    r = PublicResearch()
    with patch('recommendation.public_research.subprocess.run') as run:
        run.return_value = subprocess.CompletedProcess([], 0, b'HTTP/2 302\r\nLocation: https://other.example/x\r\n\r\n')
        result = r.run('veeam-backup-replication', allow_network=True)
        assert result.outcome == 'source_unavailable'
        cmd = run.call_args.args[0]
        assert cmd[1] == '-q' and '--location' not in cmd
        assert cmd[cmd.index('--noproxy') + 1] == '*'
        assert cmd[cmd.index('--proto') + 1] == '=https'
        assert '--max-filesize' in cmd and run.call_args.kwargs['timeout'] > 0
        assert set(run.call_args.kwargs['env']) == {'PATH'}
        assert run.call_count == 1


def test_reject_oversize_injected_body_and_expire_cache(monkeypatch):
    import recommendation.public_research as m

    now = 1
    monkeypatch.setattr(m, 'monotonic', lambda: now)
    monkeypatch.setattr(m, 'MAX_BYTES', 512)
    pages = {INDEX: LINK, LEAF: '<p>' + POS + '</p>'}
    r, calls = service(pages, cache_ttl=2)
    assert r.run('veeam-backup-replication', allow_network=True).candidates[0].status == 'confirmed'
    now = 4
    pages[LEAF] = 'x' * 513
    assert r.run('veeam-backup-replication', allow_network=True).candidates[0].status == 'needs_check'
    assert calls == [INDEX, LEAF, INDEX, LEAF]


def test_kaspersky_preserves_release_limit_in_evidence():
    root = 'https://support.kaspersky.com/kes-for-windows/12.10'
    leaf = root + '/225086'
    quote = ('The EDR Optimum component as part of Kaspersky Endpoint Security supports interaction '
             'with the Kaspersky Endpoint Detection and Response Optimum 2.0 solution. Interaction '
             'with Kaspersky Endpoint Detection and Response Optimum version 1.0 is not supported.')
    r, calls = service({root: '<a href="/kes-for-windows/12.10/225086">Integration of the built-in '
                              'agent with EDR Optimum / EDR Expert</a>', leaf: '<p>' + quote + '</p>'})
    result = r.run('kaspersky-endpoint-security-windows', allow_network=True)
    candidate, = result.candidates
    assert (candidate.product_id, candidate.relation, candidate.status) == (
        'kaspersky-edr-optimum', 'documented_integration', 'confirmed')
    assert candidate.evidence[0].quote == quote
    assert calls == [root, leaf]
