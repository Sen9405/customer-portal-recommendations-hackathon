"""Private, deterministic evidence preparation, NOT recommendations or LLM output.

Rows: (order_id, client_id, order_week, item_id, ...); as_of is inclusive.
Dates are observed week labels, not exact purchase dates; a week containing the
cutoff may include events after it. Use a completed-week cutoff for backtests.
Association rank: mean over history of co/(co+1) * orders/(orders+1) *
1/(1+days/91), plus 0.25 times mean over latest-week items of seq/(seq+1).
Days measure from the inclusive cutoff to the latest observed week for that item;
orders are distinct (client, week, order) baskets. Duplicate rows do not add weight.
All contributions are bounded, fixed and monotonic; no learned parameters.
Tie breaks by sequential score then lexical item id. Fallback ranks by unique
client-week-order count, then lexical id. These are fixed rules, not validation.
Current catalog and undated client attributes are descriptive only, never inputs
to ranking; they are not point-in-time evidence. No availability check occurs.
"""
from collections import Counter, defaultdict
from datetime import date, datetime


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise ValueError('Expected an ISO date or date object')


def catalog_evidence(features, audit):
    """Adapt current local feature/audit snapshots; never certify a match.

    Ambiguity includes multiple candidates and multiple items claiming an offer.
    Missing audit data is unknown, not an unambiguous match.
    """
    entries = features.get('items', {})
    claims = Counter(str(v['candidate']['offer_id']) for v in entries.values()
                     if v.get('candidate', {}).get('offer_id') is not None)
    result = {}
    for item, entry in entries.items():
        candidate = entry.get('candidate') or {}
        offer = candidate.get('offer_id')
        matches = audit.get('items', {}).get(str(item), {}).get('matches', {})
        union = matches.get('all_union')
        result[str(item)] = {'candidate': candidate, 'matches': None if union is None else [str(m['offer_id']) for m in union],
                             'offer_claim_count': claims.get(str(offer), 0) if offer is not None else 0,
                             'match_channels': entry.get('match_channels', [])}
    return result


