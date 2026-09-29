import os
import shutil

import pytest

from data_scraping import registry, checks
from data_viz import generate_visuals as gv

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT = os.path.join(REPO, "output")


def test_registry_keys_match_spec_keys():
    assert all(k == s.key for k, s in registry.SOURCES.items())


def test_every_target_source_is_registered():
    declared = {s for sources in gv.TARGET_SOURCES.values() for s in sources}
    assert declared <= set(registry.SOURCES)


def test_scrape_paths_are_strings_not_imports():
    # web must be able to import the registry without Selenium installed
    assert all(s.scrape is None or ":" in s.scrape for s in registry.SOURCES.values())


def test_national_das_has_extended_time_limit_for_a_full_replay():
    # A rollback ("replay to here") re-ingests every published DAS workbook up to the target,
    # oldest first, in one transaction -- that can run long as history accumulates.
    assert registry.get_source("nationalDAS").time_limit == 1800


@pytest.mark.skipif(not os.path.isdir(OUTPUT), reason="needs the local output/ snapshot")
class TestRealFilesPassTier1:
    @pytest.mark.parametrize("key", [k for k, s in registry.SOURCES.items() if s.contract])
    def test_current_file_passes(self, key):
        spec = registry.get_source(key)
        files = sorted(f for f in os.listdir(OUTPUT) if f.endswith(f"_{key}.{spec.contract.ext}"))
        if not files:
            pytest.skip(f"no {key} file in output/")
        assert checks.tier1(os.path.join(OUTPUT, files[-1]), spec.contract).ok


@pytest.mark.skipif(not os.path.isdir(OUTPUT), reason="needs the local output/ snapshot")
class TestTargetSourcesCoverage:
    """Each cleaner must succeed with ONLY its declared sources present -- an undeclared read raises
    FileNotFoundError under strict=True. Uses a stub writer so no DB is needed."""

    @pytest.mark.parametrize("target", sorted(gv.TARGET_SOURCES))
    def test_cleaner_reads_only_declared_sources(self, target, tmp_path, monkeypatch):
        for source in gv.TARGET_SOURCES[target]:
            matches = sorted(f for f in os.listdir(OUTPUT) if f"_{source}." in f)
            if not matches:
                pytest.skip(f"no {source} file in output/")
            shutil.copy(os.path.join(OUTPUT, matches[-1]), tmp_path / matches[-1])

        class StubVisual:
            def __getattr__(self, name):
                return lambda *a, **k: self

        class StubWriter:
            def visual(self, *a, **k):
                return StubVisual()

        with gv.use_output_dir(str(tmp_path)):
            gv.V1_DIRECT[target](StubWriter(), target)


def test_context_driver_is_lazy_and_closed(monkeypatch, tmp_path):
    from data_scraping import context
    started = []

    class FakeDriver:
        def quit(self):
            started.append("quit")
    monkeypatch.setattr(context, "_start_driver", lambda download_dir: started.append("start") or FakeDriver())
    ctx = context.ScrapeContext(str(tmp_path), None, None)
    ctx.close()
    assert started == []
    ctx.driver
    ctx.driver
    ctx.close()
    assert started == ["start", "quit"]
