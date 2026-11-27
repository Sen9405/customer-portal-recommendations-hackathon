from datetime import date
from types import SimpleNamespace
from unittest import TestCase

from recommendation.semantic_transition_eval import label_pairs, score_window


def test_semantic_label_changes_only_third_slot_and_scoring_is_paired():
    user = 'local'
    pipe = SimpleNamespace(history={user:{'src'}})
    names = {'src':'A','b':'B','d':'D'}
    prepared = [(user, ['first','second','third'], [('b', 2,'src',5), ('d',1,'src',3)])]
    rows = [('o',user,date(2026,6,1),'first'), ('o2',user,date(2026,6,1),'d')]
    scores, example = score_window(rows,date(2026,6,1),None,(pipe,names,prepared),
                                  {('A','B'):'renewal',('A','D'):'upgrade'})
    assert scores['A']['profile'] == scores['A']['transition'] == 1
    assert scores['A']['model'] == scores['B']['model'] == 2
    assert scores['A']['model_added_vs_control'] == 1
    assert scores['A']['model_lost_vs_control'] == 0
    assert example is not None
    assert (example['before'],example['after'],example['suppressed_label']) == ('B','D','renewal')


def test_label_batch_rejects_wrong_keys_and_contains_no_history_or_stats():
    window = (None, {1:'A',2:'B'}, [('private',[],[(2,1.5,1,6)])])
    def invoke(_, payload):
        assert payload == '{"p1": ["A", "B"]}'
        return '{"p2":"upgrade"}', None
    with TestCase().assertRaises(ValueError):
        label_pairs([window],invoke)
