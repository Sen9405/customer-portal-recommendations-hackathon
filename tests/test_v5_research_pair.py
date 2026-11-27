from recommendation.v5_research_pair import inject


def test_injection_requires_identity_and_evidence_and_limits_to_one_slot():
    base = ['a', 'b', 'c']
    finding = {'offer_id': 'public', 'source_status': 'confirmed', 'url': 'https://vendor.example/doc', 'status': 'needs_check'}
    unchanged, reasons = inject(base, [finding], {}, set())
    assert unchanged == base and reasons == {'no_unique_train_item': 1}
    assert inject(base, [finding], {'public': 'd'}, set())[0] == ['a', 'b', 'd']
    assert inject(base, [finding], {'public': 'd'}, {'d'})[0] == base
    assert inject(base, [finding], {'public': 'b'}, set())[0] == base
    assert inject(base, [{**finding, 'status': 'rejected'}], {'public': 'd'}, set())[0] == base
    assert inject(base, [{**finding, 'source_status': 'unavailable'}], {'public': 'd'}, set())[0] == base