from datetime import date
from recommendation.profile_v5_comparison import compare


def test_variants_new_short_lists_and_future_groups_are_reporting_only():
    d = date(2026, 1, 1)
    f = date(2026, 9, 7)
    rows = [('1','u',d,'a',None,'A','family'),
            ('2','v',d,'a',None,'A','family'),
            ('2','v',d,'b',None,'B','family'),
            ('3','w',d,'c',None,'C','other'),
            ('4','u',f,'b',None,'B','family'),
            ('5','u',f,'c',None,'C','other')]
    result = compare(rows, {'u':'B2B','v':'B2B','w':'B2C'})
    c = result['results']['A']['all']['copurchase']
    assert (c['clients'], c['hits'], c['variants'], c['new']) == (1, 1, 1, 0)
    assert (c['nonempty'], c['full_triples']) == (1, 0)
    assert c['precision_at_3'] == 1/3
    rows[-2] = ('4','u',f,'b',None,'B','changed')
    altered = compare(rows, {'u':'B2B','v':'B2B','w':'B2C'})
    x = altered['results']['A']['all']['copurchase']
    assert (x['hits'], x['variants'], x['new'], x['full_triples']) == (1, 0, 1, 0)
    assert x['precision_at_3'] == c['precision_at_3']


def test_no_hits_and_empty_segments():
    rows = [('1','u',date(2026,1,1),'a',None,'A',None),
            ('2','u',date(2026,9,7),'b',None,'B',None)]
    result = compare(rows, {'u':'B2B'})['results']
    empty = result['A']['B2C']['profile']
    assert empty['clients'] == 0 and empty['precision_at_3'] is None
    no_hits = result['A']['all']['copurchase']
    assert no_hits['variant_share'] is None and no_hits['fill_rate'] == 0
