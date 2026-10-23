from __future__ import annotations

import asyncio
import gzip
import importlib
import io
import json
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "prts_plugin_under_test"


def _bootstrap_package() -> None:
    if PACKAGE in sys.modules:
        return
    module = types.ModuleType(PACKAGE)
    module.__path__ = [str(PLUGIN_ROOT)]
    sys.modules[PACKAGE] = module


_bootstrap_package()
corpus = importlib.import_module(f"{PACKAGE}.corpus")
config_module = importlib.import_module(f"{PACKAGE}.config")
startup = importlib.import_module(f"{PACKAGE}.startup")


def _gzip_jsonl(rows: list[dict]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb") as stream:
        for row in rows:
            stream.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    return buffer.getvalue()


class FakeReleaseServer:
    def __init__(
        self, release_id: str, data_version: str, packs: list[str], *, text: str = "内容", fail_manifest: bool = False
    ):
        self.release_id = release_id
        self.data_version = data_version
        self.packs = list(packs)
        self.fail_manifest = fail_manifest
        self.current_requests = 0
        self.manifest_requests = 0
        self.download_requests = 0
        self.assets: dict[tuple[str, str, str], bytes] = {}
        for pack in self.packs:
            shard_path = "shards/00000.jsonl.gz"
            catalog_path = "catalog/documents.jsonl.gz"
            document = {
                "document_id": f"{release_id}:{pack}:doc",
                "display_title": f"{release_id}-{pack}",
                "document_type": "reference",
                "document_kind": "reference",
                "document_category": "reference",
                "source_ref_prefix": f"{release_id}:{pack}",
                "line_count": 1,
            }
            self.assets[(release_id, pack, shard_path)] = _gzip_jsonl(
                [{"lines": [{"line_number": 1, "line_type": "knowledge", "speaker_raw": "", "text": text}]}]
            )
            self.assets[(release_id, pack, catalog_path)] = _gzip_jsonl(
                [{"document": document, "shard_path": shard_path, "record_index": 0, "search_index_id": 1}]
            )

    def _manifest(self, pack: str) -> dict:
        catalog_path = "catalog/documents.jsonl.gz"
        shard_path = "shards/00000.jsonl.gz"
        return {
            "pack_id": pack,
            "document_count": 1,
            "line_count": 1,
            "document_catalog": {
                "path": catalog_path,
                "compressed_size": len(self.assets[(self.release_id, pack, catalog_path)]),
                "sha256": "b" * 64,
            },
            "shards": [
                {
                    "path": shard_path,
                    "compressed_size": len(self.assets[(self.release_id, pack, shard_path)]),
                    "sha256": "c" * 64,
                }
            ],
        }

    def respond(self, url: str, *, timeout: int) -> dict:
        if url == corpus.CURRENT_URL:
            self.current_requests += 1
            return {
                "code": 200,
                "data": {"release_id": self.release_id, "data_version": self.data_version, "mirrors": []},
            }
        if url.endswith("/release-manifest.json"):
            self.manifest_requests += 1
            if self.fail_manifest:
                raise corpus.CorpusError("模拟 release-manifest 失败")
            return {
                "release_id": self.release_id,
                "data_version": self.data_version,
                "document_count": len(self.packs),
                "packs": [{"pack_id": pack, "manifest_path": f"{pack}/pack-manifest.json"} for pack in self.packs],
                "licenses": [{"pack_id": pack, "public_distribution_approved": True} for pack in self.packs],
            }
        for pack in self.packs:
            if url.endswith(f"/{pack}/pack-manifest.json"):
                return self._manifest(pack)
        raise AssertionError(f"unexpected url: {url}")


def install_fake_remote(monkeypatch: pytest.MonkeyPatch, state: dict) -> None:
    def fake_request(url: str, *, timeout: int) -> dict:
        return state["server"].respond(url, timeout=timeout)

    def fake_download(urls, destination: Path, *, size: int, sha256: str, timeout: int) -> None:
        state["server"].download_requests += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = str(destination)
        for (release_id, pack, relative), content in state["server"].assets.items():
            if target.endswith(f"/{release_id}/{pack}/{relative}"):
                destination.write_bytes(content)
                return
        destination.write_bytes(b"\0" * size)

    monkeypatch.setattr(corpus, "_request_json", fake_request)
    monkeypatch.setattr(corpus, "_download_verified", fake_download)


def read_pointer(data_dir: Path) -> dict:
    return json.loads((data_dir / "releases" / "current.json").read_text(encoding="utf-8"))


def test_same_release_skips_without_download_or_pointer_change(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    server = FakeReleaseServer("r1", "a" * 64, ["references"])
    install_fake_remote(monkeypatch, {"server": server})
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)
    before = read_pointer(data_dir)

    server.current_requests = 0
    server.manifest_requests = 0
    server.download_requests = 0
    result = corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert result["updated"] is False
    assert result["release_id"] == "r1"
    assert server.current_requests == 1
    assert server.manifest_requests == 0
    assert server.download_requests == 0
    assert read_pointer(data_dir) == before


def test_new_release_switches_pointer(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"])}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"])
    result = corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert result["updated"] is True
    assert result["release_id"] == "r2"
    assert read_pointer(data_dir)["release_id"] == "r2"


