# -*- coding: utf-8 -*-
"""检测 Python 环境关联的模型文件。

两类来源：
1. 已安装的 ML 库对应的官方模型缓存目录（HuggingFace / Torch Hub / Whisper /
   Keras / NLTK / Paddle / ModelScope / Kaggle 等），按缓存子项列出；
2. 环境内自带的体积 ≥ 50MB 的模型权重文件（.pt/.pth/.onnx/.safetensors 等），
   这类文件属于库的一部分，删除可能导致对应库损坏，界面会重点提示。
"""
from __future__ import annotations

import os

from remover import robust_rmtree
from sizes import dir_size

MODEL_EXTS = {".pt", ".pth", ".onnx", ".safetensors", ".ckpt", ".pdparams",
              ".pdmodel", ".pdiparams", ".h5", ".tflite", ".engine", ".plan",
              ".trt", ".params", ".mlmodel", ".model", ".nemo"}
BIG_FILE_MIN = 50 * 1024 * 1024      # 环境内大文件阈值
ENTRY_MIN = 1024 * 1024              # 缓存子项小于 1MB 不列出

# 库名（小写、_ 归一为 -）→ [(显示名, 路径片段)]；路径片段首元素为
# "~"（用户主目录）或环境变量名（如 APPDATA）
LIB_CACHES = [
    (("transformers", "diffusers", "sentence-transformers", "timm",
      "accelerate", "peft"),
     [("HuggingFace 模型缓存", ("~", ".cache", "huggingface", "hub"))]),
    (("torch", "torchvision", "torchaudio"),
     [("Torch Hub 缓存", ("~", ".cache", "torch", "hub", "checkpoints")),
      ("Torch Hub 下载的仓库", ("~", ".cache", "torch", "hub"))]),
    (("ultralytics",),
     [("Ultralytics 模型/设置", ("%APPDATA%", "Ultralytics"))]),
    (("whisper", "openai-whisper"),
     [("Whisper 模型缓存", ("~", ".cache", "whisper"))]),
    (("clip", "open-clip-torch", "open_clip"),
     [("CLIP 模型缓存", ("~", ".cache", "clip"))]),
    (("keras", "tensorflow"),
     [("Keras/TF 模型与数据", ("~", ".keras"))]),
    (("nltk",),
     [("NLTK 语料库", ("~", "nltk_data")),
      ("NLTK 语料库", ("%APPDATA%", "nltk_data"))]),
    (("modelscope",),
     [("ModelScope 模型缓存", ("~", ".cache", "modelscope"))]),
    (("paddlepaddle", "paddlenlp", "paddlehub", "paddleocr"),
     [("Paddle 模型缓存", ("~", ".cache", "paddle")),
      ("PaddleNLP 模型缓存", ("~", ".paddlenlp")),
      ("PaddleHub 模块", ("~", ".paddlehub"))]),
    (("kagglehub",),
     [("Kaggle 数据/模型缓存", ("~", ".cache", "kagglehub"))]),
    (("gdown",),
     [("gdown 下载缓存", ("~", ".cache", "gdown"))]),
]

CHECKPOINTS_DIR = "checkpoints"      # torch hub 下需要再展开一层的子目录


def _base(name: str) -> str:
    if name == "~":
        return os.path.expanduser("~")
    return os.environ.get(name, "")


def _norm_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def installed_lib_names(site_packages: str) -> set:
    """从 site-packages 的元数据目录名提取已安装的库（归一化名称）"""
    names = set()
    if not site_packages or not os.path.isdir(site_packages):
        return names
    try:
        entries = os.listdir(site_packages)
    except OSError:
        return names
    for n in entries:
        low = n.lower()
        stem = None
        if low.endswith(".dist-info"):
            stem = low[:-len(".dist-info")]
        elif low.endswith(".egg-info"):
            stem = low[:-len(".egg-info")]
        if stem is None:
            continue
        name = stem.rsplit("-", 1)[0] if "-" in stem else stem
        names.add(_norm_name(name))
    return names


def _make_entry(path: str, group: str, kind: str):
    try:
        size = dir_size(path) if os.path.isdir(path) else os.path.getsize(path)
    except OSError:
        return None
    if size < ENTRY_MIN:
        return None
    return {"kind": kind, "group": group, "path": path, "size": size}


def cache_entries(site_packages: str) -> list:
    """已安装 ML 库对应的模型缓存子项，按大小降序"""
    libs = installed_lib_names(site_packages)
    entries, seen_roots, seen_paths = [], set(), set()
    for lib_names, caches in LIB_CACHES:
        if not (libs & set(lib_names)):
            continue
        for label, parts in caches:
            base = _base(parts[0])
            if not base:
                continue
            d = os.path.join(base, *parts[1:])
            if not os.path.isdir(d):
                continue
            key = os.path.normcase(d)
            if key in seen_roots:
                continue
            seen_roots.add(key)
            try:
                children = sorted(os.listdir(d))
            except OSError:
                continue
            for child in children:
                full = os.path.join(d, child)
                if child == CHECKPOINTS_DIR and os.path.isdir(full):
                    # torch hub 的 checkpoints 再展开一层（里面才是 .pt 文件）
                    try:
                        subs = sorted(os.listdir(full))
                    except OSError:
                        continue
                    for sub in subs:
                        full_sub = os.path.join(full, sub)
                        pkey = os.path.normcase(full_sub)
                        if pkey in seen_paths:
                            continue
                        seen_paths.add(pkey)
                        e = _make_entry(full_sub, label, "cache")
                        if e:
                            entries.append(e)
                else:
                    pkey = os.path.normcase(full)
                    if pkey in seen_paths:
                        continue
                    seen_paths.add(pkey)
                    e = _make_entry(full, label, "cache")
                    if e:
                        entries.append(e)
    entries.sort(key=lambda e: -e["size"])
    return entries


def env_model_files(env_root: str) -> list:
    """环境目录内的大体积模型权重文件（属于某个库的一部分，删除需谨慎）"""
    out = []
    if not env_root or not os.path.isdir(env_root):
        return out
    for dirpath, dirnames, filenames in os.walk(env_root):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in MODEL_EXTS:
                continue
            full = os.path.join(dirpath, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size >= BIG_FILE_MIN:
                out.append({"kind": "envfile", "group": "环境内模型文件",
                            "path": full, "size": size})
    out.sort(key=lambda e: -e["size"])
    return out


def model_entries(env_root: str, site_packages: str) -> list:
    """汇总两类模型条目（缓存 + 环境内大文件）"""
    return cache_entries(site_packages) + env_model_files(env_root)


def delete_model_entries(entries: list, progress=None):
    """永久删除所选条目，返回 (释放字节数, 错误列表)。

    progress(已完成数, 总数, 已释放字节数) 每删完一项回调。
    """
    total_freed = 0
    errors = []
    done = 0
    for e in entries:
        path = e["path"]
        if os.path.isdir(path):
            errors.extend(robust_rmtree(path))
        else:
            try:
                os.remove(path)
            except PermissionError:
                try:
                    os.chmod(path, 0o666)
                    os.remove(path)
                except OSError as exc:
                    errors.append(str(exc))
            except OSError as exc:
                errors.append(str(exc))
        total_freed += e["size"]
        done += 1
        if progress:
            try:
                progress(done, len(entries), total_freed)
            except Exception:
                pass
    return total_freed, errors
