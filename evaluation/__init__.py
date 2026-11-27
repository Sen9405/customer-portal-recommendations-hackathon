"""Offline top-3 evaluation using only Python's standard library.

An event is one interaction. The global chronological split puts the entire
boundary timestamp in test, guaranteeing max(train.time) < min(test.time)
when both sets are nonempty; a large tie can make either partition empty.
Popularity counts train interactions, including repeats; ties use item ID order.
Previously interacted items are excluded for each user.

Evaluation is macro-averaged over users with nonempty test truth only; missing
recommendations count as empty. The first three recommendation positions are
considered (duplicates consume positions). Precision is unique relevant hits / 3.
Fill rate is the fraction of all users in the evaluation cohort (all keys of
truth) receiving at least one recommendation; it includes empty-truth users.
Slot fill is the average fraction of three unique positions filled across the
same cohort. With no eligible users the corresponding metric is None.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import floor


@dataclass(frozen=True)
class Event:
    user_id: str
    item_id: str
    timestamp: int


def chronological_split(events, test_fraction=0.2):
    """Split globally by time; equal timestamps always stay together in test."""
    if not 0 < test_fraction < 1:
        raise ValueError('test_fraction must be strictly between 0 and 1')
    ordered = sorted(events, key=lambda event: event.timestamp)
    if not ordered:
        return [], []
    boundary = ordered[floor(len(ordered) * (1 - test_fraction))].timestamp
    return ([event for event in ordered if event.timestamp < boundary],
            [event for event in ordered if event.timestamp >= boundary])


def popularity_recommendations(train, users):
    """Recommend up to three popular train items not seen by each user."""
    counts = Counter()
    seen = defaultdict(set)
    for event in train:
        counts[event.item_id] += 1
        seen[event.user_id].add(event.item_id)
    ranking = sorted(counts, key=lambda item: (-counts[item], item))
    return {user: [item for item in ranking if item not in seen[user]][:3]
            for user in users}


def evaluate(recommendations, truth):
    """Measure precision for known truth and coverage for the full cohort."""
    precisions, fills = [], []
    for user, relevant in truth.items():
        items = set(recommendations.get(user, [])[:3])
        fills.append(len(items) / 3)
        relevant = set(relevant)
        if relevant:
            precisions.append(len(items & relevant) / 3)
    count = len(precisions)
    cohort = len(fills)
    return {'precision_at_3': sum(precisions) / count if count else None,
            'fill_rate': sum(fill > 0 for fill in fills) / cohort if cohort else None,
            'slot_fill_rate': sum(fills) / cohort if cohort else None,
            'evaluated_users': count, 'cohort_size': cohort}
