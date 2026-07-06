"""Unit tests for vision-based procedure extraction — the vision LLM is
mocked, no network. Focused on the two-pass verification added after a live
test showed the model fabricating a procedure name on a blank image instead
of emitting the VISION_UNCERTAIN sentinel roughly half the time."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.vision as vision_module
from app.schemas.vision import VisionExtraction, VisionVerification
from app.services.vision import VISION_UNCERTAIN, extract_procedure_query


@pytest.fixture(autouse=True)
def reset_structured_output_cache():
    """_structured_output_ok is module-level state (a perf memo of whether
    with_structured_output works for a given model) — must reset between
    tests or one test's simulated failure silently skips the structured
    attempt in a later, unrelated test using the same model name."""
    vision_module._structured_output_ok.clear()
    yield
    vision_module._structured_output_ok.clear()


def _mock_llm(by_schema: dict, raw_content_by_schema: dict | None = None):
    """by_schema: {SchemaClass: return_value_or_Exception} for the structured
    (primary) path. raw_content_by_schema: {SchemaClass: str} for the raw
    ainvoke fallback path, keyed by whichever schema's structured call failed."""
    mock_llm = MagicMock()

    def _with_structured_output(schema):
        mock_structured = AsyncMock()
        result = by_schema.get(schema)
        if isinstance(result, Exception):
            mock_structured.ainvoke = AsyncMock(side_effect=result)
        else:
            mock_structured.ainvoke = AsyncMock(return_value=result)
        return mock_structured

    mock_llm.with_structured_output.side_effect = _with_structured_output

    if raw_content_by_schema:
        calls = iter(raw_content_by_schema.values())

        async def _raw_ainvoke(*args, **kwargs):
            resp = MagicMock()
            resp.content = next(calls)
            return resp

        mock_llm.ainvoke = AsyncMock(side_effect=_raw_ainvoke)
    return mock_llm


@pytest.mark.asyncio
async def test_extract_returns_price_question_when_legible_and_verified():
    mock_llm = _mock_llm({
        VisionExtraction: VisionExtraction(is_legible=True, price_question="¿Cuánto cuesta un examen de IGRA?"),
        VisionVerification: VisionVerification(text_visible=True),
    })
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == "¿Cuánto cuesta un examen de IGRA?"


@pytest.mark.asyncio
async def test_extract_returns_uncertain_when_illegible_without_calling_verification():
    mock_llm = _mock_llm({VisionExtraction: VisionExtraction(is_legible=False, price_question=None)})
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == VISION_UNCERTAIN
    # Only the extraction schema should have been requested — no wasted
    # verification call when there's nothing to verify.
    called_schemas = [c.args[0] for c in mock_llm.with_structured_output.call_args_list]
    assert VisionVerification not in called_schemas


@pytest.mark.asyncio
async def test_extract_returns_uncertain_when_legible_but_no_question():
    mock_llm = _mock_llm({VisionExtraction: VisionExtraction(is_legible=True, price_question=None)})
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == VISION_UNCERTAIN


@pytest.mark.asyncio
async def test_extract_returns_uncertain_when_verification_rejects_the_claim():
    """The core fix: the model claiming is_legible=true isn't trusted on its
    own — a second independent call re-examining the image can still veto it."""
    mock_llm = _mock_llm({
        VisionExtraction: VisionExtraction(is_legible=True, price_question="¿Cuánto cuesta una colonoscopía?"),
        VisionVerification: VisionVerification(text_visible=False),
    })
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == VISION_UNCERTAIN


@pytest.mark.asyncio
async def test_extract_falls_back_to_json_parse_when_extraction_structured_fails():
    """Once extraction's structured attempt fails for a model, the memo means
    verification (same model, same call) also skips straight to the JSON
    fallback — both raw responses must be supplied."""
    mock_llm = _mock_llm(
        by_schema={VisionExtraction: Exception("model doesn't support tool calling")},
        raw_content_by_schema={
            VisionExtraction: '{"is_legible": true, "price_question": "¿Cuánto cuesta una biopsia de mama?"}',
            VisionVerification: '{"text_visible": true}',
        },
    )
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == "¿Cuánto cuesta una biopsia de mama?"


