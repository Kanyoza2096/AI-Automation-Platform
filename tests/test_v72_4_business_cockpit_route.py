from services.v72_4_business_truth_adapters import BusinessTruthAdapters

def test_all_ten_business_sections_exist_and_are_read_only():
    values = BusinessTruthAdapters('workspace', db=None).build()
    assert set(values) == {
        'businessHealth','objectives','opportunities','risks','decisions',
        'workforce','operations','outcomes','evidence','governance'
    }
    for value in values.values():
        payload = value.as_dict()
        assert payload['actionAuthorityGranted'] is False
        assert payload['authoritySource']
