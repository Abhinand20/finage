from __future__ import annotations

from typing import Protocol

from finage.artifacts import ArtifactStore
from finage.collector import WsbCollector
from finage.llm import LlmProvider, create_llm_provider
from finage.models import DigestResult, WsbSnapshot
from finage.prompting import build_digest_payload, render_digest_prompt
from finage.settings import Settings


class Collector(Protocol):
    async def collect(self) -> WsbSnapshot:
        ...


def build_digest_prompt(snapshot: WsbSnapshot) -> str:
    return render_digest_prompt(snapshot)


class DigestService:
    def __init__(
        self,
        settings: Settings,
        *,
        collector: Collector | None = None,
        llm_provider: LlmProvider | None = None,
        artifact_store: ArtifactStore | None = None,
    ):
        self.settings = settings
        self.collector = collector or WsbCollector(settings)
        self.llm_provider = llm_provider or create_llm_provider(settings)
        self.artifact_store = artifact_store or ArtifactStore(settings.data_dir)

    async def generate(self) -> DigestResult:
        previous_digest = self.artifact_store.read_latest_digest()
        snapshot = await self.collector.collect()
        snapshot_path = self.artifact_store.write_snapshot(snapshot)
        prompt = render_digest_prompt(
            snapshot,
            previous_digest=previous_digest,
            prompt_template_path=self.settings.digest_prompt_path,
        )
        # print('-' * 20)
        # print("Sending the following prompt to the LLM:")
        # print(prompt)
        # print('-' * 20)
        digest_text = await self.llm_provider.generate(prompt)
        result = DigestResult(
            provider=self.llm_provider.name,
            model=self.llm_provider.model,
            digest=digest_text,
            snapshot_path=str(snapshot_path),
        )
        self.artifact_store.write_digest(result)
        return result
