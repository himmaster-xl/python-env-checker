# -*- coding: utf-8 -*-
"""环境删除后的系统残留清理：注册表、开始菜单快捷方式、environments.txt、缓存。

安全原则（保证系统安全性的核心设计）：
1. 精确匹配：只处理 InstallPath/目标/参数/图标中精确指向"已删除环境根目录"的
   条目；路径必须等于根目录或位于根目录内部（带分隔符判断，避免误伤
   miniconda3 与 miniconda3-env 这类前缀相似的环境），绝不整树删除。
2. 先备份：删除任何注册表键之前，先用 reg export 导出到
   ~/.py_env_checker_backup/<时间戳>/，误删可双击 .reg 文件还原。
3. 权限降级：HKLM 写入需要管理员权限，无权限时跳过该项并在报告中说明，
   绝不因此中断其余清理。
4. 缓存只清理固定路径（pip cache、conda 主环境的 pkgs），pkgs 仅在确认
   父目录含 conda-meta 时才会被清理。
"""
from __future__ import annotations

import os
import stat
import subprocess
import time
import winreg

from remover import robust_rmtree
from scanner import CREATE_NO_WINDOW
from sizes import dir_size

BACKUP_ROOT = os.path.join(os.path.expanduser("~"), ".py_env_checker_backup")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _match_root(field: str, root: str) -> bool:
    """判断快捷方式的目标/参数/图标文本是否指向 root 或其内部。

    参数文本不是纯路径（如 "/K C:\\x\\activate.bat C:\\x"），因此只做
    normcase 后的包含判断；带分隔符比较，避免 "C:\\x\\miniconda3"
    误匹配 "C:\\x\\miniconda3-env" 这类前缀相似的环境。
    """
    if not field:
        return False
    f = os.path.normcase(field.replace('"', "").strip())
    r = os.path.normcase(os.path.normpath(root))
    if not f or not r:
        return False
    return f == r or (r + os.sep) in f or f.endswith(r)


# ---------------- 注册表 ----------------

def _enum_subkeys(hive, path: str) -> list[str]:
    try:
        k = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
    except OSError:
        return []
    with k:
        out, i = [], 0
        while True:
            try:
                out.append(winreg.EnumKey(k, i))
                i += 1
            except OSError:
                break
        return out


def _reg_delete_tree(hive, path: str):
    """自底向上删除整个键（winreg.DeleteKey 只能删除没有子键的键）"""
    for sub in _enum_subkeys(hive, path):
        _reg_delete_tree(hive, path + "\\" + sub)
    winreg.DeleteKey(hive, path)


def _backup_reg(hive_str: str, path: str, backup_dir: str):
    os.makedirs(backup_dir, exist_ok=True)
    safe = path.replace("\\", "_").replace(" ", "_") or "key"
    file = os.path.join(backup_dir, f"{hive_str}_{safe}.reg")
    try:
        p = subprocess.run(["reg", "export", f"{hive_str}\\{path}", file, "/y"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW,
                           timeout=30)
        if p.returncode == 0:
            return file
    except Exception:
        pass
    return None


_PEP514_BASES = (
    (winreg.HKEY_CURRENT_USER, "HKCU", r"Software\Python"),
    (winreg.HKEY_LOCAL_MACHINE, "HKLM", r"Software\Python"),
    (winreg.HKEY_LOCAL_MACHINE, "HKLM", r"Software\WOW6432Node\Python"),
)

_UNINSTALL_BASES = (
    (winreg.HKEY_CURRENT_USER, "HKCU",
     r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, "HKLM",
     r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, "HKLM",
     r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
)


def _pep514_targets(root: str):
    """返回所有 InstallPath 指向 root 的 PEP 514 注册键 [(hive, hive_str, 键路径)]"""
    root_n = _norm(root)
    found = []
    for hive, hive_str, base in _PEP514_BASES:
        for company in _enum_subkeys(hive, base):
            company_path = base + "\\" + company
            for tag in _enum_subkeys(hive, company_path):
                install_path = company_path + "\\" + tag + r"\InstallPath"
                try:
                    k = winreg.OpenKey(hive, install_path, 0, winreg.KEY_READ)
                except OSError:
                    continue
                with k:
                    try:
                        d = winreg.QueryValueEx(k, None)[0] or ""
                    except OSError:
                        d = ""
                    try:
                        exe = winreg.QueryValueEx(k, "ExecutablePath")[0] or ""
                    except OSError:
                        exe = ""
                if (d and _norm(d) == root_n) or \
                        (exe and _norm(os.path.dirname(exe)) == root_n):
                    found.append((hive, hive_str, company_path + "\\" + tag))
    return found


def _uninstall_targets(root: str):
    """返回 InstallLocation/DisplayIcon 指向 root 的卸载注册键"""
    root_n = _norm(root)
    found = []
    for hive, hive_str, base in _UNINSTALL_BASES:
        for sub in _enum_subkeys(hive, base):
            key_path = base + "\\" + sub
            try:
                k = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ)
            except OSError:
                continue
            with k:
                vals = {}
                for name in ("InstallLocation", "DisplayIcon"):
                    try:
                        vals[name] = winreg.QueryValueEx(k, name)[0]
                    except OSError:
                        pass
            loc = vals.get("InstallLocation", "")
            icon = vals.get("DisplayIcon", "").split(",")[0]
            if (loc and _norm(loc) == root_n) or \
                    (icon and _norm(os.path.dirname(icon)) == root_n):
                found.append((hive, hive_str, key_path))
    return found


# ---------------- 开始菜单快捷方式 ----------------

def _shortcut_roots() -> list[str]:
    dirs = []
    for env_name in ("APPDATA", "ProgramData"):
        base = os.environ.get(env_name, "")
        if base:
            d = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")
            if os.path.isdir(d):
                dirs.append(d)
    return dirs


def _list_shortcuts(search_dirs: list[str]):
    """用 PowerShell 解析每个 .lnk 的真实目标，返回 [(lnk, target, args, icon)]"""
    results = []
    for d in search_dirs:
        script = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "Get-ChildItem -LiteralPath '%s' -Recurse -Filter *.lnk "
            "-ErrorAction SilentlyContinue | ForEach-Object { "
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($_.FullName); "
            "Write-Output ($_.FullName + '|' + $s.TargetPath + '|' + "
            "$s.Arguments + '|' + $s.IconLocation) }"
            % d.replace("'", "''")
        )
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                               capture_output=True, creationflags=CREATE_NO_WINDOW,
                               timeout=120)
        except Exception:
            continue
        for line in p.stdout.decode("utf-8", "replace").splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4 and parts[0].lower().endswith(".lnk"):
                results.append(tuple(parts))
    return results


