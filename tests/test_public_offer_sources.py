from recommendation.public_evidence_agent import evidence_for_offers


def test_public_evidence_only_exact_known_offer():
    result = evidence_for_offers([{'offer_id':'298271'}, {'offer_id':'synthetic-other'}])
    assert set(result)=={'298271'}
    assert result['298271']
    assert all(x['url'].startswith('https://support.kaspersky.com/') for x in result['298271'])
    assert all('id' in x and 'claim' in x and 'limitation' in x for x in result['298271'])
    assert evidence_for_offers([{'offer_id':'298271x'}]) == {}


def test_existing_registry_matches_public_product_family_without_identity_claim():
    result=evidence_for_offers([{'offer_id':'sample', 'name':'Veeam ONE 13'}])
    assert 'sample' in result
    assert any(r['id']=='veeam-one13-backup-monitoring' for r in result['sample'])
    assert all('не подтвержда' in r['limitation'] for r in result['sample'])
    assert evidence_for_offers([{'offer_id':'x', 'name':'unknown'}]) == {}


def test_more_manufacturers_have_bound_vendor_documents():
    candidates=[{'offer_id':'red','name':'РБД Монитор'}, {'offer_id':'csp','name':'КриптоПро CSP'}, {'offer_id':'cyber','name':'Кибер Бэкап Облачный'}]
    result=evidence_for_offers(candidates)
    assert set(result)=={'red','csp','cyber'}
    assert result['red'][0]['url'].startswith('https://rdb.red-soft.ru/')
    assert result['csp'][0]['url'].startswith('https://docs.cryptopro.ru/')
    assert result['cyber'][0]['url'].startswith('https://docs.cyberprotect.ru/')
    assert all('не подтвержда' in refs[0]['limitation'].lower() for refs in result.values())


def test_five_additional_manufacturers():
    products=[('dr','Dr.Web Enterprise Security Suite','drweb-product-documentation'),
              ('pro','PRO32 Endpoint Security','pro32-endpoint-product'),
              ('astra','Astra Linux Special Edition','astra-linux-ald-knowledge-base'),
              ('nano','Платформа nanoCAD','nanocad-mechanics-component'),
              ('ascon','КОМПАС-3D v24','ascon-kompas24-software')]
    result=evidence_for_offers([{'offer_id':i,'name':name} for i,name,_ in products])
    assert all(any(ref['id']==expected for ref in result[i]) for i,_,expected in products)
    assert all(refs[0]['limitation'] for refs in result.values())


def test_no_model_or_user_supplied_url_accepted():
    result = evidence_for_offers([{'offer_id':'unknown','url':'https://evil.example/test'}])
    assert not result
