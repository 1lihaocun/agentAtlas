import json
import os
from pathlib import Path

from fastapi import Request

from atlas.assets.service import AssetService
from atlas.core.storage import Store
from atlas.duplicates.service import DuplicateService
from atlas.files.service import FileStore
from atlas.guides.service import FileGuide
from atlas.guides.provenance import ProvenanceService
from atlas.jobs.service import JobService
from atlas.memories.service import MemoryService
from atlas.reviews.service import ReviewService
from atlas.search.index import SearchIndex
from atlas.search.service import SearchService
from atlas.settings.service import SettingsStore
from atlas.translation.service import TranslationService
from atlas.app.workflows import Workflows


class Services:
    def __init__(self, config):
        self.config = config
        self.assets = AssetService(config, self.load_instructions)
        self.files = FileStore(self.assets.files, config.state_dir / "backups")
        self.settings = SettingsStore(config.workspace)
        self.index = SearchIndex(str(config.state_dir / "search.sqlite3"))
        self.jobs = JobService()
        self.memories = MemoryService(self.assets, self.load_instructions, config.home)
        self.guide = FileGuide(config.guide_path)
        codex_home = config.codex_home or (Path(os.environ.get("CODEX_HOME") or config.home / ".codex").expanduser().resolve()
                                           if config.home == Path.home() else config.home / ".codex")
        self.provenance = ProvenanceService(guide=self.guide, home=config.home, codex_home=codex_home)
        self.reviews = ReviewService(self.load_instructions, self.files,
                                    Store(config.state_dir / "lifecycle"), codex_home / "sessions", codex_home)
        self.duplicates = DuplicateService(self.load_instructions, self.files)
        self.search = SearchService(self.index, self.settings, self.assets, config.state_dir)
        self.translation = TranslationService(self.files, config.workspace, config.state_dir)
        self.workflows = Workflows(config, self.assets, self.files, self.index, self.jobs, self.settings)

    def load_instructions(self):
        path = self.config.instruction_path
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def services(request: Request) -> Services:
    return request.app.state.services
