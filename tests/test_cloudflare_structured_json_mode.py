import json

import httpx

from services.connectors.cloudflare_ai_adapter import CloudflareAIAdapter


def test_structured_completion_requests_cloudflare_json_mode():
    observed = {}

    def handler(request):
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "result": {
                    "choices": [{
                        "message": {
                            "content": '{"title":"A valid title","excerpt":"A useful excerpt","body":"Article body","citations":["kb-1"]}'
                        },
                        "finish_reason": "stop",
                    }]
                }
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = CloudflareAIAdapter(
        account_id="test-account",
        token="test-token",
        model="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        client=client,
    )
    try:
        receipt = adapter.analyze(
            user_prompt="Generate a structured article",
            correlation_id="test-correlation",
            structured=True,
            max_output_tokens=4000,
        )
    finally:
        adapter.close()
        client.close()

    assert observed["body"]["response_format"] == {"type": "json_object"}
    assert receipt.status == "succeeded"
    assert receipt.structured_output == {
        "title": "A valid title",
        "excerpt": "A useful excerpt",
        "body": "Article body",
        "citations": ["kb-1"],
    }
    assert receipt.finish_reason == "stop"


def test_chat_completion_does_not_force_cloudflare_json_mode():
    observed = {}

    def handler(request):
        observed["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"result": {"response": "A normal chat response"}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = CloudflareAIAdapter(
        account_id="test-account",
        token="test-token",
        client=client,
    )
    try:
        receipt = adapter.analyze(
            user_prompt="Say hello",
            correlation_id="test-chat-correlation",
            structured=False,
        )
    finally:
        adapter.close()
        client.close()

    assert "response_format" not in observed["body"]
    assert receipt.status == "succeeded"
    assert receipt.raw_output == "A normal chat response"
