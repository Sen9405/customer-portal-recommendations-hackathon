from types import SimpleNamespace
from typing import cast

from recommendation.contest_recommender import ContestRecommender


def test_cold_popular_keeps_goal_group_and_industry_support_separate():
    def offer(name, category='cad', availability='true'):
        return dict(name=name, category_id=category, availability=availability, params='[]')

    offers = dict(local=offer('CAD Industry'), broad=offer('CAD Business'), home=offer('Microsoft 365 Family'),
                  outside=offer('Windows', 'other'), missing=offer('CAD Missing', availability='false'),
                  media=offer('CAD Media Pack'))
    clients = {i: {'group':'B2B', 'industry':'selected' if i < 3 else 'another'} for i in range(9)}
    clients[9] = {'group':'B2C'}
    history = {i: {'home','outside','missing','media', 'local' if i < 3 else 'broad'} for i in range(9)}
    history[9] = {'home'}
    fake = SimpleNamespace(pipeline=SimpleNamespace(history=history), _unique=offers.get,
                           goal_categories={'cad':{'cad'}})
    fake._cold_popular_rows = lambda *args, **kwargs: ContestRecommender._cold_popular_rows(cast(ContestRecommender, fake), *args, **kwargs)
    pop = lambda group, industry, goal, platform=None: ContestRecommender.cold_popular(cast(ContestRecommender, fake),clients,group,industry,goal,platform)
    assert [(r['name'],r['industry_match']) for r in pop('B2B','selected','cad')] == [('CAD Industry', True), ('CAD Business',False)]
    assert [r['name'] for r in pop('B2C',None,'cad')] == ['Microsoft 365 Family']
    assert not pop('B2B','selected','cad','armv8')
    assert pop('B2B',None,'cad')[0]['name'] == 'CAD Business'
