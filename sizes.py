# -*- coding: utf-8 -*-
"""目录大小统计与容量格式化（支持 Windows 长路径）"""
from __future__ import annotations

import os

_LONG_PREFIX = "\\\\?\\"


def _long(path: str) -> str:
    path = os.path.abspath(path)
    if not path.startswith(_LONG_PREFIX):
        path = _LONG_PREFIX + path
    return path


def dir_size(path: str) -> int:
    """递归统计目录总字节数。跳过符号链接/联接点，忽略无权限的目录。"""
    return dir_size_index(path)[0]


def dir_size_index(path: str):
    """同 dir_size，同时返回 {normcase(绝对路径): 字节数} 索引，供包大小解析复用。"""
    total = 0
    index = {}
    stack = [_long(path)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            n = e.stat(follow_symlinks=False).st_size
                            total += n
                            # 去掉长路径前缀，键与 os.path.join 的普通路径一致
                            key = e.path
                            if key.startswith(_LONG_PREFIX):
                                key = key[len(_LONG_PREFIX):]
                            index[os.path.normcase(key)] = n
                    except OSError:
                        pass
        except OSError:
            pass
    return total, index


def format_size(n) -> str:
    if n is None or n < 0:
        return "…"
    units = ["B", "KB", "MB", "GB", "TB"]
    v = float(n)
    for u in units:
        if v < 1024 or u == units[-1]:
            return f"{v:.0f} {u}" if u == "B" else f"{v:.1f} {u}"
        v /= 1024
    return f"{v:.1f} TB"