def test_failure_keeps_old_pointer_and_readable_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"], text="旧内容")}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"], text="新内容", fail_manifest=True)
    with pytest.raises(corpus.CorpusError):
        corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert read_pointer(data_dir)["release_id"] == "r1"
    store = corpus.CorpusStore(data_dir)
    assert store.search("旧内容", pack_ids=["references"])["count"] == 1


def test_auto_update_adds_missing_pack(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references", "entities"])}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    result = corpus.auto_update_if_needed(data_dir, ["references", "entities"], timeout=10)

    assert result["updated"] is True
    assert set(result["packs"]) == {"references", "entities"}


def test_auto_update_keeps_installed_packs_beyond_config(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    server = FakeReleaseServer("r1", "a" * 64, ["references", "entities", "official_game"])
    install_fake_remote(monkeypatch, {"server": server})
    corpus.sync_packs_locked(data_dir, ["references", "entities", "official_game"], timeout=10)

    server.current_requests = 0
    server.download_requests = 0
    result = corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert result["updated"] is False
    assert set(result["packs"]) == {"references", "entities", "official_game"}


def test_auto_update_recovers_after_failure(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"])}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"], fail_manifest=True)
    with pytest.raises(corpus.CorpusError):
        corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"])
    result = corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert result["updated"] is True
    assert result["release_id"] == "r2"


def test_same_store_reads_new_release_after_auto_update(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"], text="旧内容")}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    store = corpus.CorpusStore(data_dir)
    assert store.search("旧内容", pack_ids=["references"])["count"] == 1

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"], text="新内容")
    result = corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert result["updated"] is True
    search = store.search("新内容", pack_ids=["references"])
    assert search["count"] == 1
    assert search["data_version"] == "b" * 64
    assert store.search("旧内容", pack_ids=["references"])["count"] == 0


def test_manual_and_auto_sync_share_one_lock(tmp_path, monkeypatch):
    active = 0
    peak = 0
    guard = threading.Lock()

    def fake_sync(data_dir, packs, *, timeout=120, current_payload=None):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return {"release_id": "r1", "data_version": "a" * 64, "packs": list(packs), "synced_at": ""}

    monkeypatch.setattr(corpus, "sync_packs", fake_sync)
    monkeypatch.setattr(
        corpus,
        "_request_json",
        lambda url, *, timeout: {"code": 200, "data": {"release_id": "r1", "data_version": "a" * 64, "mirrors": []}},
    )

    threads = [
        threading.Thread(target=corpus.sync_packs_locked, args=(tmp_path, ["references"])),
        threading.Thread(target=corpus.auto_update_if_needed, args=(tmp_path, ["references"])),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak == 1


def write_gzip_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_gzip_jsonl(rows))


def test_entity_names_refreshes_on_data_version_change(tmp_path):
    data_dir = tmp_path / "data"
    release_dir = data_dir / "releases" / "r1" / "entities"
    catalog = release_dir / "catalog" / "documents.jsonl.gz"
    write_gzip_jsonl(catalog, [{"document": {"display_title": "孤星", "document_id": "client:entities:lonestar"}}])
    (release_dir / "pack-manifest.json").write_text(
        json.dumps({"pack_id": "entities", "document_catalog": {"path": "catalog/documents.jsonl.gz"}}),
        encoding="utf-8",
    )
    pointer_path = data_dir / "releases" / "current.json"
    pointer_path.write_text(
        json.dumps({"release_id": "r1", "data_version": "a" * 64, "packs": ["entities"]}),
        encoding="utf-8",
    )

    store = corpus.CorpusStore(data_dir)
    assert store.contains_entity("孤星剧情")

    write_gzip_jsonl(catalog, [{"document": {"display_title": "霜星", "document_id": "client:entities:frostnova"}}])
    pointer_path.write_text(
        json.dumps({"release_id": "r1", "data_version": "b" * 64, "packs": ["entities"]}),
        encoding="utf-8",
    )

    assert store.contains_entity("霜星剧情")
    assert not store.contains_entity("孤星剧情")


def test_auto_update_repairs_missing_local_asset(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    server = FakeReleaseServer("r1", "a" * 64, ["references"])
    install_fake_remote(monkeypatch, {"server": server})
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    shard = data_dir / "releases" / "r1" / "references" / "shards" / "00000.jsonl.gz"
    assert shard.is_file()
    shard.unlink()
    server.current_requests = 0
    server.download_requests = 0

    result = corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert result["updated"] is True
    assert server.current_requests == 1
    assert server.download_requests >= 1
    assert shard.is_file()


def test_download_failure_midway_keeps_old_data_readable(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"], text="旧内容")}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"], text="新内容")
    delegate = corpus._download_verified

    def failing_download(urls, destination, *, size, sha256, timeout):
        if "/r2/" in str(destination):
            raise corpus.CorpusError("模拟下载中断")
        return delegate(urls, destination, size=size, sha256=sha256, timeout=timeout)

    monkeypatch.setattr(corpus, "_download_verified", failing_download)

    with pytest.raises(corpus.CorpusError):
        corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert read_pointer(data_dir)["release_id"] == "r1"
    store = corpus.CorpusStore(data_dir)
    assert store.search("旧内容", pack_ids=["references"])["count"] == 1


def test_auto_update_refuses_same_release_new_data_version(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"])}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)
    before = read_pointer(data_dir)

    state["server"] = FakeReleaseServer("r1", "b" * 64, ["references"])
    with pytest.raises(corpus.CorpusError):
        corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)

    assert read_pointer(data_dir) == before
    assert state["server"].download_requests == 0


@pytest.mark.parametrize("release_id", ["../evil", "/tmp/evil-abs", "a/b", "..", ""])
def test_rejects_malicious_release_id(tmp_path, monkeypatch, release_id):
    data_dir = tmp_path / "data"
    install_fake_remote(monkeypatch, {"server": FakeReleaseServer(release_id, "a" * 64, ["references"])})

    with pytest.raises(corpus.CorpusError):
        corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)
    with pytest.raises(corpus.CorpusError):
        corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    assert not (data_dir / "evil").exists()
    assert not Path("/tmp/evil-abs").exists()


def test_concurrent_queries_keep_version_and_serialize(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references"], text="旧内容")}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references"], timeout=10)

    store = corpus.CorpusStore(data_dir)
    first_entered = threading.Event()
    release_first = threading.Event()
    original_load_shard = store._load_shard
    load_calls: list[str] = []

    def blocking_load_shard(location):
        load_calls.append(location.pack_id)
        if len(load_calls) == 1:
            first_entered.set()
            release_first.wait(5)
        return original_load_shard(location)

    monkeypatch.setattr(store, "_load_shard", blocking_load_shard)
    results: dict[str, dict] = {}

    old_thread = threading.Thread(
        target=lambda: results.__setitem__("old", store.search("旧内容", pack_ids=["references"]))
    )
    old_thread.start()
    assert first_entered.wait(5)

    state["server"] = FakeReleaseServer("r2", "b" * 64, ["references"], text="新内容")
    assert corpus.auto_update_if_needed(data_dir, ["references"], timeout=10)["updated"] is True

    fresh_thread = threading.Thread(
        target=lambda: results.__setitem__("fresh", store.search("新内容", pack_ids=["references"]))
    )
    fresh_thread.start()
    time.sleep(0.2)
    assert fresh_thread.is_alive()
    assert load_calls == ["references"]

    release_first.set()
    old_thread.join(5)
    fresh_thread.join(5)

    assert results["old"]["count"] == 1
    assert results["old"]["data_version"] == "a" * 64
    assert results["fresh"]["count"] == 1
    assert results["fresh"]["data_version"] == "b" * 64


