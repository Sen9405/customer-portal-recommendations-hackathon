"""Run with python -m evaluation; all events below are synthetic."""

import json
from collections import defaultdict

from . import Event, chronological_split, evaluate, popularity_recommendations


def main():
    events = [Event('u1', 'a', 1), Event('u2', 'b', 2),
              Event('u3', 'a', 3), Event('u1', 'b', 4),
              Event('u2', 'a', 5), Event('u3', 'c', 6)]
    train, test = chronological_split(events, 0.5)
    truth = defaultdict(set)
    for event in test:
        truth[event.user_id].add(event.item_id)
    recommendations = popularity_recommendations(train, truth)
    result = evaluate(recommendations, truth)
    print(json.dumps({'data': 'synthetic', 'train_events': len(train),
                      'test_events': len(test), **result}, indent=2))


if __name__ == '__main__':
    main()