class ProfilePipeline:
    """Construct once per cutoff, then call profile(user) or profiles(users).

    The caller owns private input/output handling. No network or per-user logging.
    All sales-derived counts and candidates use only rows at or before as_of.
    """
    def __init__(self, rows, as_of, *, customers=None, catalog=None):
        self.as_of = _day(as_of)
        self.customers = customers or {}
        self.catalog = catalog or {}
        orders = defaultdict(set)
        weeks = defaultdict(set)
        self.history = defaultdict(set)
        for row in rows:
            order, user, week, item = row[:4]
            if any(x is None or x == '' for x in (order, user, item)):
                raise ValueError('Missing order, customer or item identifier')
            week = _day(week)
            if week > self.as_of:
                continue
            orders[(user, week, order)].add(item)
            weeks[(user, week)].add(item)
            self.history[user].add(item)
        self.user_weeks = defaultdict(dict)
        self.item_orders = defaultdict(Counter)
        self.user_orders = Counter()
        self.popularity = Counter()
        self.co = defaultdict(Counter)
        self.seq = defaultdict(Counter)
        for (user, _week, _order), basket in orders.items():
            self.user_orders[user] += 1
            self.item_orders[user].update(basket)
            self.popularity.update(basket)
            for item in basket:
                self.co[item].update(basket - {item})
        for (user, week), basket in weeks.items():
            self.user_weeks[user][week] = basket
        self._transition_clients = defaultdict(set)
        for customer, baskets in self.user_weeks.items():
            ordered = sorted(baskets)
            for previous, current in zip(ordered, ordered[1:]):
                for item in baskets[previous]:
                    next_items = baskets[current] - {item}
                    self.seq[item].update(next_items)
                    for target in next_items:
                        self._transition_clients[item, target].add(customer)
        self.pop_rank = sorted(self.popularity, key=lambda x: (-self.popularity[x], str(x)))
        self.distinct_clients = Counter()
        for items in self.history.values():
            self.distinct_clients.update(items)
        self.distinct_rank = sorted(self.distinct_clients, key=lambda x: (-self.distinct_clients[x], str(x)))

    def blended_candidates(self, user, *, eligible=None, profile=None):
        """Two original profile candidates, then distinct-client popularity.

        Eligibility is applied before mixing (e.g. usable snapshot mapping). The
        original profile, including its basket-count fallback, remains unchanged.
        """
        if eligible is not None:
            profile = self.profile(user, _eligible=eligible)
        else:
            profile = self.profile(user) if profile is None else profile
        seen = self.history.get(user, set())
        eligible = eligible or (lambda item: True)
        personal = profile['candidates']
        result = personal[:2]
        selected = {c['item_id'] for c in result}
        for item in self.distinct_rank:
            if item not in seen and item not in selected and eligible(item):
                result.append({'item_id': item, 'method': 'distinct_client_popularity',
                               'score': self.distinct_clients[item],
                               'evidence': {'train_distinct_client_count': self.distinct_clients[item]},
                               'catalog': self._catalog(item)})
                selected.add(item)
                if len(result) >= 3:
                    break
        for candidate in personal[2:]:
            if len(result) >= 3:
                break
            if candidate['item_id'] not in selected:
                result.append(candidate)
                selected.add(candidate['item_id'])
        return result

    def _catalog(self, item):
        entry = self.catalog.get(str(item))
        if not entry:
            return {'status': 'no_candidate_or_source', 'identity': 'unverified', 'ambiguous': None}
        candidate = entry.get('candidate') or {}
        offer = candidate.get('offer_id')
        matches = entry.get('matches')
        claims = entry.get('offer_claim_count')
        ambiguous = None if matches is None or claims is None else (
            len(set(map(str, matches))) != 1 or claims != 1 or offer is None or str(offer) not in map(str, matches))
        return {'status': 'current_snapshot_candidate' if offer is not None else 'no_candidate',
                'identity': 'unverified', 'ambiguous': ambiguous,
                'offer_id': offer, 'match_channels': entry.get('match_channels', []),
                'matching_offer_ids': matches, 'offer_claim_count': claims,
                'category': candidate.get('category'), 'url': candidate.get('url'),
                'snapshot_available': candidate.get('available'),
                'limitations': 'Current undated snapshot, not historical or live availability; matching is not identity or compatibility.'}

    def profile(self, user, *, _eligible=None):
        seen = self.history.get(user, set())
        baskets = self.user_weeks.get(user, {})
        dates = sorted(baskets)
        latest = baskets[dates[-1]] if dates else set()
        co_scores, seq_scores = Counter(), Counter()
        for item in sorted(seen, key=str):
            n = self.item_orders[user][item]
            last = max(d for d in dates if item in baskets[d])
            weight = (n / (n + 1)) / (1 + (self.as_of - last).days / 91)
            for target, count in self.co.get(item, {}).items():
                co_scores[target] += weight * count / (count + 1) / len(seen)
        for item in sorted(latest, key=str):
            for target, count in self.seq.get(item, {}).items():
                seq_scores[target] += 0.25 * count / (count + 1) / len(latest)
        targets = (set(co_scores) | set(seq_scores)) - seen
        ranked = sorted(targets, key=lambda x: (-(co_scores[x] + seq_scores[x]), -seq_scores[x], str(x)))
        ranked.extend(x for x in self.pop_rank if x not in seen and x not in targets)
        candidates = []
        for target in ranked:
            if _eligible is not None and not _eligible(target):
                continue
            if len(candidates) >= 3:
                break
            co = [{'history_item': item, 'order_count': self.co[item][target]}
                  for item in sorted(seen, key=str) if self.co.get(item, {}).get(target)]
            seq = [{'last_week_item': item, 'transition_count': self.seq[item][target],
                    'other_client_count': len(clients) - (user in clients)}
                   for item in sorted(latest, key=str) if self.seq.get(item, {}).get(target)
                   for clients in (self._transition_clients[item, target],)]
            candidates.append({'item_id': target, 'method': 'association' if target in targets else 'popularity_fallback',
                               'score': co_scores[target] + seq_scores[target],
                               'evidence': {'coorder': co, 'sequential': seq,
                                            'train_unique_order_count': self.popularity[target]},
                               'catalog': self._catalog(target)})
        raw = self.customers.get(user, {}) or {}
        attributes = {key: {'value': raw.get(key) or None,
                            'temporal_status': 'undated' if raw.get(key) else 'unknown'}
                      for key in ('group', 'industry', 'segment')}
        return {'status': 'observed_history' if seen else 'cold_no_observed_history',
                'as_of': self.as_of.isoformat(), 'history_items': sorted(seen, key=str),
                'item_history': [{'item_id': item,
                                  'week_dates': [d.isoformat() for d in dates if item in baskets[d]],
                                  'week_count': sum(item in baskets[d] for d in dates),
                                  'order_count': self.item_orders[user][item]}
                                 for item in sorted(seen, key=str)],
                'history': {'week_dates': [d.isoformat() for d in dates], 'week_count': len(dates),
                            'order_count': self.user_orders[user],
                            'week_gaps_days': [(b-a).days for a, b in zip(dates, dates[1:])],
                            'recency_days': (self.as_of-dates[-1]).days if dates else None},
                'customer_attributes': attributes, 'candidates': candidates,
                'limitations': ['Candidates for manual analysis only; not LLM decisions or client-ready recommendations.',
                                'Observed weeks and orders are not quantity, renewal, or complete history.',
                                'Week labels are not exact purchase dates; incomplete cutoff weeks may leak later events.',
                                'Co-order and consecutive observed weeks are associations, not compatibility or causation.',
                                'Current catalog and undated attributes may postdate the cutoff; excluded from ranking.',
                                'Identity, entitlement and live availability unverified.']}

    def profiles(self, users):
        """Iterate requested customer IDs, including customers with no history."""
        for user in users:
            yield user, self.profile(user)