def test_entity_names_not_blocked_by_search_lock(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    state = {"server": FakeReleaseServer("r1", "a" * 64, ["references", "entities"])}
    install_fake_remote(monkeypatch, state)
    corpus.sync_packs_locked(data_dir, ["references", "entities"], timeout=10)

    store = corpus.CorpusStore(data_dir)
    entered = threading.Event()
    release = threading.Event()
    original_load_shard = store._load_shard

    def blocking_load_shard(location):
        entered.set()
        release.wait(5)
        return original_load_shard(location)

    monkeypatch.setattr(store, "_load_shard", blocking_load_shard)
    searcher = threading.Thread(target=lambda: store.search("r1-references", pack_ids=["references"]))
    searcher.start()
    assert entered.wait(5)

    done = threading.Event()

    def run_entity_names():
        store.entity_names()
        done.set()

    namer = threading.Thread(target=run_entity_names)
    namer.start()
    assert done.wait(2)

    release.set()
    searcher.join(5)
    namer.join(5)
    assert not namer.is_alive()


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, SimpleNamespace] = {}

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    def remove_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)

    def add_job(
        self,
        func,
        *,
        trigger,
        replace_existing=False,
        coalesce=False,
        max_instances=1,
        misfire_grace_time=None,
        next_run_time=None,
        **trigger_args,
    ):
        job_id = trigger_args.pop("id")
        self.jobs[job_id] = SimpleNamespace(
            func=func,
            trigger=trigger,
            id=job_id,
            coalesce=coalesce,
            max_instances=max_instances,
            next_run_time=next_run_time,
            **trigger_args,
        )