def clean_shortcuts(root: str, log=None, search_dirs=None) -> int:
    """删除目标指向 root 的开始菜单快捷方式，返回删除数量"""
    log = log or (lambda s: None)
    dirs = search_dirs if search_dirs is not None else _shortcut_roots()
    if not dirs:
        return 0
    removed = 0
    for lnk, target, args, icon in _list_shortcuts(dirs):
        if any(_match_root(f, root) for f in (target, args, icon)):
            try:
                os.remove(lnk)
                removed += 1
                log(f"已删除快捷方式：{os.path.basename(lnk)}")
            except OSError as e:
                log(f"无法删除快捷方式 {lnk}：{e}")
    return removed


# ---------------- conda environments.txt ----------------

def environments_txt_path() -> str:
    return os.path.join(os.environ.get("USERPROFILE", ""),
                        ".conda", "environments.txt")


def clean_environments_txt(path=None) -> int:
    """移除其中已不存在的目录条目，返回移除行数"""
    path = path or environments_txt_path()
    if not path or not os.path.isfile(path):
        return 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = [ln.rstrip("\r\n") for ln in f]
    except OSError:
        return 0
    kept, removed = [], 0
    for ln in lines:
        s = ln.strip()
        if s and not os.path.isdir(s):
            removed += 1
            continue
        kept.append(ln)
    if removed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(kept) + ("\n" if kept else ""))
        except OSError:
            return 0
    return removed


# ---------------- 缓存 ----------------

def pip_cache_path() -> str:
    local = os.environ.get("LOCALAPPDATA", "")
    p = os.path.join(local, "pip", "cache") if local else ""
    return p if p and os.path.isdir(p) else ""


def conda_pkgs_paths(conda_roots) -> list[str]:
    """返回各 Conda 主环境的 pkgs 缓存目录（仅当父目录确有 conda-meta）"""
    out = []
    for root in conda_roots:
        if root and os.path.isdir(os.path.join(root, "conda-meta")):
            p = os.path.join(root, "pkgs")
            if os.path.isdir(p):
                out.append(p)
    return out


def clean_dir_contents(path: str, progress=None):
    """清空目录内容（保留目录本身），返回 (释放字节数, 错误列表)。

    progress(已完成条目数, 总条目数, 已释放字节数) 在每删完一项后回调。
    """
    freed = 0
    errors = []
    try:
        names = os.listdir(path)
    except OSError as e:
        return 0, [f"无法读取目录 {path}：{e}"]
    total = len(names)
    done = 0
    for name in names:
        full = os.path.join(path, name)
        if os.path.isdir(full):
            freed += dir_size(full)
            errors.extend(robust_rmtree(full))
        else:
            try:
                freed += os.path.getsize(full)
            except OSError:
                pass
            try:
                os.remove(full)
            except PermissionError:
                try:
                    os.chmod(full, stat.S_IWRITE)
                    os.remove(full)
                except OSError as e:
                    errors.append(str(e))
            except OSError as e:
                errors.append(str(e))
        done += 1
        if progress:
            try:
                progress(done, total, freed)
            except Exception:
                pass
    return freed, errors


# ---------------- 总入口 ----------------

def deep_clean(root: str, log=None) -> dict:
    """删除环境后清理系统残留，返回报告。

    报告字段：reg_found/reg_deleted（注册表）、shortcuts（快捷方式）、
    envtxt（environments.txt 死条目）、backup_dir（备份目录）、messages（明细）。
    """
    messages: list[str] = []
    log = log or messages.append
    report = {"reg_found": 0, "reg_deleted": 0, "shortcuts": 0, "envtxt": 0,
              "backup_dir": "", "messages": messages}
    backup_dir = os.path.join(BACKUP_ROOT, time.strftime("%Y%m%d_%H%M%S"))
    report["backup_dir"] = backup_dir

    targets = _pep514_targets(root) + _uninstall_targets(root)
    report["reg_found"] = len(targets)
    for hive, hive_str, path in targets:
        _backup_reg(hive_str, path, backup_dir)
        try:
            _reg_delete_tree(hive, path)
            report["reg_deleted"] += 1
            log(f"已删除注册表键：{hive_str}\\{path}")
        except PermissionError:
            log(f"跳过 {hive_str}\\{path}（需要管理员权限）")
        except OSError as e:
            log(f"删除注册表 {hive_str}\\{path} 失败：{e}")

    report["shortcuts"] = clean_shortcuts(root, log)
    report["envtxt"] = clean_environments_txt()
    return report
