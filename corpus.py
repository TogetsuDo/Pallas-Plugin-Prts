from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CURRENT_URL = "https://prts.chat/api/agent/data/releases/current"
SITE_BASE_URL = "https://prts.chat/api/agent/data/releases"
MODEL_SCOPE_PACK_REPOS = {
    "official_game": "HTiantian/prts-agent-corpus-arknights",
    "reviewed_wiki": "HTiantian/prts-agent-corpus-arknights",
    "terra_journey": "HTiantian/prts-agent-corpus-arknights",
    "entities": "HTiantian/prts-agent-corpus-arknights",
    "references": "HTiantian/prts-agent-corpus-arknights",
    "endfield_official_game": "HTiantian/prts-agent-corpus-endfield",
    "endfield_reviewed_knowledge": "HTiantian/prts-agent-corpus-endfield",
}
ALLOWED_PACKS = frozenset(MODEL_SCOPE_PACK_REPOS)
MAX_MATCH_CHARS = 1600


class CorpusError(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentLocation:
    pack_id: str
    document_id: str
    shard_path: str
    record_index: int
    summary: dict[str, Any]
    search_index_id: int | None


def corpus_root(data_dir: Path) -> Path:
    return data_dir / "releases"


def parse_pack_ids(raw: str, *, include_endfield: bool = False) -> list[str]:
    packs = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    if include_endfield:
        packs.extend(("endfield_official_game", "endfield_reviewed_knowledge"))
    return list(dict.fromkeys(item for item in packs if item in ALLOWED_PACKS))


def _request_json(url: str, *, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "pallas-prts/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise CorpusError(f"读取 PRTS 元数据失败：{exc}") from exc
    if not isinstance(data, dict):
        raise CorpusError("PRTS 元数据不是 JSON 对象")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_verified(urls: Iterable[str], destination: Path, *, size: int, sha256: str, timeout: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == size and _sha256_file(destination) == sha256:
        return
    last_error: Exception | None = None
    for url in urls:
        temporary = None
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "pallas-prts/0.1"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
                with os.fdopen(fd, "wb") as stream:
                    while block := response.read(1024 * 1024):
                        stream.write(block)
            temporary_path = Path(temporary)
            if temporary_path.stat().st_size != size or _sha256_file(temporary_path) != sha256:
                raise CorpusError(f"文件校验失败：{url}")
            os.replace(temporary_path, destination)
            return
        except (CorpusError, OSError, urllib.error.URLError) as exc:
            last_error = exc
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    raise CorpusError(f"下载语料失败：{last_error}")


def _asset_rows(pack: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    catalog = pack.get("document_catalog")
    if isinstance(catalog, dict):
        rows.append(catalog)
    rows.extend(item for item in pack.get("shards", []) if isinstance(item, dict))
    search = pack.get("search_index")
    if isinstance(search, dict):
        rows.extend(item for item in search.get("shards", []) if isinstance(item, dict))
    return rows


def _safe_asset_path(base: Path, raw_path: str) -> Path:
    path = str(raw_path or "")
    if not path or "\\" in path:
        raise CorpusError("PRTS 资源路径无效")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise CorpusError("PRTS 资源路径越界")
    return base.joinpath(*relative.parts)


def _mirror_urls(current: dict[str, Any], pack_id: str, release_id: str, path: str) -> list[str]:
    urls: list[str] = []
    for mirror in current.get("mirrors", []):
        if not isinstance(mirror, dict) or pack_id not in (mirror.get("pack_ids") or []):
            continue
        base = str(mirror.get("base_url") or "").rstrip("/")
        if base:
            urls.append(f"{base}/{pack_id}/{path}")
    repo = MODEL_SCOPE_PACK_REPOS.get(pack_id)
    if repo:
        urls.append(
            f"https://modelscope.cn/datasets/{repo}/resolve/master/releases/{release_id}/{pack_id}/{path}"
        )
    urls.append(f"{SITE_BASE_URL}/{release_id}/{pack_id}/{path}")
    return list(dict.fromkeys(urls))


def sync_packs(data_dir: Path, pack_ids: list[str], *, timeout: int = 120) -> dict[str, Any]:
    requested = [pack_id for pack_id in pack_ids if pack_id in ALLOWED_PACKS]
    if not requested:
        raise CorpusError("没有选择有效的 PRTS pack")
    current_payload = _request_json(CURRENT_URL, timeout=timeout)
    data = current_payload.get("data")
    if current_payload.get("code") != 200 or not isinstance(data, dict):
        raise CorpusError("PRTS current 响应无效")
    release_id = str(data.get("release_id") or "")
    data_version = str(data.get("data_version") or "")
    if not release_id or len(data_version) != 64:
        raise CorpusError("PRTS current 缺少 release_id 或 data_version")
    release_manifest = _request_json(
        f"{SITE_BASE_URL}/{release_id}/release-manifest.json",
        timeout=timeout,
    )
    if release_manifest.get("release_id") != release_id or release_manifest.get("data_version") != data_version:
        raise CorpusError("PRTS release-manifest 与 current 不一致")
    licenses = {
        str(item.get("pack_id")): item
        for item in release_manifest.get("licenses", [])
        if isinstance(item, dict)
    }
    descriptor_by_id = {
        str(item.get("pack_id")): item
        for item in release_manifest.get("packs", [])
        if isinstance(item, dict)
    }
    release_dir = corpus_root(data_dir) / release_id
    for pack_id in requested:
        descriptor = descriptor_by_id.get(pack_id)
        if descriptor is None:
            raise CorpusError(f"当前 release 不包含 pack：{pack_id}")
        license_row = licenses.get(pack_id)
        if license_row is not None and not license_row.get("public_distribution_approved"):
            raise CorpusError(f"当前 release 未批准公开下载 pack：{pack_id}")
        manifest_path = str(descriptor.get("manifest_path") or f"{pack_id}/pack-manifest.json")
        pack_manifest = _request_json(f"{SITE_BASE_URL}/{release_id}/{manifest_path}", timeout=timeout)
        if pack_manifest.get("pack_id") not in (None, pack_id):
            raise CorpusError(f"pack-manifest 标识错误：{pack_id}")
        pack_dir = release_dir / pack_id
        _write_json(pack_dir / "pack-manifest.json", pack_manifest)
        for asset in _asset_rows(pack_manifest):
            path = str(asset.get("path") or "")
            size = int(asset.get("compressed_size") or 0)
            sha256 = str(asset.get("sha256") or "")
            if not path or size <= 0 or len(sha256) != 64:
                raise CorpusError(f"pack-manifest 资源描述错误：{pack_id}")
            _download_verified(
                _mirror_urls(current_payload.get("data") or {}, pack_id, release_id, path),
                _safe_asset_path(pack_dir, path),
                size=size,
                sha256=sha256,
                timeout=timeout,
            )
    pointer = {
        "release_id": release_id,
        "data_version": data_version,
        "packs": requested,
        "document_count": release_manifest.get("document_count"),
        "synced_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }
    _write_json(corpus_root(data_dir) / "current.json", pointer)
    return pointer


def _normalize(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).lower()


def _ngrams(value: str) -> list[str]:
    chars = list(_normalize(value))
    if not chars:
        return []
    size = min(3, len(chars))
    return list(dict.fromkeys("".join(chars[index : index + size]) for index in range(len(chars) - size + 1)))


def _document_uid(document_id: str) -> str:
    digest = hashlib.sha256(f"prts-document\0{document_id}".encode()).digest()[:12]
    return f"doc_{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"


def _game(summary: dict[str, Any]) -> str:
    explicit = _normalize(summary.get("game"))
    if explicit in {"arknights", "endfield"}:
        return explicit
    identity = f"{summary.get('document_id', '')} {summary.get('source_ref_prefix', '')}".lower()
    return "endfield" if "endfield:" in identity else "arknights"


def _resource_type(summary: dict[str, Any]) -> str:
    explicit = str(summary.get("resource_type") or "").strip()
    if explicit:
        return explicit
    kind = str(summary.get("document_kind") or "")
    document_type = str(summary.get("document_type") or "")
    category = str(summary.get("document_category") or "")
    if document_type == "story":
        return "operator_record" if category == "memory" else "story"
    if document_type == "character":
        return "character_module" if category == "模组文案" else "character_profile"
    if document_type == "entity":
        return "entity_profile"
    if document_type == "reference":
        return "timeline" if kind == "reference" else "reference"
    return "knowledge"


def _line_text(line: dict[str, Any]) -> str:
    speaker = str(line.get("speaker_raw") or "").strip()
    text = str(line.get("text") or "")
    return f"{speaker}: {text}" if speaker else text


class CorpusStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.root = corpus_root(data_dir)
        self._loaded_key = ""
        self._locations: dict[tuple[str, str], DocumentLocation] = {}
        self._by_id: dict[str, DocumentLocation] = {}
        self._by_uid: dict[str, DocumentLocation] = {}
        self._by_title: dict[str, list[DocumentLocation]] = {}
        self._shard_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._index_cache: dict[tuple[str, str], dict[str, list[int]]] = {}
        self._manifests: dict[str, dict[str, Any]] = {}
        self._entity_names_key = ""
        self._entity_names_cache: tuple[str, ...] = ()

    def status(self) -> dict[str, Any]:
        pointer = self._read_pointer()
        if not pointer:
            return {"installed": False, "packs": [], "release_id": "", "data_version": ""}
        installed = []
        for pack_id in pointer.get("packs", []):
            manifest = self._read_manifest(str(pack_id), pointer.get("release_id"))
            if manifest:
                installed.append({
                    "pack_id": pack_id,
                    "documents": int(manifest.get("document_count") or 0),
                    "lines": int(manifest.get("line_count") or 0),
                })
        return {
            "installed": bool(installed),
            "release_id": str(pointer.get("release_id") or ""),
            "data_version": str(pointer.get("data_version") or ""),
            "packs": installed,
        }

    def _read_pointer(self) -> dict[str, Any] | None:
        path = self.root / "current.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpusError(f"PRTS current 指针损坏：{exc}") from exc
        return data if isinstance(data, dict) else None

    def _read_manifest(self, pack_id: str, release_id: Any) -> dict[str, Any] | None:
        if not release_id:
            return None
        path = self.root / str(release_id) / pack_id / "pack-manifest.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpusError(f"PRTS pack 清单损坏：{pack_id}: {exc}") from exc
        return data if isinstance(data, dict) else None

    def entity_names(self, *, pack_ids: Iterable[str] = ("entities",)) -> tuple[str, ...]:
        pointer = self._read_pointer()
        if not pointer or not pointer.get("release_id"):
            return ()
        release_id = str(pointer["release_id"])
        selected = tuple(pack_id for pack_id in pack_ids if pack_id in (pointer.get("packs") or []))
        key = f"{release_id}:{','.join(selected)}"
        if key == self._entity_names_key:
            return self._entity_names_cache
        names: set[str] = set()
        for pack_id in selected:
            manifest = self._read_manifest(pack_id, release_id)
            catalog = manifest.get("document_catalog") if isinstance(manifest, dict) else None
            if not isinstance(catalog, dict):
                continue
            catalog_path = _safe_asset_path(
                self.root / release_id / pack_id,
                str(catalog.get("path") or ""),
            )
            if not catalog_path.is_file():
                continue
            try:
                with gzip.open(catalog_path, "rt", encoding="utf-8") as stream:
                    for raw in stream:
                        row = json.loads(raw)
                        summary = row.get("document") if isinstance(row, dict) else None
                        if not isinstance(summary, dict):
                            continue
                        for field_name in (
                            "display_title",
                            "character_name",
                            "activity_name",
                            "story_name",
                            "collection_name",
                        ):
                            name = _normalize(summary.get(field_name))
                            if len(name) >= 2:
                                names.add(name)
            except (OSError, json.JSONDecodeError) as exc:
                raise CorpusError(f"读取 PRTS 实体目录失败：{catalog_path}: {exc}") from exc
        self._entity_names_key = key
        self._entity_names_cache = tuple(sorted(names, key=lambda item: (-len(item), item)))
        return self._entity_names_cache

    def contains_entity(self, text: str, *, pack_ids: Iterable[str] = ("entities",)) -> bool:
        query = _normalize(text)
        if not query:
            return False
        for name in self.entity_names(pack_ids=pack_ids):
            if name.isascii() and re.fullmatch(r"[a-z0-9][a-z0-9 ._-]*", name):
                if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", query):
                    return True
            elif name in query:
                return True
        return False

    def _load(self) -> None:
        pointer = self._read_pointer()
        if not pointer or not pointer.get("release_id"):
            raise CorpusError("PRTS 语料尚未安装，请先发送 PRTS更新")
        key = f"{pointer.get('release_id')}:{','.join(pointer.get('packs') or [])}"
        if key == self._loaded_key:
            return
        self._locations.clear()
        self._by_id.clear()
        self._by_uid.clear()
        self._by_title.clear()
        self._shard_cache.clear()
        self._index_cache.clear()
        self._manifests.clear()
        release_id = str(pointer["release_id"])
        for pack_id in pointer.get("packs", []):
            manifest = self._read_manifest(str(pack_id), release_id)
            if not manifest:
                continue
            self._manifests[str(pack_id)] = manifest
            catalog = manifest.get("document_catalog")
            if not isinstance(catalog, dict):
                continue
            catalog_path = _safe_asset_path(
                self.root / release_id / str(pack_id),
                str(catalog.get("path") or ""),
            )
            if not catalog_path.is_file():
                continue
            with gzip.open(catalog_path, "rt", encoding="utf-8") as stream:
                for raw in stream:
                    if not raw.strip():
                        continue
                    row = json.loads(raw)
                    summary = row.get("document") if isinstance(row, dict) else None
                    if not isinstance(summary, dict):
                        continue
                    document_id = str(summary.get("document_id") or "")
                    shard_path = str(row.get("shard_path") or "")
                    record_index = row.get("record_index")
                    if not document_id or not shard_path or not isinstance(record_index, int):
                        continue
                    location = DocumentLocation(
                        pack_id=str(pack_id),
                        document_id=document_id,
                        shard_path=shard_path,
                        record_index=record_index,
                        summary=summary,
                        search_index_id=int(row["search_index_id"]) if row.get("search_index_id") is not None else None,
                    )
                    self._locations[(str(pack_id), document_id)] = location
                    self._by_id[document_id] = location
                    self._by_uid[_document_uid(document_id)] = location
                    title = _normalize(summary.get("display_title"))
                    if title:
                        self._by_title.setdefault(title, []).append(location)
        self._loaded_key = key

    def _release_dir(self, pack_id: str) -> Path:
        pointer = self._read_pointer() or {}
        return self.root / str(pointer.get("release_id") or "") / pack_id

    def _load_shard(self, location: DocumentLocation) -> list[dict[str, Any]]:
        key = (location.pack_id, location.shard_path)
        cached = self._shard_cache.get(key)
        if cached is not None:
            return cached
        path = _safe_asset_path(self._release_dir(location.pack_id), location.shard_path)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                records = [json.loads(raw) for raw in stream if raw.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpusError(f"读取 PRTS 分片失败：{path}: {exc}") from exc
        self._shard_cache[key] = records
        return records

    def _load_index(self, pack_id: str, path: str) -> dict[str, list[int]]:
        key = (pack_id, path)
        cached = self._index_cache.get(key)
        if cached is not None:
            return cached
        index_path = _safe_asset_path(self._release_dir(pack_id), path)
        try:
            with gzip.open(index_path, "rb") as stream:
                raw = stream.read()
        except OSError as exc:
            raise CorpusError(f"读取 PRTS 倒排索引失败：{index_path}: {exc}") from exc
        if raw[:8] not in (b"PRTSNG2\0", b"PRTSTG1\0"):
            raise CorpusError(f"PRTS 倒排索引格式未知：{index_path}")
        count = int.from_bytes(raw[8:12], "little")
        table_start = 12
        payload_start = table_start + (count + 1) * 4
        if payload_start > len(raw):
            raise CorpusError(f"PRTS 倒排索引偏移表损坏：{index_path}")
        offsets = [int.from_bytes(raw[table_start + index * 4 : table_start + index * 4 + 4], "little") for index in range(count + 1)]
        result: dict[str, list[int]] = {}
        for index in range(count):
            cursor = payload_start + offsets[index]
            end = payload_start + offsets[index + 1]
            length, cursor = _read_varint(raw, cursor, end)
            gram = raw[cursor : cursor + length].decode("utf-8")
            cursor += length
            posting_count, cursor = _read_varint(raw, cursor, end)
            postings: list[int] = []
            current = 0
            for _ in range(posting_count):
                delta, cursor = _read_varint(raw, cursor, end)
                current += delta
                postings.append(current)
            result[gram] = postings
        self._index_cache[key] = result
        return result

    def _candidate_locations(self, query: str, pack_ids: list[str] | None = None) -> list[DocumentLocation] | None:
        grams = _ngrams(query)
        if not grams:
            return None
        candidates: list[DocumentLocation] = []
        selected_packs = set(pack_ids or self._manifests)
        has_index = False
        for pack_id, manifest in self._manifests.items():
            if pack_id not in selected_packs:
                continue
            search = manifest.get("search_index")
            if not isinstance(search, dict) or not search.get("shards"):
                continue
            has_index = True
            per_gram: dict[str, set[int]] = {gram: set() for gram in grams}
            for descriptor in search.get("shards", []):
                if not isinstance(descriptor, dict):
                    continue
                first = str(descriptor.get("first_ngram") or descriptor.get("first_trigram") or "").encode("utf-8")
                last = str(descriptor.get("last_ngram") or descriptor.get("last_trigram") or "").encode("utf-8")
                relevant = [gram for gram in grams if first <= gram.encode("utf-8") <= last]
                if not relevant:
                    continue
                index = self._load_index(pack_id, str(descriptor.get("path") or ""))
                for gram in relevant:
                    per_gram[gram].update(index.get(gram, []))
            intersection: set[int] | None = None
            for gram in grams:
                values = per_gram[gram]
                intersection = values if intersection is None else intersection.intersection(values)
            for location in self._locations.values():
                if location.pack_id == pack_id and location.search_index_id in (intersection or set()):
                    candidates.append(location)
        return candidates if has_index else None

    def _matches_filters(self, summary: dict[str, Any], record: dict[str, Any], filters: dict[str, Any]) -> bool:
        if filters.get("games") and _game(summary) not in filters["games"]:
            return False
        if filters.get("resource_types") and _resource_type(summary) not in filters["resource_types"]:
            return False
        metadata = " ".join(str(summary.get(key) or "") for key in (
            "display_title", "character_name", "activity_name", "story_name", "collection_name",
        ))
        metadata_text = _normalize(metadata)
        for key in ("character_names", "activity_names", "entity_names"):
            if filters.get(key) and not any(_normalize(item) in metadata_text for item in filters[key]):
                return False
        if filters.get("speakers"):
            speakers = {str(item.get("speaker_raw") or "") for item in record.get("lines", []) if isinstance(item, dict)}
            if not speakers.intersection(filters["speakers"]):
                return False
        return True

    def search(self, query: str, *, pack_ids: list[str] | None = None, limit: int = 5, **filters: Any) -> dict[str, Any]:
        self._load()
        normalized_query = _normalize(query)
        locations = self._candidate_locations(normalized_query, pack_ids) if normalized_query else None
        if locations is None:
            locations = list(self._locations.values())
        selected_packs = set(pack_ids or self._manifests)
        results: list[dict[str, Any]] = []
        for location in locations:
            if location.pack_id not in selected_packs:
                continue
            records = self._load_shard(location)
            if location.record_index >= len(records):
                continue
            record = records[location.record_index]
            if not isinstance(record, dict) or not self._matches_filters(location.summary, record, filters):
                continue
            lines = record.get("lines") if isinstance(record.get("lines"), list) else []
            matches = []
            for line in lines:
                if not isinstance(line, dict):
                    continue
                if normalized_query and normalized_query not in _normalize(_line_text(line)):
                    continue
                matches.append({
                    "line": int(line.get("line_number") or 0),
                    "speaker": str(line.get("speaker_raw") or ""),
                    "text": _bounded_text(line.get("text")),
                })
                if len(matches) >= 3:
                    break
            title_hit = normalized_query and normalized_query in _normalize(location.summary.get("display_title"))
            if normalized_query and not matches and not title_hit:
                continue
            line_start = matches[0]["line"] if matches else None
            line_end = matches[-1]["line"] if matches else None
            title = str(location.summary.get("display_title") or location.document_id)
            result = {
                "game": _game(location.summary),
                "title": title,
                "resource_type": _resource_type(location.summary),
                "authority": str(self._manifests.get(location.pack_id, {}).get("authority") or ""),
                "document_uid": _document_uid(location.document_id),
                "source_ref_prefix": str(location.summary.get("source_ref_prefix") or ""),
                "matches": matches,
                "citation": f"《{title}》" + (f"第 {line_start}-{line_end} 行" if line_start else ""),
            }
            results.append(result)
        results.sort(key=lambda item: (0 if item["matches"] else 1, item["title"]))
        return {
            "query": query,
            "data_version": str((self._read_pointer() or {}).get("data_version") or ""),
            "count": min(len(results), max(1, limit)),
            "items": results[: max(1, limit)],
        }

    def read(
        self,
        *,
        document_uid: str = "",
        document_id: str = "",
        display_title: str = "",
        source_ref: str = "",
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = 80,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        self._load()
        location = None
        center_line = None
        if document_uid:
            location = self._by_uid.get(document_uid.strip())
        elif document_id:
            location = self._by_id.get(document_id.strip())
        elif display_title:
            matches = self._by_title.get(_normalize(display_title), [])
            if len(matches) > 1:
                raise CorpusError("资料标题有歧义，请使用 document_uid")
            location = matches[0] if matches else None
        elif source_ref:
            prefix, separator, line = source_ref.rpartition(":L")
            location = next((item for item in self._by_id.values() if item.summary.get("source_ref_prefix") == prefix), None)
            if separator and line.isdigit():
                center_line = int(line)
        if location is None:
            raise CorpusError("找不到指定的 PRTS 文档")
        if center_line is not None:
            start_line = max(1, center_line - 3)
            end_line = center_line + 3
        records = self._load_shard(location)
        if location.record_index >= len(records):
            raise CorpusError("PRTS 文档记录不存在")
        record = records[location.record_index]
        lines = record.get("lines") if isinstance(record.get("lines"), list) else []
        start = max(1, int(start_line))
        end = min(len(lines), int(end_line or len(lines)), start + max(1, max_lines) - 1)
        selected: list[dict[str, Any]] = []
        char_count = 0
        truncated = False
        for line in lines:
            if not isinstance(line, dict) or not start <= int(line.get("line_number") or 0) <= end:
                continue
            text = str(line.get("text") or "")
            remaining = max(0, int(max_chars) - char_count)
            if not remaining:
                truncated = True
                break
            if len(text) > remaining:
                line = {**line, "text": f"{text[: max(0, remaining - 1)]}…"}
                truncated = True
            char_count += len(str(line.get("text") or ""))
            selected.append(line)
            if len(selected) >= max(1, max_lines) or truncated:
                break
        title = str(location.summary.get("display_title") or location.document_id)
        return {
            "data_version": str((self._read_pointer() or {}).get("data_version") or ""),
            "game": _game(location.summary),
            "title": title,
            "resource_type": _resource_type(location.summary),
            "authority": str(self._manifests.get(location.pack_id, {}).get("authority") or ""),
            "document_uid": _document_uid(location.document_id),
            "lines": [
                {
                    "line": int(line.get("line_number") or 0),
                    "speaker": str(line.get("speaker_raw") or ""),
                    "text": str(line.get("text") or ""),
                    "source_ref": f"{location.summary.get('source_ref_prefix')}:L{line.get('line_number')}",
                }
                for line in selected
            ],
            "has_more": truncated or end < len(lines),
            "citation": f"《{title}》第 {start}-{end} 行",
        }


def _read_varint(raw: bytes, cursor: int, end: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while cursor < end and shift <= 49:
        byte = raw[cursor]
        cursor += 1
        value += (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, cursor
        shift += 7
    raise CorpusError("PRTS 倒排索引 varint 损坏")


def _bounded_text(value: Any, limit: int = MAX_MATCH_CHARS) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