def install_fake_scheduler(monkeypatch: pytest.MonkeyPatch) -> FakeScheduler:
    scheduler = FakeScheduler()
    monkeypatch.setitem(sys.modules, "nonebot_plugin_apscheduler", SimpleNamespace(scheduler=scheduler))
    monkeypatch.setattr(startup, "_is_sharded_worker", lambda: False)
    return scheduler


def fake_config(**overrides) -> SimpleNamespace:
    base = {
        "enabled": True,
        "auto_update": True,
        "update_interval_hours": 24,
        "packs": "references",
        "include_endfield": False,
        "download_timeout_sec": 30,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_config_defaults_and_bounds():
    cfg = config_module.Config()
    assert cfg.auto_update is True
    assert cfg.update_interval_hours == 24
    assert config_module.Config(auto_update=False).auto_update is False
    assert config_module.Config(update_interval_hours=1).update_interval_hours == 1
    assert config_module.Config(update_interval_hours=720).update_interval_hours == 720
    with pytest.raises(ValidationError):
        config_module.Config(update_interval_hours=0)
    with pytest.raises(ValidationError):
        config_module.Config(update_interval_hours=721)


def test_reschedule_registers_interval_job(monkeypatch):
    scheduler = install_fake_scheduler(monkeypatch)
    monkeypatch.setattr(startup, "get_config", lambda: fake_config(update_interval_hours=12))

    startup.reschedule_auto_update_job()

    job = scheduler.get_job(startup.JOB_ID)
    assert job is not None
    assert job.trigger == "interval"
    assert job.hours == 12
    assert job.coalesce is True
    assert job.max_instances == 1
    assert job.next_run_time is None


def test_startup_schedules_soon_first_check(monkeypatch):
    scheduler = install_fake_scheduler(monkeypatch)
    monkeypatch.setattr(startup, "get_config", lambda: fake_config())

    startup.setup_prts_runtime()

    job = scheduler.get_job(startup.JOB_ID)
    assert job is not None
    assert job.next_run_time is not None


def test_reschedule_removes_job_when_disabled(monkeypatch):
    scheduler = install_fake_scheduler(monkeypatch)
    monkeypatch.setattr(startup, "get_config", lambda: fake_config())

    startup.reschedule_auto_update_job()
    assert scheduler.get_job(startup.JOB_ID) is not None

    monkeypatch.setattr(startup, "get_config", lambda: fake_config(auto_update=False))
    startup.reschedule_auto_update_job()
    assert scheduler.get_job(startup.JOB_ID) is None

    monkeypatch.setattr(startup, "get_config", lambda: fake_config(enabled=False))
    startup.reschedule_auto_update_job()
    assert scheduler.get_job(startup.JOB_ID) is None


def test_reschedule_skips_for_sharded_worker(monkeypatch):
    scheduler = install_fake_scheduler(monkeypatch)
    monkeypatch.setattr(startup, "_is_sharded_worker", lambda: True)
    monkeypatch.setattr(startup, "get_config", lambda: fake_config())

    startup.reschedule_auto_update_job()

    assert scheduler.get_job(startup.JOB_ID) is None


def test_config_reload_hook_reschedules(monkeypatch):
    calls = []
    monkeypatch.setattr(startup, "reschedule_auto_update_job", lambda: calls.append(True))

    config_module.on_prts_config_reload(fake_config())

    assert calls == [True]


def test_auto_update_job_skips_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(corpus, "auto_update_if_needed", lambda *args, **kwargs: calls.append(True) or {})
    monkeypatch.setattr(startup, "get_config", lambda: fake_config(auto_update=False))

    asyncio.run(startup._auto_update_job())

    assert calls == []


def test_auto_update_job_runs_when_enabled(tmp_path, monkeypatch):
    calls = []

    def fake_auto(data_dir, packs, *, timeout):
        calls.append((data_dir, list(packs), timeout))
        return {"updated": False, "release_id": "r1", "data_version": "a" * 64, "packs": ["references"]}

    monkeypatch.setattr(corpus, "auto_update_if_needed", fake_auto)
    monkeypatch.setattr(startup, "get_config", lambda: fake_config())
    monkeypatch.setattr(startup, "plugin_data_dir", lambda name: tmp_path)

    asyncio.run(startup._auto_update_job())

    assert calls
    assert calls[0][1] == ["references"]
    assert calls[0][2] == 30


def test_auto_update_job_survives_failure(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise corpus.CorpusError("模拟失败")

    monkeypatch.setattr(corpus, "auto_update_if_needed", boom)
    monkeypatch.setattr(startup, "get_config", lambda: fake_config())
    monkeypatch.setattr(startup, "plugin_data_dir", lambda name: tmp_path)

    asyncio.run(startup._auto_update_job())
