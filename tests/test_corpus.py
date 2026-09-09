from __future__ import annotations

import gzip
import json
from pathlib import Path

from corpus import CorpusStore, _document_uid


def write_gzip_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False))
            stream.write("\n")


def make_fixture(tmp_path: Path) -> Path:
    data_dir = tmp_path / "data"
    release_dir = data_dir / "releases" / "r1" / "references"
    rows = [
        {
            "document": {
                "document_id": "client:references:timeline",
                "display_title": "泰拉年表",
                "document_type": "reference",
                "document_kind": "reference",
                "document_category": "reference",
                "source_ref_prefix": "client_data:references:timeline",
                "line_count": 2,
            },
            "lines": [
                {"line_number": 1, "line_type": "knowledge", "speaker_raw": "", "text": "黑暗时代发生了许多事件"},
                {"line_number": 2, "line_type": "knowledge", "speaker_raw": "", "text": "孤星活动发生在后来"},
            ],
        },
    ]
    write_gzip_jsonl(release_dir / "shards/00000.jsonl.gz", rows)
    write_gzip_jsonl(
        release_dir / "catalog/documents.jsonl.gz",
        [
            {
                "document": rows[0]["document"],
                "shard_path": "shards/00000.jsonl.gz",
                "record_index": 0,
                "search_index_id": 1,
            },
        ],
    )
    (release_dir / "pack-manifest.json").write_text(
        json.dumps(
            {
                "pack_id": "references",
                "document_count": 1,
                "line_count": 2,
                "document_catalog": {"path": "catalog/documents.jsonl.gz"},
                "shards": [{"path": "shards/00000.jsonl.gz"}],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "releases/current.json").write_text(
        json.dumps({"release_id": "r1", "data_version": "a" * 64, "packs": ["references"]}),
        encoding="utf-8",
    )
    return data_dir


def test_search_and_read_without_search_index(tmp_path: Path) -> None:
    store = CorpusStore(make_fixture(tmp_path))

    search = store.search("黑暗时代", pack_ids=["references"])

    assert search["count"] == 1
    item = search["items"][0]
    assert item["title"] == "泰拉年表"
    assert item["matches"][0]["line"] == 1

    read = store.read(document_uid=item["document_uid"], start_line=1, end_line=1)

    assert read["lines"][0]["text"] == "黑暗时代发生了许多事件"
    assert read["lines"][0]["source_ref"].endswith(":L1")


def test_document_uid_is_stable() -> None:
    assert _document_uid("client:references:timeline") == _document_uid("client:references:timeline")
