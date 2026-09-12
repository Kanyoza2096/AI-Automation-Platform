from unittest.mock import MagicMock, patch


def test_linkedin_uses_current_posts_api_and_versioned_headers():
    from services.connectors.linkedin_connector import LinkedInConnector

    account = MagicMock(
        access_token="tok",
        platform_account_id="urn:li:organization:123",
        platform_config={"linkedin_version": "202608"},
    )
    connector = LinkedInConnector(account)
    response = MagicMock(status_code=201, headers={"X-RestLi-Id": "urn:li:share:1"})
    with patch("services.connectors.linkedin_connector.requests.post", return_value=response) as post:
        result = connector.publish("hello")

    assert result.ok is True
    assert result.post_id == "urn:li:share:1"
    assert post.call_args.args[0] == "https://api.linkedin.com/rest/posts"
    assert post.call_args.kwargs["headers"]["Linkedin-Version"] == "202608"


def test_linkedin_does_not_silently_drop_media():
    from services.connectors.linkedin_connector import LinkedInConnector

    account = MagicMock(access_token="tok", platform_account_id="urn:li:organization:123", platform_config={})
    result = LinkedInConnector(account).publish("hello", image_url="https://example.com/a.jpg")
    assert result.ok is False
    assert result.outcome == "failed"
    assert "media" in (result.error or "").lower()


def test_twitter_partial_thread_is_uncertain_not_success():
    from plugins.twitter.plugin import TwitterPlugin

    plugin = TwitterPlugin()
    plugin._started = True
    connector = MagicMock()
    first = MagicMock(ok=True, post_id="t1", error=None)
    second = MagicMock(ok=False, post_id=None, error="rate limited")
    connector.publish.side_effect = [first, second]
    plugin._connector = connector

    result = plugin._publish_thread(connector, ["one", "two"])
    assert result["ok"] is False
    assert result["outcome"] == "uncertain"
    assert result["reconciliation_required"] is True
    assert result["post_id"] == "t1"


def test_canonical_path_prefers_brand_bound_social_account_when_available():
    import asyncio
    from domain.publishing.canonical_pipeline import _connector_execute
    from models.social_account import SocialAccount, Platform

    account = SocialAccount(
        id="sa-1", workspace_id="ws-1", brand_id="brand-1", platform=Platform.LINKEDIN,
        platform_account_id="urn:li:organization:123", access_token="tok"
    )
    plugin = MagicMock()
    plugin.publish.return_value = MagicMock(ok=True, post_id="li-1", error=None)

    class Container:
        social_plugins = {"linkedin": MagicMock()}
        industry_plugins = {}
        def try_resolve(self, cls):
            return None

    with patch("repositories.social_account_repository.SocialAccountRepository.find_by_brand_and_platform", return_value=account), \
         patch("services.connectors.connector_factory.create_connector", return_value=plugin) as create:
        result = asyncio.run(_connector_execute(
            container=Container(), workspace_id="ws-1", brand_id="brand-1",
            brand=MagicMock(id="brand-1", workspace_id="ws-1"), platform="linkedin",
            caption="hello", topic="", decision_id="dec-1", idempotency_key=None,
            allow_simulate=False,
        ))

    assert result["status"] == "ok"
    assert result["platform_post_id"] == "li-1"
    create.assert_called_once_with(account)


def test_twitter_does_not_silently_drop_image_url():
    from plugins.twitter.plugin import TwitterPlugin

    plugin = TwitterPlugin()
    plugin._started = True
    plugin._token_expires_at = 9999999999
    plugin._connector = MagicMock()
    result = plugin.publish("hello", image_url="https://example.com/a.jpg")
    assert result["ok"] is False
    assert result["outcome"] == "failed"
    assert "image_bytes" in result["reason"]


def test_linkedin_image_bytes_use_images_api_then_posts_api():
    from services.connectors.linkedin_connector import LinkedInConnector

    account = MagicMock(
        access_token="tok",
        platform_account_id="urn:li:organization:123",
        platform_config={"linkedin_version": "202608"},
    )
    init = MagicMock(status_code=200)
    init.json.return_value = {"value": {"uploadUrl": "https://upload.example/image", "image": "urn:li:image:1"}}
    upload = MagicMock(status_code=202)
    status = MagicMock(status_code=200)
    status.json.return_value = {"status": "AVAILABLE", "id": "urn:li:image:1"}
    post = MagicMock(status_code=201, headers={"X-RestLi-Id": "urn:li:share:1"})

    with patch("services.connectors.linkedin_connector.requests.post", side_effect=[init, post]) as post_call, \
         patch("services.connectors.linkedin_connector.requests.put", return_value=upload) as put_call, \
         patch("services.connectors.linkedin_connector.requests.get", return_value=status) as get_call:
        result = LinkedInConnector(account).publish("hello", image_bytes=b"fake-image")

    assert result.ok is True
    assert result.post_id == "urn:li:share:1"
    assert put_call.call_args.args[0] == "https://upload.example/image"
    assert get_call.call_args.args[0].endswith("/images/urn:li:image:1")
    assert post_call.call_args_list[-1].kwargs["json"]["content"]["media"]["id"] == "urn:li:image:1"


def test_linkedin_image_processing_timeout_is_uncertain():
    from services.connectors.linkedin_connector import LinkedInConnector

    account = MagicMock(access_token="tok", platform_account_id="urn:li:organization:123", platform_config={})
    init = MagicMock(status_code=200)
    init.json.return_value = {"value": {"uploadUrl": "https://upload.example/image", "image": "urn:li:image:2"}}
    upload = MagicMock(status_code=202)
    processing = MagicMock(status_code=200)
    processing.json.return_value = {"status": "PROCESSING"}

    with patch("services.connectors.linkedin_connector.requests.post", return_value=init), \
         patch("services.connectors.linkedin_connector.requests.put", return_value=upload), \
         patch("services.connectors.linkedin_connector.requests.get", return_value=processing), \
         patch("services.connectors.linkedin_connector.time.monotonic", side_effect=[0, 31]):
        result = LinkedInConnector(account).publish("hello", image_bytes=b"fake-image")

    assert result.ok is False
    assert result.outcome == "uncertain"
    assert "timed out" in (result.error or "")


def test_twitter_media_upload_failure_never_degrades_to_text_only():
    from services.connectors.twitter_connector import TwitterConnector

    account = MagicMock(access_token="tok")
    media_response = MagicMock(status_code=500, text="provider unavailable")
    with patch("services.connectors.twitter_connector.requests.post", return_value=media_response) as post:
        result = TwitterConnector(account).publish("hello", image_bytes=b"image")

    assert result.ok is False
    assert result.outcome == "uncertain"
    assert post.call_count == 1
