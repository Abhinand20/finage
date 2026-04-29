from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from finage.models import DigestResult, WsbSnapshot

LATEST_DIGEST_FILENAME = "latest_digest.json"
LATEST_SNAPSHOT_FILENAME = "latest_wsb_snapshot.json"


class ArtifactStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def write_snapshot(self, snapshot: WsbSnapshot) -> Path:
        return self._write_model(LATEST_SNAPSHOT_FILENAME, snapshot)

    def write_digest(self, digest: DigestResult) -> Path:
        return self._write_model(LATEST_DIGEST_FILENAME, digest)

    def read_latest_snapshot(self) -> WsbSnapshot | None:
        path = self.data_dir / LATEST_SNAPSHOT_FILENAME
        if not path.exists():
            return None
        return WsbSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def read_latest_digest(self) -> DigestResult | None:
        path = self.data_dir / LATEST_DIGEST_FILENAME
        if not path.exists():
            return None
        return DigestResult.model_validate_json(path.read_text(encoding="utf-8"))

    def _write_model(self, filename: str, model: BaseModel) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / filename
        path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        return path
