# -*- coding: utf-8 -*-
"""读取某个环境 site-packages 中的包清单，并依据 RECORD 清单计算每个包的占用大小。

直接解析 *.dist-info / *.egg-info 元数据，不启动目标环境的解释器，
因此对损坏的环境同样有效，也不会触发杀毒软件的频繁拦截。

RECORD 中大小字段缺失的文件（__pycache__ 缓存等）通过 site-packages
的一次性扫描索引补齐，避免上万次逐文件 stat。
"""
from __future__ import annotations

import os

from sizes import format_size

_LONG_PREFIX = "\\\\?\\"


def count_packages(site_packages: str) -> int:
    """快速统计包数量（只数元数据目录，不解析内容）"""
    if not site_packages or not os.path.isdir(site_packages):
        return -1
    try:
        names = os.listdir(site_packages)
    except OSError:
        return -1
    return sum(1 for n in names
               if n.endswith(".dist-info") or n.endswith(".egg-info"))


def _parse_metadata(meta_path: str):
    name = version = ""
    try:
        with open(meta_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Name:") and not name:
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:") and not version:
                    version = line.split(":", 1)[1].strip()
                if name and version:
                    break
    except OSError:
        pass
    return name, version


def _record_size(record_path: str, site_packages: str, index: dict) -> int:
    """累加 RECORD 清单中每个文件的大小；缺大小记录的查索引或实际 stat"""
    total = 0
    try:
        f = open(record_path, encoding="utf-8", errors="replace")
    except OSError:
        return 0
    with f:
        for line in f:
            parts = line.rstrip("\r\n").rsplit(",", 2)
            if len(parts) < 3:
                continue
            rel, _, size = parts[0], parts[1], parts[2]
            if size.isdigit():
                total += int(size)
                continue
            full = os.path.normpath(os.path.join(site_packages, rel))
            hit = index.get(os.path.normcase(full))
            if hit is None and rel.startswith(".."):
                # 包外文件（如 Scripts\*.exe）：单独 stat
                try:
                    if os.path.isfile(full):
                        hit = os.path.getsize(full)
                except OSError:
                    hit = None
            if hit:
                total += hit
    return total


def list_packages(site_packages: str, file_index=None) -> list[dict]:
    """返回 [{'name','version','size','size_text'}]，按大小降序。

    file_index: 可传入 sizes.dir_size_index() 的索引，避免重复扫描。
    """
    if not site_packages or not os.path.isdir(site_packages):
        return []
    try:
        entries = sorted(os.listdir(site_packages))
    except OSError:
        return []
    if file_index is None:
        from sizes import dir_size_index
        _, file_index = dir_size_index(site_packages)

    result = []
    for entry in entries:
        is_dist = entry.endswith(".dist-info")
        is_egg = entry.endswith(".egg-info")
        if not (is_dist or is_egg):
            continue
        full = os.path.join(site_packages, entry)
        if not os.path.isdir(full):
            continue

        # 目录名兜底：numpy-1.26.4.dist-info
        stem = entry[:-len(".dist-info")] if is_dist else entry[:-len(".egg-info")]
        default_name, _, default_ver = stem.partition("-")

        if is_dist:
            name, version = _parse_metadata(os.path.join(full, "METADATA"))
            record = os.path.join(full, "RECORD")
            if os.path.isfile(record):
                size = _record_size(record, site_packages, file_index)
            else:
                size = _dir_size_simple(full)
        else:  # egg-info
            meta = os.path.join(full, "PKG-INFO")
            if os.path.isfile(meta):
                name, version = _parse_metadata(meta)
            else:
                name, version = "", ""
            size = _dir_size_simple(full)

        result.append({
            "name": name or default_name,
            "version": version or default_ver,
            "size": size,
            "size_text": format_size(size),
        })

    result.sort(key=lambda p: (p["size"] < 0, -p["size"] if p["size"] >= 0 else 0))
    return result


def _dir_size_simple(path: str) -> int:
    """小目录（dist-info/egg-info 本身）的大小，无需长路径前缀"""
    total = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total
