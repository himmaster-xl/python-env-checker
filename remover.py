# -*- coding: utf-8 -*-
"""环境删除、包卸载、查找官方卸载程序"""
from __future__ import annotations

import os
import stat
import subprocess
import winreg

from scanner import CREATE_NO_WINDOW


def robust_rmtree(path: str, progress=None) -> list[str]:
    """删除目录树：自动清除只读属性、支持超长路径。

    返回错误列表；空列表表示完全删除成功。progress(已删除文件数) 可选。
    """
    root = os.path.abspath(path)
    errors: list[str] = []
    target = root if root.startswith("\\\\?\\") else "\\\\?\\" + root
    count = 0

    def walk_onerror(exc):
        errors.append(str(exc))

    for dirpath, dirnames, filenames in os.walk(target, topdown=False,
                                                onerror=walk_onerror):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            try:
                os.chmod(fp, stat.S_IWRITE)
            except OSError:
                pass
            try:
                os.remove(fp)
            except OSError as e:
                errors.append(str(e))
            count += 1
            if progress and count % 100 == 0:
                progress(count)
        for dn in dirnames:
            dp = os.path.join(dirpath, dn)
            try:
                os.chmod(dp, stat.S_IWRITE)
            except OSError:
                pass
            try:
                os.rmdir(dp)
            except OSError as e:
                errors.append(str(e))
    try:
        os.rmdir(target)
    except OSError as e:
        errors.append(str(e))
    return errors


def uninstall_package(python_path: str, package_name: str):
    """用该环境自带的 pip 卸载包，返回 (是否成功, 输出文本)"""
    if not python_path or not os.path.isfile(python_path):
        return False, "该环境缺少可用的 python.exe，无法调用 pip。"
    try:
        p = subprocess.run(
            [python_path, "-m", "pip", "uninstall", "-y", package_name],
            capture_output=True, timeout=600, creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        return False, f"启动 pip 失败：{e}"
    out = (p.stdout or b"").decode("utf-8", "replace") + \
          (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode == 0, out.strip()[-1500:]


def find_uninstaller(env_root: str) -> str:
    """在注册表卸载信息中查找与该环境匹配的官方卸载命令"""
    target = os.path.normcase(os.path.normpath(env_root))
    for hive, extra in ((winreg.HKEY_LOCAL_MACHINE, 0),
                        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                        (winreg.HKEY_CURRENT_USER, 0)):
        try:
            key = winreg.OpenKey(
                hive,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                0, winreg.KEY_READ | extra)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    sk = winreg.OpenKey(key, sub)
                except OSError:
                    continue
                with sk:
                    vals = {}
                    for name in ("DisplayName", "InstallLocation",
                                 "UninstallString", "DisplayIcon"):
                        try:
                            vals[name] = winreg.QueryValueEx(sk, name)[0]
                        except OSError:
                            pass
                loc = vals.get("InstallLocation", "")
                icon = vals.get("DisplayIcon", "").split(",")[0]
                if loc and os.path.normcase(os.path.normpath(loc)) == target:
                    return vals.get("UninstallString", "")
                if icon and os.path.normcase(os.path.dirname(icon)) == target:
                    return vals.get("UninstallString", "")
    return ""
