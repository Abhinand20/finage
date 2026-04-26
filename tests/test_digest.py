from pathlib import Path

import pytest

from finage.digest import DigestService, build_digest_payload, build_digest_prompt
from finage.models import PostEvidence, TickerEvidence, TrendingTicker, WsbSnapshot
from finage.prompting import render_digest_prompt
from finage.settings import Settings


class FakeCollector:
    def __init__(self, snapshot: WsbSnapshot):
        self.snapshot = snapshot

    async def collect(self) -> WsbSnapshot:
        return self.snapshot


class FakeLlm:
    name = "fake"
    model = "fake-model"

    async def generate(self, prompt: str) -> str:
        assert "Evidence JSON" in prompt
        return "WSB Momentum\n- TSLA: test digest.\nNot financial advice; WSB data is noisy."


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        reddit_client_id="reddit-id",
        reddit_client_secret="reddit-secret",
        telegram_bot_token="telegram-token",
        telegram_allowed_ids=[123],
        telegram_default_chat_id=123,
        gemini_api_key="gemini-key",
        data_dir=tmp_path,
    )


def make_snapshot() -> WsbSnapshot:
    return WsbSnapshot(
        subreddit="wallstreetbets",
        trending_tickers=[TrendingTicker(ticker="TSLA", rank=1, mentions=10, upvotes=50)],
        ticker_evidence=[
            TickerEvidence(
                ticker="TSLA",
                trending=TrendingTicker(ticker="TSLA", rank=1, mentions=10, upvotes=50),
                posts=[
                    PostEvidence(
                        id="abc",
                        url="https://www.reddit.com/r/wallstreetbets/comments/abc",
                        title="TSLA catalyst thread",
                        score=500,
                        num_comments=100,
                        mentioned_tickers=["TSLA"],
                    )
                ],
            )
        ],
    )


def test_build_digest_payload_keeps_ticker_evidence() -> None:
    payload = build_digest_payload(make_snapshot())

    assert payload["ticker_evidence"][0]["ticker"] == "TSLA"
    assert payload["ticker_evidence"][0]["posts"][0]["title"] == "TSLA catalyst thread"


def test_build_digest_prompt_includes_required_caveat() -> None:
    prompt = build_digest_prompt(make_snapshot())

    assert "Evidence JSON" in prompt
    assert "TSLA catalyst thread" in prompt


def test_render_digest_prompt_supports_custom_template(tmp_path: Path) -> None:
    prompt_path = tmp_path / "custom_prompt.md"
    prompt_path.write_text("Custom prompt\n{evidence_json}", encoding="utf-8")

    prompt = render_digest_prompt(make_snapshot(), prompt_template_path=prompt_path)

    assert prompt.startswith("Custom prompt")
    assert "TSLA catalyst thread" in prompt


@pytest.mark.asyncio
async def test_digest_service_writes_latest_artifacts(tmp_path: Path) -> None:
    service = DigestService(
        make_settings(tmp_path),
        collector=FakeCollector(make_snapshot()),
        llm_provider=FakeLlm(),
    )

    result = await service.generate()

    assert "TSLA" in result.digest
    assert (tmp_path / "latest_wsb_snapshot.json").exists()
    assert (tmp_path / "latest_digest.json").exists()
