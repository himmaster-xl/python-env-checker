# -*- coding: utf-8 -*-
"""扫描 Windows 上的 Python 环境（官方安装 / venv / conda / pyenv / 商店版 / 便携版）"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import winreg
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# 防止调用子进程时闪出黑色控制台窗口
CREATE_NO_WINDOW = 0x08000000

TYPE_LABELS = {
    "official": "官方安装",
    "venv": "虚拟环境",
    "conda": "Conda",
    "conda_env": "Conda 环境",
    "pyenv": "pyenv",
    "store": "商店版",
    "portable": "便携版",
}


@dataclass
class PythonEnv:
    name: str = ""
    python_path: str = ""
    root: str = ""
    version: str = "?"
    env_type: str = "portable"
    site_packages: str = ""
    total_size: int = -1        # 字节；-1 表示尚未统计
    packages_size: int = -1
    packages_count: int = -1
    broken: bool = False        # 环境存在但解释器无法运行
    base_version: str = ""      # venv 声明的基础版本
    uid: str = field(default="")

    @property
    def type_label(self) -> str:
        return TYPE_LABELS.get(self.env_type, self.env_type)


def _decode(b) -> str:
    if b is None:
        return ""
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else str(b)


def _run(args, timeout=12):
    # 防线：打包成 exe 后，sys.executable 是本程序自身，绝不能执行它，
    # 否则每次扫描都会启动一个新实例（无限自我繁殖）
    if args and getattr(sys, "frozen", False) and \
            os.path.normcase(os.path.abspath(args[0])) == \
            os.path.normcase(os.path.abspath(sys.executable)):
        return None
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
    except Exception:
        return None
    return _decode(p.stdout) + _decode(p.stderr)


def detect_version(python_path: str):
    out = _run([python_path, "--version"])
    if out:
        m = re.search(r"Python\s+(\d+\.\d+(?:\.\d+)?)", out)
        if m:
            return m.group(1)
    return None


def _read_pyvenv_cfg(cfg_path: str) -> dict:
    info = {}
    try:
        with open(cfg_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return info


def _find_python_in_root(root: str) -> str:
    """环境根目录里的解释器：venv 在 Scripts 下，其余在根目录"""
    for cand in (os.path.join(root, "Scripts", "python.exe"),
                 os.path.join(root, "python.exe")):
        if os.path.isfile(cand):
            return cand
    return os.path.join(root, "python.exe")


def _classify(python_path: str):
    """根据路径特征判断环境类型；返回 None 表示应跳过（如商店占位符）"""
    root = os.path.dirname(os.path.normpath(python_path))
    low = root.lower()
    if "windowsapps" in low:
        if os.path.basename(root).lower().startswith("pythonsoftwarefoundation"):
            return "store"
        return None  # 商店占位 stub，不是真实环境
    # venv 的解释器位于 <venv>\Scripts\python.exe
    if os.path.basename(root).lower() == "scripts":
        parent = os.path.dirname(root)
        if os.path.isfile(os.path.join(parent, "pyvenv.cfg")):
            return "venv"
    if os.path.isfile(os.path.join(root, "pyvenv.cfg")):
        return "venv"
    if os.path.isdir(os.path.join(root, "conda-meta")):
        if os.path.basename(os.path.dirname(root)).lower() == "envs":
            return "conda_env"
        return "conda"
    if ".pyenv" in low and "pyenv-win" in low:
        return "pyenv"
    return "portable"


class ScanResult:
    """收集去重后的环境列表"""

    def __init__(self):
        self.envs: list[PythonEnv] = []
        self._keys = set()

    def add(self, python_path="", env_type=None, name="", root="", broken=False):
        if root:
            root = os.path.normpath(root)
            exe = python_path or _find_python_in_root(root)
        else:
            exe = os.path.normpath(python_path)
            root = os.path.dirname(exe)
        if not root or not os.path.isdir(root):
            return

        if env_type is None:
            env_type = _classify(exe) if os.path.isfile(exe) else "venv"
            if env_type is None:
                return
        if env_type == "venv" and os.path.basename(root).lower() == "scripts":
            # 通过 Scripts\python.exe 找到的 venv，根目录取上一级
            root = os.path.dirname(root)
        if env_type == "official" and os.path.isdir(os.path.join(root, "conda-meta")):
            # Anaconda/Miniconda 也会注册到 PythonCore 下，按 conda 识别更准确
            env_type = "conda"
        if env_type == "venv" and not os.path.isfile(exe):
            # 允许列出损坏的 venv，方便用户清理
            broken = True

        key = os.path.normcase(os.path.realpath(exe if not broken else root))
        if key in self._keys:
            return
        self._keys.add(key)

        env = PythonEnv(
            name=name or os.path.basename(root),
            python_path="" if broken else exe,
            root=root,
            env_type=env_type,
            site_packages=os.path.join(root, "Lib", "site-packages"),
            broken=broken,
        )
        if broken:
            cfg = _read_pyvenv_cfg(os.path.join(root, "pyvenv.cfg"))
            env.version = cfg.get("version", "无法获取") or "无法获取"
            env.base_version = cfg.get("home", "")
        self.envs.append(env)


# ---------------- 各个发现渠道 ----------------

def _registry_envs(res: ScanResult):
    """PEP 514：HKLM/HKCU 下的 SOFTWARE\Python\<公司>\<标签>\InstallPath"""
    for hive, extra in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                        (winreg.HKEY_CURRENT_USER, 0)):
        try:
            companies = winreg.OpenKey(hive, r"SOFTWARE\Python", 0, winreg.KEY_READ | extra)
        except OSError:
            continue
        with companies:
            i = 0
            while True:
                try:
                    company = winreg.EnumKey(companies, i)
                except OSError:
                    break
                i += 1
                try:
                    ck = winreg.OpenKey(companies, company)
                except OSError:
                    continue
                with ck:
                    j = 0
                    while True:
                        try:
                            tag = winreg.EnumKey(ck, j)
                        except OSError:
                            break
                        j += 1
                        try:
                            tk = winreg.OpenKey(ck, tag)
                        except OSError:
                            continue
                        with tk:
                            try:
                                ip = winreg.OpenKey(tk, "InstallPath")
                            except OSError:
                                continue
                            with ip:
                                try:
                                    exe_dir = winreg.QueryValueEx(ip, None)[0] or ""
                                except OSError:
                                    exe_dir = ""
                                try:
                                    exe = winreg.QueryValueEx(ip, "ExecutablePath")[0] or ""
                                except OSError:
                                    exe = ""
                            try:
                                sysver = winreg.QueryValueEx(tk, "SysVersion")[0] or ""
                            except OSError:
                                sysver = ""
                        exe_dir = exe_dir.strip().strip('"')
                        exe = exe.strip().strip('"')
                        if not exe and exe_dir:
                            exe = os.path.join(os.path.normpath(exe_dir), "python.exe")
                        if exe and os.path.isfile(exe):
                            env_type = "official" if company == "PythonCore" else "conda"
                            name = f"Python {sysver or tag}"
                            res.add(python_path=exe, env_type=env_type, name=name)


def _launcher_envs(res: ScanResult):
    """py 启动器：py -0p"""
    out = _run(["py", "-0p"], timeout=15)
    if not out:
        return
    for line in out.splitlines():
        m = re.search(r"([A-Za-z]:\\.+?python(?:w)?\.exe)\s*$", line.strip(), re.I)
        if m:
            res.add(python_path=m.group(1), env_type=None, name="")


def _path_envs(res: ScanResult):
    """PATH 上的 python.exe"""
    out = _run(["where", "python"], timeout=15)
    for line in (out or "").splitlines():
        p = line.strip().strip('"')
        if p.lower().endswith("python.exe") and os.path.isfile(p):
            res.add(python_path=p, env_type=None, name="")


def _common_dir_envs(res: ScanResult):
    """常见安装目录"""
    local = os.environ.get("LOCALAPPDATA", "")
    patterns = [
        os.path.join(local, "Programs", "Python", "Python*"),
        os.path.join(local, "Microsoft", "WindowsApps", "PythonSoftwareFoundation*"),
        r"C:\Python*",
        r"C:\Program Files\Python*",
        r"C:\Program Files (x86)\Python*",
    ]
    for pat in patterns:
        for d in glob.glob(pat):
            exe = os.path.join(d, "python.exe")
            if os.path.isfile(exe):
                res.add(python_path=exe, env_type=None, name="")


CONDA_ROOTS = [
    os.path.join(os.environ.get("USERPROFILE", ""), "anaconda3"),
    os.path.join(os.environ.get("USERPROFILE", ""), "miniconda3"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Anaconda3"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Miniconda3"),
    r"C:\ProgramData\anaconda3", r"C:\ProgramData\miniconda3",
    r"C:\ProgramData\Anaconda3", r"C:\ProgramData\Miniconda3",
    r"C:\anaconda3", r"C:\miniconda3",
    r"C:\Program Files\anaconda3", r"C:\Program Files\miniconda3",
]


def _conda_envs(res: ScanResult):
    """conda 主环境与子环境（含 environments.txt 记录的历史路径）"""
    candidates = [r for r in CONDA_ROOTS if r and os.path.isfile(os.path.join(r, "python.exe"))]
    txt = os.path.join(os.environ.get("USERPROFILE", ""), ".conda", "environments.txt")
    if os.path.isfile(txt):
        try:
            with open(txt, encoding="utf-8", errors="replace") as f:
                for line in f:
                    d = line.strip()
                    if d and os.path.isdir(d) and os.path.isfile(os.path.join(d, "python.exe")):
                        candidates.append(d)
        except OSError:
            pass
    for root in candidates:
        root = os.path.normpath(root)
        if os.path.isdir(os.path.join(root, "conda-meta")):
            res.add(python_path=os.path.join(root, "python.exe"), env_type="conda")
        envs_dir = os.path.join(root, "envs")
        if os.path.isdir(envs_dir):
            try:
                names = os.listdir(envs_dir)
            except OSError:
                continue
            for name in names:
                d = os.path.join(envs_dir, name)
                if os.path.isfile(os.path.join(d, "python.exe")):
                    res.add(python_path=os.path.join(d, "python.exe"), env_type="conda_env")


def _pyenv_envs(res: ScanResult):
    user = os.environ.get("USERPROFILE", "")
    base = os.path.join(user, ".pyenv", "pyenv-win", "versions")
    if os.path.isdir(base):
        for name in os.listdir(base):
            exe = os.path.join(base, name, "python.exe")
            if os.path.isfile(exe):
                res.add(python_path=exe, env_type="pyenv")


VENV_SKIP_DIRS = {".git", ".idea", ".vscode", "node_modules", "__pycache__",
                  "site-packages", "$RECYCLE.BIN", "System Volume Information"}


def _venv_envs(res: ScanResult, max_depth=4):
    """在常用目录中按 pyvenv.cfg 浅层搜索虚拟环境"""
    user = os.environ.get("USERPROFILE", "")
    roots = [os.path.join(user, "Desktop"),
             os.path.join(user, "OneDrive", "Desktop"),
             os.path.join(user, "Documents"),
             os.path.join(user, "OneDrive", "Documents"),
             os.path.join(user, "Downloads"),
             os.path.join(user, ".virtualenvs"),
             os.path.join(user, "envs"),
             os.getcwd()]
    seen_roots = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        rkey = os.path.normcase(os.path.realpath(root))
        if rkey in seen_roots:
            continue
        seen_roots.add(rkey)
        for dirpath, dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            if rel != "." and rel.count(os.sep) >= max_depth:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d not in VENV_SKIP_DIRS]
            if "pyvenv.cfg" in filenames:
                res.add(root=dirpath, env_type="venv")


# ---------------- 版本与命名 ----------------

def _fill_versions(envs: list[PythonEnv]):
    def work(env: PythonEnv):
        if env.broken or not env.python_path:
            return
        ver = detect_version(env.python_path)
        if ver:
            env.version = ver
        else:
            env.broken = True
            env.version = "无法获取"
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, envs))


def _finalize_names(envs: list[PythonEnv]):
    used = {}
    for env in envs:
        if env.env_type == "official":
            env.name = f"Python {env.version}"
        elif env.env_type == "store":
            env.name = f"Python {env.version} (商店)"
        elif env.env_type == "conda":
            env.name = f"{os.path.basename(env.root)} (Conda)"
        elif not env.name:
            env.name = os.path.basename(env.root)
        base = env.name
        n = used.get(base, 0) + 1
        used[base] = n
        if n > 1:
            env.name = f"{base} #{n}"


def scan_all(progress=None) -> list[PythonEnv]:
    """扫描全部环境；progress(text) 用于向界面汇报进度"""
    progress = progress or (lambda t: None)
    res = ScanResult()

    progress("正在读取注册表中的 Python 安装记录...")
    _registry_envs(res)
    _path_envs(res)
    _launcher_envs(res)

    progress("正在检查常见安装目录...")
    _common_dir_envs(res)
    _conda_envs(res)
    _pyenv_envs(res)

    # 把当前运行本程序的解释器也列出。注意：打包成 exe 后 sys.executable
    # 是本程序自身而非 Python 解释器，绝不能加入（否则会自我执行、反复启动）
    if os.path.basename(sys.executable).lower() in ("python.exe", "pythonw.exe") \
            and os.path.isfile(sys.executable):
        res.add(python_path=sys.executable, env_type=None)

    progress("正在搜索虚拟环境（可能需要几秒）...")
    _venv_envs(res)

    progress("正在获取各环境的版本号...")
    _fill_versions(res.envs)
    _finalize_names(res.envs)
    return res.envs


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for e in scan_all(progress=lambda t: print(">", t)):
        flag = " [损坏]" if e.broken else ""
        print(f"{e.name:<28} {e.version:<12} {e.type_label:<10} {e.root}{flag}")
