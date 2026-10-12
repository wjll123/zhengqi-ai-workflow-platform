"""Move selected workspace files to a reversible trash batch."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path


class FileTrashError(ValueError):
    pass


def _selected(items: object, roots: dict[str, Path]) -> list[tuple[str, str, Path]]:
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise FileTrashError("请选择 1–100 个文件")
    chosen: list[tuple[str, str, Path]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            raise FileTrashError("文件列表格式不正确")
        where, name = item.get("where"), item.get("name")
        if where not in roots or not isinstance(name, str) or not name or name != Path(name).name:
            raise FileTrashError("目录或文件名不正确")
        if name.startswith((".", "~$")) or "\x00" in name or "\\" in name:
            raise FileTrashError("不能删除隐藏文件或非法文件名")
        key = (where, name)
        if key in seen:
            raise FileTrashError(f"重复选择：{name}")
        seen.add(key)
        file = roots[where] / name
        if file.is_symlink() or not file.is_file():
            raise FileTrashError(f"文件不存在或不可删除：{name}")
        chosen.append((where, name, file))
    return chosen


def move_many(items: object, roots: dict[str, Path], trash_dir: Path) -> dict:
    """Validate the whole selection before moving anything; rollback on failure."""
    chosen = _selected(items, roots)
    token = uuid.uuid4().hex
    batch_dir = trash_dir / token
    moved: list[tuple[Path, Path]] = []
    try:
        for where, name, original in chosen:
            destination = batch_dir / where / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(original, destination)
            moved.append((original, destination))
        manifest = [{"where": where, "name": name} for where, name, _ in chosen]
        (batch_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        for original, destination in reversed(moved):
            os.replace(destination, original)
        raise FileTrashError(f"批量删除失败，已恢复文件：{exc}") from exc
    return {"token": token, "items": manifest}


def restore_many(token: object, roots: dict[str, Path], trash_dir: Path) -> list[dict]:
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise FileTrashError("撤销编号不正确")
    batch_dir = trash_dir / token
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileTrashError("这批文件已恢复或不在回收区")
    items = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = []
    if not isinstance(items, list) or not items:
        raise FileTrashError("回收区记录不完整")
    for item in items:
        where, name = item.get("where"), item.get("name")
        if where not in roots or not isinstance(name, str) or name != Path(name).name:
            raise FileTrashError("回收区记录不正确")
        source, destination = batch_dir / where / name, roots[where] / name
        if not source.is_file():
            raise FileTrashError(f"回收区缺少文件：{name}")
        if destination.exists():
            raise FileTrashError(f"原目录已有同名文件，无法撤销：{name}")
        selected.append((source, destination))
    restored: list[tuple[Path, Path]] = []
    try:
        for source, destination in selected:
            os.replace(source, destination)
            restored.append((source, destination))
    except OSError as exc:
        for source, destination in reversed(restored):
            os.replace(destination, source)
        raise FileTrashError(f"撤销失败，文件仍在回收区：{exc}") from exc
    manifest_path.unlink()
    for where in roots:
        folder = batch_dir / where
        if folder.is_dir():
            folder.rmdir()
    batch_dir.rmdir()
    return items
