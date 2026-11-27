"""Opt-in public documentation research, independent of catalog and model.

    service = PublicResearch()  # reuse to cache public pages for 15 minutes
    result = service.run('veeam-backup-replication', allow_network=True)
    payload = dataclasses.asdict(result)

Only pass a reviewed public product anchor ID from ANCHORS, never a history,
customer data, an offer ID, a URL, or a search query. Calls use only the static
vendor configuration. No private/session-dependent results are cached. The
caller may match returned product families to offers *locally*, but this module
makes no SKU, entitlement, compatibility, availability or customer-need claims.
`confirmed`/`rejected` refer only to the documented public family-level relation.
A `needs_check` candidate requires an actual discovered link; inability to find
one produces no candidate. This is current evidence, not a historical feature.

Production transport requires curl >= 8.4 (streaming max-filesize); no proxies or
redirects are used. An injected fetch is for trusted offline tests only and must
honor the supplied timeout. Treat all returned source text as untrusted plain
text; never render it as HTML or pass it to a model as instructions.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import math
import os
import re
import subprocess
from threading import Lock
from time import monotonic
from types import MappingProxyType
from typing import Callable, Literal
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 2 * 1024 * 1024
MAX_URL = 250
MAX_QUOTE = 600
MAX_LINKS = 4096
MAX_BLOCKS = 4096
MAX_CACHE_PAGES = 24

EvidenceStatus = Literal['confirmed', 'needs_check', 'rejected']
RunOutcome = Literal['disabled', 'unsupported_anchor', 'source_unavailable', 'no_findings', 'completed']


@dataclass(frozen=True)
class Evidence:
    url: str
    quote: str
    retrieved_at: str


@dataclass(frozen=True)
class PublicCandidate:
    product_id: str
    product_name: str
    relation: str
    status: EvidenceStatus
    reason: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class TraceStep:
    stage: Literal['discover', 'verify']
    url: str
    outcome: Literal['fetched', 'cached', 'unavailable', 'budget_exhausted']


@dataclass(frozen=True)
class ResearchResult:
    anchor_id: str | None
    outcome: RunOutcome
    candidates: tuple[PublicCandidate, ...] = ()
    trace: tuple[TraceStep, ...] = ()


@dataclass(frozen=True)
class _Tool:
    entry: str
    documents: tuple[str, ...]
    link_label: re.Pattern[str]
    product_id: str
    product_name: str
    relation: str
    supports: re.Pattern[str]
    contradicts: re.Pattern[str]
    direct: bool = False


# Static tool configuration, not a fact store. Most rules discover a leaf
# via a reviewed index link; experimental rules verify fixed primary pages.
_TOOLS = {
    'kes-standard-to-advanced': _Tool(
        'https://www.kaspersky.ru/small-to-medium-business-security/endpoint-advanced', (), re.compile(r'(?!)'),
        'kes-advanced', 'Kaspersky Endpoint Security для бизнеса Расширенный', 'upgrade',
        re.compile(r'Включает в себя все возможности и преимущества Kaspersky Endpoint Security для бизнеса Стандартный, а также дополнительные возможности и технологии защиты\.', re.I),
        re.compile(r'не включает все возможности.*Стандартный', re.I), direct=True,
    ),
    'm365-personal-to-family': _Tool(
        'https://support.microsoft.com/en-us/accounts-billing/subscriptions/switch-between-microsoft-365-subscriptions', (), re.compile(r'(?!)'),
        'm365-family', 'Microsoft 365 Family', 'subscription_switch',
        re.compile(r'you can upgrade from Microsoft 365 Personal to Family, Premium, or Pro to share benefits with others', re.I),
        re.compile(r'cannot (?:switch|upgrade) from Microsoft 365 Personal to Family', re.I), direct=True,
    ),
    'windows11-home-to-pro': _Tool(
        'https://support.microsoft.com/en-us/windows/deployment/install-upgrade/upgrade-windows-home-to-windows-pro', (), re.compile(r'(?!)'),
        'windows11-pro', 'Windows 11 Pro', 'upgrade',
        re.compile(r'The best way to upgrade from Windows 11 Home to Windows 11 Pro is through the Microsoft Store\.', re.I),
        re.compile(r'cannot upgrade from Windows 11 Home to Windows 11 Pro', re.I), direct=True,
    ),
    'kaspersky-standard-to-premium': _Tool(
        'https://support.kaspersky.com/kaspersky-for-windows/21.27/licensing/13994', (), re.compile(r'(?!)'),
        'kaspersky-premium', 'Kaspersky Premium', 'edition_change',
        re.compile(r'When renewing the license, you can continue using Kaspersky Standard or Plus, or you can buy Kaspersky Premium to ensure maximum protection\.', re.I),
        re.compile(r'cannot (?:switch|upgrade) from Kaspersky Standard to Kaspersky Premium', re.I), direct=True,
    ),
    'kaspersky-edr-current-requirements': _Tool(
        'https://support.kaspersky.com/kedr-optimum/4.0/216855',
        ('https://support.kaspersky.com/kedr-optimum/4.0/216930',),
        re.compile(r'Activating the application|Application activation|Activating Kaspersky Endpoint Detection and Response Optimum', re.I),
        'kaspersky-edr-optimum-4', 'Kaspersky EDR Optimum 4.0', 'documented_integration',
        re.compile(r'Separately but in addition to a previously purchased license to use an EPP application\.', re.I),
        re.compile(r'Cannot be activated separately in addition to an EPP application\.', re.I),
    ),
    'cryptopro-csp-server-upgrade': _Tool(
        'https://cryptopro.ru/order/',
        ('https://cryptopro.ru/order/cat/csp_all_positions',),
        re.compile(r'(?:СКЗИ )?КриптоПро CSP \(все позиции\)', re.I),
        'cryptopro-csp-5-server-upgrade', 'КриптоПро CSP 5.0 — обновление на сервере', 'upgrade',
        re.compile(r'Лицензия на обновление СКЗИ "КриптоПро CSP" до версии 5\.0 на сервере', re.I),
        re.compile(r'Обновление СКЗИ "КриптоПро CSP" до версии 5\.0 на сервере не предоставляется', re.I),
    ),
    'veeam-backup-replication': _Tool(
        'https://helpcenter.veeam.com/docs/one/userguide/index.html',
        ('https://helpcenter.veeam.com/docs/one/userguide/about_one.html',),
        re.compile(r'About Veeam ONE', re.I),
        'veeam-one', 'Veeam ONE', 'monitoring',
        re.compile(r'(?:^|(?<=\. ))Veeam ONE enables real-time monitoring, business documentation and management '
                   r'reporting for Veeam Backup & Replication(?:, [^.]{1,430})?\.', re.I),
        re.compile(r'^Veeam ONE does not support monitoring Veeam Backup & Replication\.', re.I),
    ),
    'microsoft-sql-server': _Tool(
        'https://learn.microsoft.com/en-us/ssms/',
        ('https://learn.microsoft.com/en-us/ssms/sql-server-management-studio-ssms',),
        re.compile(r'What is SSMS\?', re.I),
        'microsoft-ssms', 'SQL Server Management Studio (SSMS)', 'administration',
        re.compile(r'^(?:SQL Server Management Studio \(SSMS\) is an integrated environment for '
                   r'managing any SQL infrastructure\. )?Use SSMS to access, configure, manage, '
                   r'administer, and develop platforms that use the Microsoft SQL Database Engine, '
                   r'including SQL Server(?:, [^.]{1,380})?\.', re.I),
        re.compile(r'^SSMS does not support administering SQL Server\.', re.I),
    ),
    'kaspersky-endpoint-security-windows': _Tool(
        'https://support.kaspersky.com/kes-for-windows/12.10',
        ('https://support.kaspersky.com/kes-for-windows/12.10/225086',),
        re.compile(r'Integration of the built-in agent with EDR Optimum / EDR Expert', re.I),
        'kaspersky-edr-optimum', 'Kaspersky Endpoint Detection and Response Optimum', 'documented_integration',
        re.compile(r'^The EDR Optimum component as part of Kaspersky Endpoint Security supports interaction '
                   r'with the Kaspersky Endpoint Detection and Response Optimum 2\.0 solution\. Interaction '
                   r'with Kaspersky Endpoint Detection and Response Optimum version 1\.0 is not supported\.', re.I),
        re.compile(r'^The EDR Optimum component as part of Kaspersky Endpoint Security does not support '
                   r'interaction with the Kaspersky Endpoint Detection and Response Optimum 2\.0 solution\.', re.I),
    ),
}
ANCHORS = MappingProxyType({
    'kaspersky-edr-current-requirements': 'Kaspersky EDR Optimum 4.0: условия активации',
    'cryptopro-csp-server-upgrade': 'КриптоПро CSP · бизнес, Windows, действующая серверная лицензия прежней версии (демо)',
    'veeam-backup-replication': 'Veeam Backup & Replication',
    'microsoft-sql-server': 'Microsoft SQL Server',
    'kaspersky-endpoint-security-windows': 'Kaspersky Endpoint Security for Windows (12.10 documentation)',
})
# The published category link redirects to HTTP. Fetch its exact HTTPS
# equivalent directly; do not follow redirects or accept inputs to its query.
_CANONICAL_DOCUMENTS = MappingProxyType({
    'https://cryptopro.ru/order/cat/csp_all_positions': 'https://cryptopro.ru/order/OrderForm.aspx?button=csp_all_positions',
})
NANOCAD_PUBLIC_DOCS = MappingProxyType({
    'faq': 'https://www.nanocad.ru/faq/',
    'mech_product': 'https://www.nanocad.ru/products/platforma/konfiguratsiya-nanocad-standart-mech/',
    'build_product': 'https://www.nanocad.ru/products/platforma/konfiguratsiya-nanocad-standart-build/',
})
_ALLOWED_URLS = frozenset(_CANONICAL_DOCUMENTS.values()) | frozenset(NANOCAD_PUBLIC_DOCS.values()) | frozenset(
    url for tool in _TOOLS.values() for url in (tool.entry, *tool.documents))
_DIRECT_URLS = frozenset(tool.entry for tool in _TOOLS.values() if tool.direct)


def public_anchor(name: str | None) -> str | None:
    """Resolve a catalog name locally to a fixed public family, never pass the name outbound."""
    if not isinstance(name, str):
        return None
    labels = (
        ('veeam-backup-replication', r'\bVeeam\s+Backup\s*(?:&|and)\s*Replication\b'),
        ('microsoft-sql-server', r'\bMicrosoft\s+SQL\s+Server\b(?!\s+Management\b)'),
        ('kaspersky-endpoint-security-windows', r'\bKaspersky\s+Endpoint\s+Security\s+(?:for\s+)?Windows\b'),
    )
    hits = [key for key, pattern in labels if re.search(pattern, name, re.I)]
    return hits[0] if len(hits) == 1 else None


def _normalize(text: str) -> str:
    return ' '.join(text.split())


class _PageParser(HTMLParser):
    """Expose only actual anchors and normalized text blocks, never execute markup."""

    _BLOCKS = frozenset(('p', 'li', 'th', 'td', 'h1', 'h2', 'h3', 'h4'))
    _IGNORE = frozenset(('script', 'style', 'template', 'noscript', 'svg', 'head'))
    _VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
                       'meta', 'param', 'source', 'track', 'wbr'))

    def __init__(self, *, include_divs=False):
        super().__init__(convert_charrefs=True)
        self._blocks = self._BLOCKS | {'div'} if include_divs else self._BLOCKS
        self.links: list[tuple[str, str]] = []
        self.blocks: list[str] = []
        self._ignored: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []
        self._block: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag not in self._VOID and (tag in self._IGNORE or 'hidden' in values or
                                       values.get('aria-hidden') == 'true' or self._ignored):
            self._ignored.append(tag)
            return
        if self._ignored:
            return
        if tag == 'a':
            self._href = values.get('href') if sum(name == 'href' for name, _ in attrs) == 1 else None
            self._label = []
        if tag in self._blocks:
            self._flush_block()
            self._block = tag
        if tag == 'br':
            self.handle_data(' ')

    def handle_endtag(self, tag):
        if self._ignored:
            if self._ignored[-1] == tag:
                self._ignored.pop()
            return
        if tag == 'a' and self._href is not None:
            label = _normalize(''.join(self._label))
            if label and len(self.links) < MAX_LINKS:
                self.links.append((self._href, label))
            self._href = None
        if tag == self._block:
            self._flush_block()

    def handle_data(self, data):
        if not self._ignored:
            if self._block is not None:
                self._text.append(data)
            if self._href is not None:
                self._label.append(data)

    def _flush_block(self):
        text = _normalize(''.join(self._text))
        if text and len(self.blocks) < MAX_BLOCKS:
            self.blocks.append(text)
        self._block = None
        self._text = []


@dataclass(frozen=True)
class _Page:
    links: tuple[tuple[str, str], ...]
    blocks: tuple[str, ...]
    retrieved_at: str


def _public_url(base: str, href: str, allowed: tuple[str, ...]) -> str | None:
    # Compare *exact* configured endpoints after resolving only clean, ordinary
    # paths. Do not forward query strings, fragments, dot segments, or tracking.
    if (not href or len(href) > MAX_URL or not href.isascii() or
            any(ord(c) <= 32 or ord(c) == 127 or c in '\\%?#' for c in href)):
        return None
    try:
        path = urlsplit(href).path
        if {'.', '..'} & set(path.split('/')):
            return None
        url = urljoin(base, href)
    except ValueError:
        return None
    return url if url in allowed else None


def _curl_fetch(url: str, timeout: float) -> bytes:
    if url not in _ALLOWED_URLS:
        raise ValueError('source_out_of_scope')
    process = subprocess.run(
        ['curl', '-q', '--noproxy', '*', '--proto', '=https', '--include',
         '--silent', '--fail', '--max-filesize', str(MAX_BYTES), '--max-time',
         f'{timeout:.3f}', '--connect-timeout', f'{min(2.0, timeout):.3f}',
         '--header', 'Accept: text/html', '--header', 'Accept-Encoding: identity', '--', url],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={'PATH': os.defpath}, timeout=timeout,
    )
    headers, sep, payload = process.stdout.partition(b'\r\n\r\n')
    if (process.returncode or not sep or len(headers) > 65536 or
            not re.match(rb'HTTP/(?:1\.[01]|2|3) 200(?: |\r|$)', headers) or
            not re.search(rb'(?:^|\r\n)content-type:\s*text/html(?:[;\r\n]|$)', headers, re.I) or
            len(payload) > MAX_BYTES):
        raise OSError('invalid_or_unavailable_source_page')
    return payload


class PublicResearch:
    """Two bounded GET steps: discover an allowlisted link, then verify its page.

    `allow_network` must be exactly True per call; disabled calls do not consult
    the cache. `fetch(url, timeout) -> bytes` overrides the trusted transport for
    tests; independent concurrent callers share only the thread-safe page cache.
    """

    def __init__(self, *, timeout: float = 3.5, total_timeout: float = 8.0,
                 cache_ttl: float = 900.0, fetch: Callable[[str, float], bytes] | None = None):
        if not (math.isfinite(timeout) and 0.01 <= timeout <= 10 and
                math.isfinite(total_timeout) and 0.01 <= total_timeout <= 20 and
                math.isfinite(cache_ttl) and 0 <= cache_ttl <= 3600):
            raise ValueError('invalid public research bounds')
        self._timeout = timeout
        self._total_timeout = total_timeout
        self._ttl = cache_ttl
        self._fetch = _curl_fetch if fetch is None else fetch
        self._cache: OrderedDict[str, tuple[float, _Page]] = OrderedDict()
        self._cache_lock = Lock()

    def _read(self, url, stage, deadline, trace):
        now = monotonic()
        if deadline - now < 0.01:
            trace.append(TraceStep(stage, url, 'budget_exhausted'))
            return None
        with self._cache_lock:
            cached = self._cache.pop(url, None)
            if cached and cached[0] > now:
                self._cache[url] = cached
                trace.append(TraceStep(stage, url, 'cached'))
                return cached[1]
        try:
            raw = self._fetch(url, min(self._timeout, deadline - now))
            if type(raw) is not bytes or not raw or len(raw) > MAX_BYTES:
                raise ValueError('empty_or_oversize_source')
            p = _PageParser(include_divs=url in _DIRECT_URLS)
            p.feed(raw.decode('utf-8-sig'))
            p.close()
        except (OSError, ValueError, subprocess.SubprocessError):
            trace.append(TraceStep(stage, url, 'unavailable'))
            return None
        if monotonic() >= deadline:
            trace.append(TraceStep(stage, url, 'budget_exhausted'))
            return None
        page = _Page(tuple(p.links), tuple(p.blocks), datetime.now(timezone.utc).isoformat())
        if self._ttl:
            with self._cache_lock:
                self._cache[url] = (monotonic() + self._ttl, page)
                self._cache.move_to_end(url)
                while len(self._cache) > MAX_CACHE_PAGES:
                    self._cache.popitem(last=False)
        trace.append(TraceStep(stage, url, 'fetched'))
        return page

    def run(self, anchor_id: str, *, allow_network: bool = False) -> ResearchResult:
        if type(anchor_id) is not str or len(anchor_id) > 64 or anchor_id not in _TOOLS:
            return ResearchResult(None, 'unsupported_anchor')
        if allow_network is not True:
            return ResearchResult(anchor_id, 'disabled')
        trace: list[TraceStep] = []
        deadline = monotonic() + self._total_timeout
        tool = _TOOLS[anchor_id]
        url = tool.entry
        if not tool.direct:
            index = self._read(tool.entry, 'discover', deadline, trace)
            if index is None:
                return ResearchResult(anchor_id, 'source_unavailable', trace=tuple(trace))
            url = next((valid for href, label in index.links if tool.link_label.fullmatch(label)
                        and (valid := _public_url(tool.entry, href, tool.documents))), None)
            if url is None:
                return ResearchResult(anchor_id, 'no_findings', trace=tuple(trace))
            url = _CANONICAL_DOCUMENTS.get(url, url)
        page = self._read(url, 'verify', deadline, trace)
        if tool.direct and page is None:
            return ResearchResult(anchor_id, 'source_unavailable', trace=tuple(trace))
        status: EvidenceStatus = 'needs_check'
        evidence: tuple[Evidence, ...] = ()
        reason = 'linked_source_unavailable' if page is None else 'no_validation_quote'
        if page is not None:
            supports = _quote(tool.supports, page.blocks)
            contradicts = _quote(tool.contradicts, page.blocks)
            if supports and contradicts:
                reason = 'conflicting_documentation'
            elif supports or contradicts:
                status, reason = ('confirmed', 'documented_family_relation') if supports else (
                    'rejected', 'explicit_family_relation_denial')
                evidence = (Evidence(url, supports or contradicts, page.retrieved_at),)
                if anchor_id == 'cryptopro-csp-server-upgrade' and status == 'confirmed':
                    terms = _quote(re.compile(r'При обновлении лицензии ОРИГИНАЛ предыдущей версии подлежит возврату в ООО "КРИПТО-ПРО"\.[^.]+по месту её приобретения[^.]+\.', re.I), page.blocks)
                    if terms:
                        evidence += (Evidence(url, terms, page.retrieved_at),)
                    else:
                        status, reason = 'needs_check', 'upgrade_terms_not_verified'
        candidate = PublicCandidate(tool.product_id, tool.product_name, tool.relation, status, reason, evidence)
        return ResearchResult(anchor_id, 'completed', (candidate,), tuple(trace))


def _quote(pattern: re.Pattern[str], blocks: tuple[str, ...]) -> str:
    # Use the actual matched slice, never a template or a freeform paraphrase.
    # Fail closed on an overlong or unrecognized sentence.
    for block in blocks:
        match = pattern.search(block)
        if match and match.end() - match.start() <= MAX_QUOTE:
            return match.group()
    return ''
