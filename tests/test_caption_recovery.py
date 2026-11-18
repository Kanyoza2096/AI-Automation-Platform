from types import SimpleNamespace

from workflows.post_workflow import GenerateContentStep


def test_caption_quality_failure_is_detected_from_top_level_engine_result():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "error_code": "CONTENT_PIPELINE_FAILED",
        "error": "caption_quality: too short: 5 words (min 80)",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) == result["error"]


def test_caption_quality_failure_is_detected_from_media_artifact():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "media_artifact": SimpleNamespace(
            error_code="CONTENT_PIPELINE_FAILED",
            error="caption_quality: too short: 5 words (min 80)",
        ),
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) == (
        "caption_quality: too short: 5 words (min 80)"
    )


def test_non_caption_pipeline_failure_is_not_repaired():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "error_code": "CONTENT_PIPELINE_FAILED",
        "error": "image renderer unavailable",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) is None


def test_empty_caption_quality_failure_is_not_repaired():
    result = {
        "caption": "  ",
        "publishable": False,
        "error_code": "CONTENT_PIPELINE_FAILED",
        "error": "caption_quality: too short: 0 words (min 80)",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) is None


def test_unrelated_error_code_is_not_repaired_even_with_caption_quality_text():
    result = {
        "caption": "A short generated caption.",
        "publishable": False,
        "error_code": "PROVIDER_UNAVAILABLE",
        "error": "caption_quality: too short: 5 words (min 80)",
    }

    assert GenerateContentStep._caption_quality_failure_reason(result) is None
