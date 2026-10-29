from core.execution_identity import make_publish_idempotency_key


def test_publish_idempotency_is_scoped_to_destination_account():
    a = make_publish_idempotency_key(
        "ws", "brand", "facebook", "page-a", "topic", "caption", "", b"img"
    )
    b = make_publish_idempotency_key(
        "ws", "brand", "facebook", "page-b", "topic", "caption", "", b"img"
    )
    assert a != b


def test_publish_idempotency_changes_when_media_changes():
    a = make_publish_idempotency_key(
        "ws", "brand", "facebook", "page-a", "topic", "caption", "", b"img-a"
    )
    b = make_publish_idempotency_key(
        "ws", "brand", "facebook", "page-a", "topic", "caption", "", b"img-b"
    )
    assert a != b
