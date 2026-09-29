from pathlib import Path
import tempfile

from atlas.assets.service import AssetService
from atlas.core.config import AppConfig
from atlas.memories.service import MemoryService


class ObservedAssets(AssetService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.snapshot_count = 0

    def snapshot(self):
        # 只计数，仍调用真实清单快照与深拷贝。
        self.snapshot_count += 1
        return super().snapshot()


def test_memory_cache_skips_snapshot_and_refresh_invalidates():
    scratch = Path(__file__).resolve().parents[3] / '.agentatlas/work'
    with tempfile.TemporaryDirectory(dir=scratch) as temp:
        home = Path(temp).resolve()
        path = home / '.hermes/memories/MEMORY.md'
        path.parent.mkdir(parents=True)
        path.write_text('# 第一次\n', encoding='utf-8')
        config = AppConfig(workspace=home, home=home, scan_roots=[])
        assets = ObservedAssets(config, lambda: {'files': []})
        assets.refresh()
        service = MemoryService(assets, lambda: {'files': []}, home)
        first = service.view()
        assert first['files'][0]['memoryInfo']['title'] == '第一次'
        assert service.view() is first
        assert assets.snapshot_count == 1
        path.write_text('# 第二次\n', encoding='utf-8')
        assets.refresh()
        second = service.view()
        assert second['catalogVersion'] != first['catalogVersion']
        assert second['files'][0]['memoryInfo']['title'] == '第二次'
        assert assets.snapshot_count == 2
