def test_contract_rejects_unsupported_media_before_execution():
    from domain.publishing.provider_contract import validate_request

    err = validate_request("twitter", text="hello", image_url="https://example.com/a.jpg")
    assert err["error_code"] == "media_url_unsupported"


def test_contract_requires_instagram_image_url():
    from domain.publishing.provider_contract import validate_request

    err = validate_request("instagram", text="hello")
    assert err["error_code"] == "image_url_required"


def test_contract_requires_whatsapp_recipient():
    from domain.publishing.provider_contract import validate_request

    err = validate_request("whatsapp", text="hello")
    assert err["error_code"] == "recipient_required"


def test_contract_allows_x_image_bytes_and_threads():
    from domain.publishing.provider_contract import get_contract, validate_request

    contract = get_contract("twitter")
    assert contract.supports_image_bytes is True
    assert contract.supports_threads is True
    assert validate_request("twitter", text="hello", image_bytes=b"x") is None


def test_contract_rejects_unknown_platform():
    from domain.publishing.provider_contract import validate_request

    err = validate_request("mastodon", text="hello")
    assert err["error_code"] == "unsupported_platform"
