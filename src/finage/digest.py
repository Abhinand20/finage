from __future__ import annotations

import logging
from typing import Protocol

from finage.artifacts import ArtifactStore
from finage.collector import WsbCollector
from finage.congress import CongressAnalyzer, CongressCollector
from finage.llm import LlmProvider, create_llm_provider
from finage.models import DigestResult, WsbSnapshot
from finage.prompting import render_congress_digest_prompt, render_digest_prompt, render_whale_digest_prompt
from finage.settings import Settings
from finage.whale import WhaleAnalyzer, WhaleCollector, format_whale_changes, format_whale_filing_updates
from finage.web_search import (
    WebSearchOptions,
    WebSearchProvider,
    WebSearchResponse,
    create_web_search_provider,
)

logger = logging.getLogger(__name__)


class Collector(Protocol):
    async def collect(self) -> WsbSnapshot:
        ...


class CongressDataSource(Protocol):
    async def get_or_fetch(self):
        ...

    def read_new_trades(self):
        ...


class WhaleDataSource(Protocol):
    async def get_or_fetch(self):
        ...

    def read_latest_changes(self):
        ...


def build_digest_prompt(snapshot: WsbSnapshot) -> str:
    return render_digest_prompt(snapshot)


def build_ticker_web_search_query(ticker: str) -> str:
    return f"{ticker} stock latest news earnings analyst catalyst"


class DigestService:
    def __init__(
        self,
        settings: Settings,
        *,
        collector: Collector | None = None,
        llm_provider: LlmProvider | None = None,
        artifact_store: ArtifactStore | None = None,
        web_search_provider: WebSearchProvider | None = None,
        congress_collector: CongressDataSource | None = None,
        whale_collector: WhaleDataSource | None = None,
    ):
        self.settings = settings
        self.collector = collector or WsbCollector(settings)
        self.llm_provider = llm_provider or create_llm_provider(settings)
        self.artifact_store = artifact_store or ArtifactStore(settings.data_dir)
        self.web_search_provider = web_search_provider or create_web_search_provider(settings)
        self.congress_collector = congress_collector or CongressCollector(settings)
        self.whale_collector = whale_collector or WhaleCollector(settings)

    async def _congress_prompt_section(self, snapshot: WsbSnapshot) -> str:
        if not self.settings.congress_enabled:
            return ""
        try:
            congress_snapshot = await self.congress_collector.get_or_fetch()
            new_trades = self.congress_collector.read_new_trades()
        except Exception:
            logger.exception("Congress enrichment failed; continuing without congress section")
            return ""

        analyzer = CongressAnalyzer()
        signals = analyzer.top_signals(
            congress_snapshot.trades,
            new_trades=new_trades,
            lookback_days=self.settings.congress_lookback_days,
            min_score=self.settings.congress_min_signal_score,
        )
        evidence_by_ticker = {item.ticker: item for item in snapshot.ticker_evidence}
        signals = analyzer.apply_convergence_scores(signals, snapshot.trending_tickers, evidence_by_ticker)
        reddit_tickers = [item.ticker for item in snapshot.trending_tickers[:10]]
        overlap = sorted(analyzer.overlap_tickers(signals, reddit_tickers))
        lines = [analyzer.format_signal_line(signal, overlap=signal.ticker in overlap) for signal in signals]
        congress_section = "\n".join(lines) if lines else "No notable congressional activity this period."
        return render_congress_digest_prompt(
            congress_section_markdown=congress_section,
            lookback_days=self.settings.congress_lookback_days,
            overlap_tickers=overlap,
        )

    async def _whale_prompt_section(self, snapshot: WsbSnapshot) -> str:
        if not self.settings.whale_enabled:
            return ""
        try:
            whale_snapshot = await self.whale_collector.get_or_fetch()
        except Exception:
            logger.exception("Whale enrichment failed; continuing without whale section")
            return ""

        analyzer = WhaleAnalyzer()
        changes = self.whale_collector.read_latest_changes()
        signals = analyzer.apply_convergence(
            whale_snapshot.signals,
            reddit_tickers=snapshot.trending_tickers,
            congress_signals=None,
        )
        lines = [analyzer.format_signal_line(signal) for signal in signals[:5]]
        signal_lines = "\n".join(lines) if lines else "No notable whale 13F momentum this period."
        return render_whale_digest_prompt(
            signal_lines=signal_lines,
            filing_updates=format_whale_filing_updates(whale_snapshot, changes),
            refresh_changes=format_whale_changes(changes),
        )

    async def _web_search_by_ticker(self, snapshot: WsbSnapshot) -> dict[str, WebSearchResponse]:
        if not self.web_search_provider or not self.settings.digest_web_search_enabled:
            return {}

        options = WebSearchOptions(
            num_results=self.settings.web_search_num_results,
            content_mode=self.settings.web_search_content_mode,
        )
        results: dict[str, WebSearchResponse] = {}
        for evidence in snapshot.ticker_evidence:
            query = build_ticker_web_search_query(evidence.ticker)
            try:
                response = await self.web_search_provider.search(query, options)
            except Exception:
                logger.exception("Web search failed for ticker=%s query=%r", evidence.ticker, query)
                continue

            if response.results:
                results[evidence.ticker] = response

        return results

    async def generate(self) -> DigestResult:
        logger.info(
            "Starting digest generation with provider=%s, model=%s",
            self.llm_provider.name,
            self.llm_provider.model,
        )
        previous_digest = self.artifact_store.read_latest_digest()
        logger.info("Previous digest artifact found=%s", previous_digest is not None)
        snapshot = await self.collector.collect()
        snapshot_path = self.artifact_store.write_snapshot(snapshot)
        logger.info(
            "Wrote snapshot artifact to %s with tickers_with_evidence=%s",
            snapshot_path,
            len(snapshot.ticker_evidence),
        )
        web_search_by_ticker = await self._web_search_by_ticker(snapshot)
        logger.info("Web search enrichment complete for tickers=%s", len(web_search_by_ticker))
        prompt = render_digest_prompt(
            snapshot,
            previous_digest=previous_digest,
            web_search_by_ticker=web_search_by_ticker,
            prompt_template_path=self.settings.digest_prompt_path,
            prompt_bundle=self.settings.digest_prompt_bundle,
        )
        congress_prompt = await self._congress_prompt_section(snapshot)
        if congress_prompt:
            prompt = f"{prompt}\n\n{congress_prompt}"
        whale_prompt = await self._whale_prompt_section(snapshot)
        if whale_prompt:
            prompt = f"{prompt}\n\n{whale_prompt}"
        logger.info("Rendered digest prompt with %s characters", len(prompt))
        digest_text = await self.llm_provider.generate(prompt)
        result = DigestResult(
            provider=self.llm_provider.name,
            model=self.llm_provider.model,
            digest=digest_text,
            snapshot_path=str(snapshot_path),
        )
        digest_path = self.artifact_store.write_digest(result)
        logger.info(
            "Digest generation complete: digest_chars=%s, digest_artifact=%s",
            len(digest_text),
            digest_path,
        )
        return result
