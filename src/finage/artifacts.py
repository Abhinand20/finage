from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from finage.models import DigestResult, WsbSnapshot


class ArtifactStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def write_snapshot(self, snapshot: WsbSnapshot) -> Path:
        return self._write_model("latest_wsb_snapshot.json", snapshot)

    def write_digest(self, digest: DigestResult) -> Path:
        return self._write_model("latest_digest.json", digest)

    def _write_model(self, filename: str, model: BaseModel) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / filename
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        return path
