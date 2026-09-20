from types import SimpleNamespace


def account(platform, account_id="acct"):
    return SimpleNamespace(platform=platform, provider_account_id=account_id, access_token="token", platform_account_id=account_id)


def test_telegram_image_url_is_not_dropped(monkeypatch):
    from services.connectors.telegram_connector import TelegramConnector
    c = TelegramConnector(account("telegram", "@channel"))
    seen = {}
    class R:
        status_code = 200
        def json(self):
            return {"ok": True, "result": {"message_id": 9}}
    def post(url, **kwargs):
        seen.update(kwargs)
        return R()
    monkeypatch.setattr(c._http, "post", post)
    r = c.publish("hello", image_url="https://cdn.example/x.jpg")
    assert r.ok and r.post_id == "9"
    assert seen["json"]["photo"] == "https://cdn.example/x.jpg"


def test_whatsapp_image_url_is_not_dropped(monkeypatch):
    from services.connectors.whatsapp_connector import WhatsAppConnector
    c = WhatsAppConnector(account("whatsapp", "phone"))
    class R:
        status_code = 200
        def json(self):
            return {"messages": [{"id": "wamid.1"}]}
    seen = {}
    def post(url, **kwargs):
        seen.update(kwargs)
        return R()
    monkeypatch.setattr("services.connectors.whatsapp_connector.requests.post", post)
    r = c.publish("hello", image_url="https://cdn.example/x.jpg", to_number="265999000000")
    assert r.ok and r.post_id == "wamid.1"
    assert seen["json"]["type"] == "image"
    assert seen["json"]["image"]["link"] == "https://cdn.example/x.jpg"


def test_whatsapp_raw_media_is_rejected_instead_of_dropped():
    from services.connectors.whatsapp_connector import WhatsAppConnector
    c = WhatsAppConnector(account("whatsapp", "phone"))
    r = c.publish("hello", image_bytes=b"x", to_number="265999000000")
    assert not r.ok
    assert r.outcome == "failed"


def test_instagram_container_timeout_is_uncertain(monkeypatch):
    from services.connectors.instagram_connector import InstagramConnector
    c = InstagramConnector(account("instagram", "ig"))
    calls = []
    class R:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    def post(url, **kwargs):
        calls.append(("post", url))
        return R({"id": "container"})
    def get(url, **kwargs):
        calls.append(("get", url))
        return R({"status_code": "IN_PROGRESS"})
    monkeypatch.setattr("services.connectors.instagram_connector.requests.post", post)
    monkeypatch.setattr("services.connectors.instagram_connector.requests.get", get)
    monkeypatch.setattr("services.connectors.instagram_connector.time.monotonic", lambda: 100.0)
    r = c.publish("hello", image_url="https://cdn.example/x.jpg", container_timeout=0)
    assert not r.ok
    assert r.outcome == "uncertain"