@pytest.mark.asyncio
async def test_extract_falls_back_to_json_parse_strips_markdown_fences():
    mock_llm = _mock_llm(
        by_schema={VisionExtraction: Exception("no tool calling")},
        raw_content_by_schema={VisionExtraction: '```json\n{"is_legible": false, "price_question": null}\n```'},
    )
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == VISION_UNCERTAIN


@pytest.mark.asyncio
async def test_extract_defaults_to_uncertain_when_extraction_totally_fails():
    mock_llm = _mock_llm(
        by_schema={VisionExtraction: Exception("api down")},
        raw_content_by_schema={VisionExtraction: "not json at all, just prose"},
    )
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == VISION_UNCERTAIN


@pytest.mark.asyncio
async def test_extract_defaults_to_uncertain_when_verification_totally_fails():
    """Safe-default direction matters: unlike triage (defaults to a guess),
    vision's safe default on total failure must be uncertainty — including
    when only the verification pass is the one that breaks."""
    mock_llm = _mock_llm(
        by_schema={
            VisionExtraction: VisionExtraction(is_legible=True, price_question="¿Cuánto cuesta un examen X?"),
            VisionVerification: Exception("api down"),
        },
        raw_content_by_schema={VisionVerification: "not json either"},
    )
    with (
        patch("app.services.vision.settings.openai_vision_model", "some-vision-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result = await extract_procedure_query(b"fake image bytes", "")

    assert result == VISION_UNCERTAIN


@pytest.mark.asyncio
async def test_extract_raises_when_vision_model_not_configured():
    with patch("app.services.vision.settings.openai_vision_model", ""):
        with pytest.raises(RuntimeError):
            await extract_procedure_query(b"fake image bytes", "")


@pytest.mark.asyncio
async def test_memoized_structured_output_failure_skips_retry_on_next_call():
    """Live testing found the configured vision model hard-fails (400/404)
    on every structured-output method OpenRouter offers — not just bad text
    back. That means retrying the structured attempt on every call is a
    guaranteed-failing network round-trip paid on every single image. Once a
    model is known not to support it, subsequent calls must skip straight to
    the JSON-mode fallback instead of repeating the doomed attempt."""
    mock_llm = MagicMock()
    mock_structured = AsyncMock()
    mock_structured.ainvoke = AsyncMock(side_effect=Exception("no tool calling"))
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)

    raw_resp = MagicMock()
    raw_resp.content = '{"is_legible": false, "price_question": null}'
    mock_llm.ainvoke = AsyncMock(return_value=raw_resp)

    with (
        patch("app.services.vision.settings.openai_vision_model", "flaky-model"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm),
    ):
        result1 = await extract_procedure_query(b"img1", "")
        assert result1 == VISION_UNCERTAIN
        assert mock_llm.with_structured_output.call_count == 1

        result2 = await extract_procedure_query(b"img2", "")
        assert result2 == VISION_UNCERTAIN
        # Second call must NOT retry the doomed structured attempt.
        assert mock_llm.with_structured_output.call_count == 1
        assert mock_llm.ainvoke.call_count == 2


@pytest.mark.asyncio
async def test_structured_output_memo_is_isolated_per_model():
    """A different model name must not inherit another model's memoized
    "structured output doesn't work" result."""
    mock_llm_a = MagicMock()
    mock_structured_a = AsyncMock()
    mock_structured_a.ainvoke = AsyncMock(side_effect=Exception("no tool calling"))
    mock_llm_a.with_structured_output = MagicMock(return_value=mock_structured_a)
    raw_resp = MagicMock()
    raw_resp.content = '{"is_legible": false, "price_question": null}'
    mock_llm_a.ainvoke = AsyncMock(return_value=raw_resp)

    with (
        patch("app.services.vision.settings.openai_vision_model", "model-a"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm_a),
    ):
        await extract_procedure_query(b"img1", "")

    mock_llm_b = MagicMock()
    mock_structured_b = AsyncMock()
    mock_structured_b.ainvoke = AsyncMock(
        return_value=VisionExtraction(is_legible=False, price_question=None)
    )
    mock_llm_b.with_structured_output = MagicMock(return_value=mock_structured_b)

    with (
        patch("app.services.vision.settings.openai_vision_model", "model-b"),
        patch("app.services.vision.get_vision_llm", return_value=mock_llm_b),
    ):
        await extract_procedure_query(b"img2", "")

    # model-b's structured attempt must still be tried — model-a's failure
    # memo must not leak across model names.
    mock_llm_b.with_structured_output.assert_called_once()
