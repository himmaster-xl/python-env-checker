# -*- coding: utf-8 -*-
"""Python 环境检查器 — 查看、管理、删除电脑上的 Python 环境

用法:
    python main.py             # 启动图形界面
    python main.py --selftest  # 命令行自检（扫描环境并打印结果）
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk, messagebox

import cleaner
import models as modelscan
import packages as pkgmod
import remover
import scanner
import sizes


def _log(msg: str):
    """写运行日志（exe 无控制台模式下 print 会失败，统一走这里）"""
    try:
        path = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                            "python环境检查器.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")
    except OSError:
        pass

# ---------------- 字体与外观 ----------------
FONT = ("Microsoft YaHei UI", 11)
FONT_SMALL = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 11, "bold")
FONT_NAME = ("Microsoft YaHei UI", 14, "bold")
FONT_NUM = ("Microsoft YaHei UI", 17, "bold")

TYPE_COLORS = {
    "official": "#1565c0",
    "conda": "#2e7d32",
    "conda_env": "#00838f",
    "venv": "#6a1b9a",
    "pyenv": "#8d6e63",
    "store": "#546e7a",
    "portable": "#37474f",
    "broken": "#c62828",
}

COLUMNS = ("name", "version", "type", "path", "total", "pkg", "count")
HEADERS = {"name": "名称", "version": "版本", "type": "类型", "path": "所在路径",
           "total": "环境总大小", "pkg": "包大小", "count": "包数量"}
WIDTHS = {"name": 195, "version": 100, "type": 110, "path": 340,
          "total": 250, "pkg": 230, "count": 80}

SORT_KEYS = {
    "name": lambda e: e.name.lower(),
    "version": lambda e: e.version,
    "type": lambda e: e.type_label,
    "path": lambda e: e.root.lower(),
    "total": lambda e: e.total_size if e.total_size >= 0 else -1,
    "pkg": lambda e: e.packages_size if e.packages_size >= 0 else -1,
    "count": lambda e: e.packages_count if e.packages_count >= 0 else -1,
}


def size_bar(n: int, max_n: int, width: int = 10) -> str:
    """文本容量条：▇ 已用部分，░ 空余部分"""
    if n is None or n < 0 or max_n <= 0:
        return ""
    filled = max(1, min(width, int(round(n / max_n * width))))
    return "█" * filled + "░" * (width - filled)


class PixelCatProgress(tk.Canvas):
    """卡通小猫进度条（凉屋风）：白猫大眼粗描边，从左往右跑。

    两种用法：
    - start()/stop()：匀速跑动，适合时长未知的任务（扫描、删除）
    - set_fraction(0~1)：小猫位置表示完成比例（缓存清理）
    """

    P = 3                      # 每个逻辑像素的边长（物理像素）
    COLS, ROWS = 22, 11
    BODY = "#8f8a5f"           # 橄榄卡其（参考图配色）
    EYE = "#f4f0dc"            # 浅色大眼（比身体浅）

    def __init__(self, master, width=None, **kw):
        if width is None:
            width = self.COLS * self.P + 6
        super().__init__(master, width=width,
                         height=self.ROWS * self.P + 2,
                         highlightthickness=0, bg="#f0f0f0", **kw)
        self._frames = [self._sprite(i) for i in range(4)]
        self._job = None
        self._tick = 0
        self._x = 0
        self._fraction = None
        self._running = False
        self.bind("<Configure>", lambda e: self._draw())

    def _sprite(self, frame):
        """绘制一帧小猫（参考图风格：扁平橄榄色块状猫，浅色大眼，无描边）。

        4 帧跑步循环：帧 1/3 整体上抬 1 像素模拟腾空，尾巴尖摆动。
        """
        W, H = self.COLS, self.ROWS
        g = [[0] * W for _ in range(H)]

        def rect(r0, r1, c0, c1, v=1):
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    g[r][c] = v

        # 方块大头 + 两个尖耳
        rect(1, 5, 11, 21)
        g[0][13] = 1
        g[0][19] = 1
        # 浅色大眼（竖椭圆）
        rect(2, 4, 13, 14, 2)
        rect(2, 4, 18, 19, 2)
        # 低矮的身体（与头部下缘相接）
        rect(4, 8, 4, 12)
        # 粗尾巴（从身体后部竖起，帧间摆动）
        rect(3, 7, 2, 3)
        if frame in (0, 2):
            g[2][2] = 1            # 尾巴尖向左摆
        else:
            g[2][3] = 1            # 尾巴尖向右摆
        # 粗短腿（2×2，4 帧循环）
        back0, back1, front0, front1 = {0: (4, 5, 10, 11),
                                        1: (5, 6, 9, 10),
                                        2: (6, 7, 8, 9),
                                        3: (5, 6, 9, 10)}[frame]
        rect(9, 10, back0, back1)
        rect(9, 10, front0, front1)

        color = {1: self.BODY, 2: self.EYE}
        img = tk.PhotoImage(width=W, height=H)
        for r in range(H):
            for c in range(W):
                v = g[r][c]
                if v:
                    img.put(color[v], (c, r))
        return img.zoom(self.P)

    def _draw(self, frame=None):
        self.delete("all")
        if not self._running:
            return
        if frame is None:
            frame = (self._tick // 2) % 4
        bob = self.P if frame in (1, 3) else 0   # 迈步腾空：整体上抬 1 像素
        self.create_image(self._x, 1 - bob, image=self._frames[frame], anchor="nw")

    def _loop(self):
        if not self._running:
            return
        self._tick += 1
        w = max(self.winfo_width(), int(self["width"]))
        img_w = self.COLS * self.P
        if self._fraction is None:
            self._x += 2
            if self._x > w:
                self._x = -img_w          # 跑出右侧后从左边重新进场
        else:
            self._x = int(max(0, w - img_w - 2) * self._fraction)
        self._draw()
        self._job = self.after(35, self._loop)

    def start(self, _interval=None):
        self._running = True
        self._x = 0
        self._fraction = None
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._loop()

    def stop(self):
        self._running = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self.delete("all")

    def set_fraction(self, f):
        self._fraction = max(0.0, min(1.0, f))
        w = max(self.winfo_width(), int(self["width"]))
        img_w = self.COLS * self.P
        self._x = int(max(0, w - img_w - 2) * self._fraction)
        if self._running:
            self._draw()


class ConfirmDialog(tk.Toplevel):
    """删除环境前的确认对话框"""

    def __init__(self, master, env: scanner.PythonEnv):
        super().__init__(master)
        self.result = False
        self.deep_clean = False
        self.title("确认删除")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        frm = ttk.Frame(self, padding=24)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="⚠ 即将删除 Python 环境",
                  font=("Microsoft YaHei UI", 14, "bold"),
                  foreground="#c62828").pack(anchor="w")
        info = (f"名称：{env.name}\n"
                f"类型：{env.type_label}\n"
                f"版本：{env.version}\n"
                f"路径：{env.root}\n"
                f"环境总大小：{sizes.format_size(env.total_size)}　　"
                f"包大小：{sizes.format_size(env.packages_size)}\n\n"
                "该环境的全部文件（含已安装的包）删除后将无法恢复！")
        ttk.Label(frm, text=info, justify="left", font=FONT).pack(anchor="w", pady=(12, 0))

        # 官方安装版 / Conda：提供系统残留深度清理选项
        if env.env_type in ("official", "conda", "conda_env"):
            self._deep_var = tk.BooleanVar(value=True)
            ttk.Checkbutton(
                frm, text="深度清理（推荐）：删除指向该环境的注册表键和开始菜单快捷方式\n"
                          "　　· 注册表键删除前自动备份，可随时还原\n"
                          "　　· 只清理精确指向此环境目录的条目，绝不误伤其他环境",
                variable=self._deep_var).pack(anchor="w", pady=(12, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(20, 0))
        ok = tk.Button(btns, text="确认删除", command=self._ok,
                       bg="#c62828", fg="white", activebackground="#b71c1c",
                       activeforeground="white", relief="flat",
                       padx=18, pady=5, cursor="hand2",
                       font=("Microsoft YaHei UI", 11, "bold"))
        ok.pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=(0, 12))

        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        x = master.winfo_rootx() + max((master.winfo_width() - self.winfo_width()) // 2, 0)
        y = master.winfo_rooty() + max((master.winfo_height() - self.winfo_height()) // 2, 0)
        self.geometry(f"+{x}+{y}")
        ok.focus_set()
        self.wait_window()

    def _ok(self):
        self.result = True
        self.deep_clean = bool(getattr(self, "_deep_var", None) and
                               self._deep_var.get())
        self.destroy()


class PackageWindow(tk.Toplevel):
    """单个环境内的包列表：名称 / 版本 / 占用大小，支持搜索、排序、pip 卸载"""

    def __init__(self, master, app, env: scanner.PythonEnv):
        super().__init__(master)
        self.app = app
        self.env = env
        self.pkgs: list[dict] = []
        self.q: queue.Queue = queue.Queue()
        self.title(f"包管理 — {env.name}")
        self.geometry(f"{int(900 * app.scale)}x{int(640 * app.scale)}")
        self.minsize(int(720 * app.scale), int(460 * app.scale))

        # 顶部：环境信息 + 三个大数字
        head = ttk.Frame(self, padding=(14, 12, 14, 4))
        head.pack(fill="x")
        ttk.Label(head, text=env.name, font=FONT_NAME).pack(anchor="w")
        ttk.Label(head, text=env.root, font=FONT_SMALL,
                  foreground="#666666").pack(anchor="w")

        cards = ttk.Frame(self, padding=(14, 6, 14, 6))
        cards.pack(fill="x")
        self.card_total = self._card(cards, "环境总大小")
        ttk.Separator(cards, orient="vertical").pack(side="left", fill="y", padx=18)
        self.card_pkg = self._card(cards, "包总大小（site-packages）")
        ttk.Separator(cards, orient="vertical").pack(side="left", fill="y", padx=18)
        self.card_count = self._card(cards, "包数量")

        ttk.Separator(self).pack(fill="x", padx=14, pady=(2, 0))

        # 搜索行
        searchbar = ttk.Frame(self, padding=(14, 8, 14, 0))
        searchbar.pack(fill="x")
        ttk.Label(searchbar, text="搜索:", font=FONT).pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        search = ttk.Entry(searchbar, textvariable=self.search_var, width=28,
                           font=FONT)
        search.pack(side="left")
        search.bind("<KeyRelease>", lambda e: self._refresh_rows())
        self.uninstall_btn = ttk.Button(searchbar, text="🗑 卸载所选包",
                                        command=self._uninstall)
        self.uninstall_btn.pack(side="right")
        self.uninstall_btn.config(state="disabled")

        # 包列表
        mid = ttk.Frame(self, padding=(14, 6, 14, 0))
        mid.pack(fill="both", expand=True)
        cols = ("name", "version", "size")
        heads = {"name": "包名", "version": "版本", "size": "大小"}
        widths = {"name": 320, "version": 130, "size": 230}
        self.tree = ttk.Treeview(mid, columns=cols, show="headings")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        for c in cols:
            self.tree.heading(c, text=heads[c],
                              command=lambda cc=c: self._sort(cc))
            self.tree.column(c, width=int(widths[c] * app.scale), anchor="w")
        self.sort_col = "size"
        self.sort_desc = True
        self.tree.bind("<<TreeviewSelect>>",
                       lambda e: self.uninstall_btn.config(
                           state="normal" if self.tree.selection() else "disabled"))

        # 底部操作
        bottom = ttk.Frame(self, padding=14)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="📁 打开 site-packages",
                   command=self._open_site).pack(side="left")
        ttk.Button(bottom, text="🔄 刷新", command=self._load).pack(side="left", padx=10)
        self.status_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.status_var,
                  font=FONT, foreground="#555555").pack(side="left", padx=14)

        self.protocol("WM_DELETE_WINDOW", self._close)
        self._load()
        self._poll()
        self.update_idletasks()
        x = master.winfo_rootx() + max((master.winfo_width() - self.winfo_width()) // 2, 0)
        y = master.winfo_rooty() + max((master.winfo_height() - self.winfo_height()) // 2, 0)
        self.geometry(f"+{x}+{y}")

    def _card(self, parent, caption):
        """大数字卡片，返回取值 Label"""
        box = ttk.Frame(parent)
        box.pack(side="left")
        val = ttk.Label(box, text="—", font=FONT_NUM)
        val.pack(anchor="w")
        ttk.Label(box, text=caption, font=FONT_SMALL,
                  foreground="#666666").pack(anchor="w")
        return val

    # ---------- 数据加载 ----------
    def _load(self):
        self.status_var.set("正在读取包列表...")
        env = self.env
        def work():
            if env.site_packages and os.path.isdir(env.site_packages):
                pk_size, index = sizes.dir_size_index(env.site_packages)
                pkgs = pkgmod.list_packages(env.site_packages, file_index=index)
            else:
                pk_size, pkgs = 0, []
            self.q.put(("loaded", pkgs, pk_size))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "loaded":
                    self.pkgs = item[1]
                    pk = item[2]
                    self.env.packages_size = pk
                    self.env.packages_count = len(self.pkgs)
                    self.app.on_packages_refreshed(self.env)
                    self._refresh_rows()
                    self.status_var.set("")
                    self._update_summary()
                elif item[0] == "uninstalled":
                    ok, out = item[1], item[2]
                    self.uninstall_btn.config(state="normal")
                    if ok:
                        messagebox.showinfo("卸载完成", "包已成功卸载。", parent=self)
                    else:
                        messagebox.showerror("卸载失败", out or "未知错误", parent=self)
                    self._load()
        except queue.Empty:
            pass
        except tk.TclError:
            return  # 窗口已销毁
        try:
            if self.winfo_exists():
                self.after(80, self._poll)
        except tk.TclError:
            pass

    def _update_summary(self):
        env = self.env
        self.card_total.config(text=sizes.format_size(env.total_size))
        self.card_pkg.config(text=sizes.format_size(env.packages_size))
        self.card_count.config(text=str(len(self.pkgs)) if self.pkgs else "—")

    def _visible_pkgs(self):
        ft = self.search_var.get().strip().lower()
        if not ft:
            return self.pkgs
        return [p for p in self.pkgs if ft in p["name"].lower()
                or ft in p["version"].lower()]

    def _refresh_rows(self):
        self.tree.delete(*self.tree.get_children())
        max_size = max((p["size"] for p in self.pkgs if p["size"] > 0), default=0)
        for p in self._visible_pkgs():
            bar = size_bar(p["size"], max_size, width=8)
            size_txt = f"{bar}  {p['size_text']}" if bar else p["size_text"]
            self.tree.insert("", "end",
                             values=(p["name"], p["version"], size_txt))

    def _sort(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, (col == "size")
        if col == "size":
            self.pkgs.sort(key=lambda p: (p["size"] < 0,
                                          -p["size"] if p["size"] >= 0 else 0),
                           reverse=self.sort_desc)
        else:
            key = col if col in ("name", "version") else "name"
            self.pkgs.sort(key=lambda p: p[key].lower(), reverse=self.sort_desc)
        self._refresh_rows()

    # ---------- 操作 ----------
    def _selected_pkg(self):
        sel = self.tree.selection()
        if not sel:
            return None
        vals = self.tree.item(sel[0], "values")
        name = vals[0]
        for p in self.pkgs:
            if p["name"] == name:
                return p
        return None

    def _uninstall(self):
        pkg = self._selected_pkg()
        if not pkg:
            return
        if not messagebox.askyesno("卸载确认",
                                   f"确定要用 pip 卸载 {pkg['name']} {pkg['version']} 吗？",
                                   parent=self):
            return
        self.uninstall_btn.config(state="disabled")
        self.status_var.set(f"正在卸载 {pkg['name']} ...")
        env = self.env
        name = pkg["name"]
        def work():
            ok, out = remover.uninstall_package(env.python_path, name)
            self.q.put(("uninstalled", ok, out))
        threading.Thread(target=work, daemon=True).start()

    def _open_site(self):
        sp = self.env.site_packages
        if sp and os.path.isdir(sp):
            os.startfile(sp)
        else:
            messagebox.showinfo("提示", "该环境没有 site-packages 目录。", parent=self)

    def _close(self):
        self.destroy()


def _enable_dpi_awareness():
    """必须在创建 Tk 窗口之前调用，否则窗口会被系统拉伸而模糊"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class CleanDialog(tk.Toplevel):
    """缓存清理中心：pip 下载缓存与 Conda 包缓存"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.items = []          # (路径, 勾选变量, 大小标签)
        self.q: queue.Queue = queue.Queue()
        self.title("清理缓存")
        self.geometry(f"{int(760 * app.scale)}x{int(430 * app.scale)}")
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text="清理下载缓存", font=FONT_NAME).pack(
            anchor="w", padx=16, pady=(14, 2))
        ttk.Label(self, text="缓存只是安装时留下的下载副本，清理不影响已安装的环境和包，"
                             "下次安装会重新下载。", style="Summary.TLabel").pack(
            anchor="w", padx=16, pady=(0, 10))

        self.frame = ttk.Frame(self, padding=(16, 0))
        self.frame.pack(fill="both", expand=True)

        pframe = ttk.Frame(self, padding=(16, 8, 16, 0))
        pframe.pack(fill="x")
        self.progress = PixelCatProgress(pframe, width=int(620 * app.scale))
        self.progress.pack(fill="x")

        bottom = ttk.Frame(self, padding=16)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar(value="正在统计缓存大小…")
        ttk.Label(bottom, textvariable=self.status_var,
                  style="Summary.TLabel").pack(side="left")
        self.clean_btn = ttk.Button(bottom, text="🧹 开始清理", command=self._clean)
        self.clean_btn.pack(side="right")
        ttk.Button(bottom, text="关闭", command=self.destroy).pack(
            side="right", padx=(0, 10))

        self._build_items()
        self._poll()
        self.update_idletasks()
        x = master.winfo_rootx() + max((master.winfo_width() - self.winfo_width()) // 2, 0)
        y = master.winfo_rooty() + max((master.winfo_height() - self.winfo_height()) // 2, 0)
        self.geometry(f"+{x}+{y}")

    def _build_items(self):
        targets = []
        pc = cleaner.pip_cache_path()
        if pc:
            targets.append(("pip 下载缓存", pc))
        conda_roots = [e.root for e in self.app.envs if e.env_type == "conda"]
        for p in cleaner.conda_pkgs_paths(conda_roots):
            targets.append((f"Conda 包缓存（{os.path.basename(os.path.dirname(p))}）", p))
        for label, path in targets:
            row = ttk.Frame(self.frame)
            row.pack(fill="x", pady=(6, 2))
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(row, text=f"{label}", variable=var).pack(side="left")
            size_lbl = ttk.Label(row, text="统计中…", font=FONT_BOLD)
            size_lbl.pack(side="right")
            self.items.append((path, label, var, size_lbl))
            ttk.Label(self.frame, text="　　" + path, style="Path.TLabel").pack(
                anchor="w")
            ttk.Separator(self.frame).pack(fill="x", pady=(4, 0))
        if not self.items:
            ttk.Label(self.frame, text="没有发现可清理的缓存。",
                      style="Summary.TLabel").pack(anchor="w", pady=8)
        else:
            threading.Thread(target=self._calc_sizes, daemon=True).start()

    def _calc_sizes(self):
        for path, _label, _var, _lbl in self.items:
            n = sizes.dir_size(path)
            self.q.put(("size", path, n))

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "size":
                    _, path, n = item
                    for p, _l, _v, lbl in self.items:
                        if p == path:
                            lbl.config(text=sizes.format_size(n))
                elif item[0] == "itemstart":
                    _, idx, n, label = item
                    self.progress.set_fraction(idx / n if n else 0)
                    self.status_var.set(f"正在清理 {label}…")
                elif item[0] == "prog":
                    _, idx, n, label, done, entries, freed = item
                    frac = done / entries if entries else 0
                    self.progress.set_fraction((idx + frac) / n if n else 0)
                    self.status_var.set(
                        f"正在清理 {label}… 已处理 {done}/{entries} 项"
                        f"（已释放 {sizes.format_size(freed)}）")
                elif item[0] == "cleaned":
                    _, freed, errs = item
                    self.clean_btn.config(state="normal")
                    self.progress.set_fraction(1.0)
                    self.progress.stop()
                    if errs:
                        messagebox.showwarning(
                            "清理时有部分错误",
                            "以下文件未能删除（可能被占用）：\n" + "\n".join(errs[:5]),
                            parent=self)
                    self.status_var.set(f"清理完成，释放 {sizes.format_size(freed)}")
                    threading.Thread(target=self._calc_sizes, daemon=True).start()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            if self.winfo_exists():
                self.after(80, self._poll)
        except tk.TclError:
            pass

    def _clean(self):
        chosen = [(p, label) for p, label, v, _ in self.items if v.get()]
        if not chosen:
            messagebox.showinfo("提示", "请先勾选要清理的缓存。", parent=self)
            return
        if not messagebox.askyesno(
                "确认清理",
                f"将清空 {len(chosen)} 个缓存目录的内容。\n已安装的环境和包不受影响。确定继续？",
                parent=self):
            return
        self.clean_btn.config(state="disabled")
        self.progress.start()
        self.status_var.set("正在清理…")
        def work():
            total, errs = 0, []
            n = len(chosen)
            for idx, (p, label) in enumerate(chosen):
                self.q.put(("itemstart", idx, n, label))
                def cb(done, entries, freed, _idx=idx, _label=label, _total=total):
                    self.q.put(("prog", _idx, n, _label, done, entries,
                                _total + freed))
                freed, e = cleaner.clean_dir_contents(p, progress=cb)
                total += freed
                errs.extend(e[:3])
            self.q.put(("cleaned", total, errs))
        threading.Thread(target=work, daemon=True).start()


class ModelDialog(tk.Toplevel):
    """检测并管理环境关联的模型文件：官方缓存目录 + 环境内大模型文件"""

    def __init__(self, master, app, env: scanner.PythonEnv):
        super().__init__(master)
        self.app = app
        self.env = env
        self.entries: list[dict] = []
        self.q: queue.Queue = queue.Queue()
        self.title(f"模型文件 — {env.name}")
        self.geometry(f"{int(980 * app.scale)}x{int(560 * app.scale)}")
        self.transient(master)
        self.grab_set()

        ttk.Label(self, text=f"{env.name} 关联的模型文件",
                  font=FONT_NAME).pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(self, text="包括：已安装的 ML 库（transformers / torch / paddle 等）"
                             "的官方模型缓存，以及环境内 ≥50MB 的模型权重文件。",
                  style="Summary.TLabel").pack(anchor="w", padx=14, pady=(0, 4))
        self.summary_var = tk.StringVar(value="正在扫描模型文件…")
        ttk.Label(self, textvariable=self.summary_var,
                  font=FONT_BOLD).pack(anchor="w", padx=14, pady=(0, 6))

        mid = ttk.Frame(self, padding=(14, 0))
        mid.pack(fill="both", expand=True)
        cols = ("group", "size", "path")
        heads = {"group": "类别", "size": "大小", "path": "路径"}
        widths = {"group": 170, "size": 110, "path": 560}
        self.tree = ttk.Treeview(mid, columns=cols, show="headings",
                                 selectmode="extended")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=int(widths[c] * app.scale), anchor="w")

        pframe = ttk.Frame(self, padding=(14, 6, 14, 0))
        pframe.pack(fill="x")
        self.progress = PixelCatProgress(pframe, width=int(620 * app.scale))
        self.progress.pack(fill="x")

        bottom = ttk.Frame(self, padding=14)
        bottom.pack(fill="x")
        self.status_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.status_var,
                  style="Summary.TLabel").pack(side="left")
        self.del_btn = ttk.Button(bottom, text="🗑 删除所选", style="Danger.TButton",
                                  command=self._delete, state="disabled")
        self.del_btn.pack(side="right")
        ttk.Button(bottom, text="🔄 重新扫描", command=self._load).pack(
            side="right", padx=(0, 10))
        ttk.Button(bottom, text="关闭", command=self.destroy).pack(
            side="right", padx=(0, 10))

        self._load()
        self._poll()
        self.update_idletasks()
        x = master.winfo_rootx() + max((master.winfo_width() - self.winfo_width()) // 2, 0)
        y = master.winfo_rooty() + max((master.winfo_height() - self.winfo_height()) // 2, 0)
        self.geometry(f"+{x}+{y}")

    def _load(self):
        self.status_var.set("正在扫描…")
        self.summary_var.set("正在扫描模型文件…")
        self.tree.delete(*self.tree.get_children())
        env = self.env
        def work():
            entries = modelscan.model_entries(env.root, env.site_packages)
            self.q.put(("loaded", entries))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "loaded":
                    self.entries = item[1]
                    self._refresh_rows()
                    total = sum(e["size"] for e in self.entries)
                    self.summary_var.set(
                        f"共 {len(self.entries)} 项 · 合计 {sizes.format_size(total)}"
                        f"　（按住 Ctrl 可多选；缓存类删除后可重新下载）")
                    self.status_var.set("")
                elif item[0] == "prog":
                    _, done, total_n, freed = item
                    self.progress.set_fraction(done / total_n if total_n else 0)
                    self.status_var.set(
                        f"正在删除… {done}/{total_n} 项"
                        f"（已释放 {sizes.format_size(freed)}）")
                elif item[0] == "deleted":
                    _, freed, errs = item
                    self.del_btn.config(state="normal")
                    self.progress.stop()
                    if errs:
                        messagebox.showwarning(
                            "删除时有部分错误",
                            "以下条目未能删除（可能被占用）：\n" + "\n".join(errs[:5]),
                            parent=self)
                    self.status_var.set(f"删除完成，释放 {sizes.format_size(freed)}")
                    self._load()
        except queue.Empty:
            pass
        except tk.TclError:
            return
        try:
            if self.winfo_exists():
                self.after(80, self._poll)
        except tk.TclError:
            pass

    def _refresh_rows(self):
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(self.entries):
            self.tree.insert("", "end", iid=str(i), values=(
                e["group"] + ("（慎删）" if e["kind"] == "envfile" else ""),
                sizes.format_size(e["size"]), e["path"]))
        self.del_btn.config(state="normal" if self.entries else "disabled")

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择要删除的模型条目。", parent=self)
            return
        chosen = [self.entries[int(i[1:])] for i in sel]
        total = sum(e["size"] for e in chosen)
        n_env = sum(1 for e in chosen if e["kind"] == "envfile")
        msg = (f"将永久删除所选 {len(chosen)} 项，合计 {sizes.format_size(total)}。\n"
               "缓存类条目删除后，下次使用时会重新下载。\n")
        if n_env:
            msg += (f"\n⚠ 其中 {n_env} 项位于环境的 site-packages 内，"
                    "删除可能导致对应的库损坏，建议改为卸载整个包！\n")
        msg += "\n确定继续吗？"
        if not messagebox.askyesno("确认删除", msg, parent=self):
            return
        self.del_btn.config(state="disabled")
        self.progress.start()
        def work():
            def cb(done, total_n, freed):
                self.q.put(("prog", done, total_n, freed))
            freed, errs = modelscan.delete_model_entries(chosen, progress=cb)
            self.q.put(("deleted", freed, errs))
        threading.Thread(target=work, daemon=True).start()


class App(tk.Tk):
    def _dpi_scale(self):
        """DPI 感知进程里，像素尺寸需按缩放系数放大"""
        try:
            import ctypes
            return ctypes.windll.user32.GetDpiForSystem() / 96.0
        except Exception:
            return 1.0

    def __init__(self, selftest=False):
        super().__init__()
        self.title("Python 环境检查器")
        self.scale = self._dpi_scale()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(int(sw * 0.8), int(1560 * self.scale))
        h = min(int(sh * 0.82), int(940 * self.scale))
        self.geometry(f"{w}x{h}")
        self.minsize(int(860 * self.scale), int(560 * self.scale))

        self.envs: list[scanner.PythonEnv] = []
        self.env_by_uid = {}
        self.q: queue.Queue = queue.Queue()
        self.gen = 0                    # 扫描代次，用于丢弃过期结果
        self.filter_text = ""
        self.sort_col = "total"
        self.sort_desc = True

        self._setup_style()
        self._build_ui()

        self.report_callback_exception = self._tk_exception
        self._poll()
        if selftest:
            self.after(2500, self.destroy)
        else:
            self.after(200, self.start_scan)

    # ---------- 界面 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("Treeview", rowheight=max(30, int(30 * self.scale)), font=FONT)
        style.configure("Treeview.Heading", font=FONT_BOLD, padding=(4, 6))
        style.configure("Tool.TButton", padding=(14, 7), font=FONT)
        style.configure("Act.TButton", padding=(12, 6), font=FONT)
        style.configure("Danger.TButton", foreground="#c62828", font=FONT_BOLD)
        style.configure("Summary.TLabel", font=("Microsoft YaHei UI", 10),
                        foreground="#555555")
        style.configure("Caption.TLabel", font=FONT_SMALL, foreground="#777777")
        style.configure("Path.TLabel", font=FONT_SMALL, foreground="#555555")

    def _build_ui(self):
        # 顶部工具栏
        bar = ttk.Frame(self, padding=(12, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text="Python 环境检查器",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(side="left")
        ttk.Button(bar, text="🔄 重新扫描", style="Tool.TButton",
                   command=self.start_scan).pack(side="left", padx=(18, 0))
        ttk.Button(bar, text="🧹 清理缓存", style="Tool.TButton",
                   command=lambda: CleanDialog(self, self)).pack(side="left", padx=(8, 0))
        ttk.Label(bar, text="筛选:").pack(side="right", padx=(8, 4))
        self.filter_var = tk.StringVar()
        f = ttk.Entry(bar, textvariable=self.filter_var, width=28, font=FONT)
        f.pack(side="right")
        f.bind("<KeyRelease>", self._on_filter)

        # 汇总行
        self.summary_var = tk.StringVar(value="正在启动…")
        ttk.Label(self, textvariable=self.summary_var, style="Summary.TLabel",
                  padding=(14, 0)).pack(fill="x")

        # 自底向上先声明：状态栏（最底）、详情卡片（其上）、主列表占据其余空间
        status = ttk.Frame(self, padding=(12, 6))
        status.pack(fill="x", side="bottom")
        self.progress = PixelCatProgress(status, width=int(160 * self.scale))
        self.progress.pack(side="left")
        self.status_var = tk.StringVar(value="正在启动…")
        ttk.Label(status, textvariable=self.status_var,
                  font=FONT_SMALL).pack(side="left", padx=(10, 0))

        self._build_detail()

        # 主列表
        frame = ttk.Frame(self, padding=(12, 6, 12, 0))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=COLUMNS, show="headings")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        for c in COLUMNS:
            anchor = "e" if c == "count" else "w"
            self.tree.heading(c, text=HEADERS[c],
                              command=lambda cc=c: self._sort_by(cc))
            self.tree.column(c, width=int(WIDTHS[c] * self.scale), anchor=anchor,
                             stretch=(c == "path"))
        for t, color in TYPE_COLORS.items():
            self.tree.tag_configure(t, foreground=color)
        self.tree.bind("<Double-1>", lambda e: self.on_view_packages())
        self.tree.bind("<Return>", lambda e: self.on_view_packages())
        self.tree.bind("<Delete>", lambda e: self.on_delete())
        self.tree.bind("<Button-3>", self._show_menu)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.menu = tk.Menu(self, tearoff=0, font=FONT)
        self.menu.add_command(label="📦 查看包", command=self.on_view_packages)
        self.menu.add_command(label="📁 打开所在文件夹", command=self.on_open_folder)
        self.menu.add_command(label="🛠 打开官方卸载程序…", command=self.on_official_uninstall)
        self.menu.add_separator()
        self.menu.add_command(label="🗑 删除该环境…", command=self.on_delete)

    def _build_detail(self):
        """选中环境后显示大号信息的详情卡片"""
        self.detail = ttk.Frame(self, padding=(12, 8), relief="groove", borderwidth=1)
        self.detail.pack(fill="x", side="bottom", padx=10, pady=(0, 2))

        info = ttk.Frame(self.detail)
        info.pack(side="left", fill="both", expand=True)
        self.d_name = ttk.Label(info, text="在上方列表中选择一个环境",
                                font=FONT_NAME)
        self.d_name.pack(anchor="w")
        self.d_meta = ttk.Label(info, text="", style="Summary.TLabel")
        self.d_meta.pack(anchor="w", pady=(2, 0))
        self.d_path = ttk.Label(info, text="", style="Path.TLabel")
        self.d_path.pack(anchor="w", pady=(2, 0))

        # 右侧三个大数字
        stats = ttk.Frame(self.detail)
        stats.pack(side="right", padx=(20, 4))
        self.d_total = self._stat(stats, "环境总大小")
        ttk.Separator(stats, orient="vertical").pack(side="left", fill="y", padx=16)
        self.d_pkg = self._stat(stats, "包大小（site-packages）")
        ttk.Separator(stats, orient="vertical").pack(side="left", fill="y", padx=16)
        self.d_count = self._stat(stats, "包数量")

        # 操作按钮（右侧竖排会太高，横排放中间偏右）
        btns = ttk.Frame(self.detail)
        btns.pack(side="right", padx=16)
        self.btn_pkg = ttk.Button(btns, text="📦 查看包", style="Act.TButton",
                                  command=self.on_view_packages, state="disabled")
        self.btn_pkg.grid(row=0, column=0, padx=4, pady=2, sticky="ew")
        self.btn_open = ttk.Button(btns, text="📁 打开文件夹", style="Act.TButton",
                                   command=self.on_open_folder, state="disabled")
        self.btn_open.grid(row=1, column=0, padx=4, pady=2, sticky="ew")
        self.btn_del = ttk.Button(btns, text="🗑 删除环境", style="Danger.TButton",
                                  command=self.on_delete, state="disabled")
        self.btn_del.grid(row=2, column=0, padx=4, pady=2, sticky="ew")
        self.btn_model = ttk.Button(btns, text="🤖 模型文件", style="Act.TButton",
                                    command=self.on_model_files, state="disabled")
        self.btn_model.grid(row=3, column=0, padx=4, pady=2, sticky="ew")
        btns.columnconfigure(0, weight=1)

    def _stat(self, parent, caption):
        box = ttk.Frame(parent)
        box.pack(side="left")
        val = ttk.Label(box, text="—", font=FONT_NUM)
        val.pack(anchor="w")
        ttk.Label(box, text=caption, style="Caption.TLabel").pack(anchor="w")
        return val

    # ---------- 工具 ----------
    def _tk_exception(self, exc_type, exc_value, tb):
        _log("界面回调异常：\n" + "".join(traceback.format_exception(exc_type, exc_value, tb)))

    def _maxes(self):
        total = max((e.total_size for e in self.envs if e.total_size > 0), default=0)
        pkg = max((e.packages_size for e in self.envs if e.packages_size > 0), default=0)
        return total, pkg

    def _visible_envs(self):
        ft = self.filter_text
        if not ft:
            return self.envs
        return [e for e in self.envs
                if ft in e.name.lower() or ft in e.root.lower()
                or ft in e.version.lower() or ft in e.type_label.lower()]

    def _row_values(self, env: scanner.PythonEnv):
        max_total, max_pkg = self._maxes()
        ver = env.version + ("（损坏）" if env.broken else "")
        total_txt = sizes.format_size(env.total_size)
        bar_t = size_bar(env.total_size, max_total)
        if bar_t:
            total_txt = f"{bar_t}  {total_txt}"
        pkg_txt = sizes.format_size(env.packages_size)
        bar_p = size_bar(env.packages_size, max_pkg, width=8)
        if bar_p:
            pkg_txt = f"{bar_p}  {pkg_txt}"
        return (env.name, ver, env.type_label, env.root,
                total_txt, pkg_txt,
                env.packages_count if env.packages_count >= 0 else "—")

    def _rebuild_tree(self):
        selected = self.tree.selection()
        sel_uid = selected[0] if selected else None
        self.tree.delete(*self.tree.get_children())
        for env in self._visible_envs():
            tag = "broken" if env.broken else env.env_type
            self.tree.insert("", "end", iid=env.uid,
                             values=self._row_values(env), tags=(tag,))
        if sel_uid and self.tree.exists(sel_uid):
            self.tree.selection_set(sel_uid)
            self.tree.see(sel_uid)

    def _update_headers(self):
        for c in COLUMNS:
            mark = " ▼" if (c == self.sort_col and self.sort_desc) \
                else (" ▲" if c == self.sort_col else "")
            self.tree.heading(c, text=HEADERS[c] + mark)

    def _sort_by(self, col):
        if self.sort_col == col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, (col in ("total", "pkg", "count"))
        self.envs.sort(key=SORT_KEYS[col], reverse=self.sort_desc)
        self._rebuild_tree()
        self._update_headers()

    def _on_filter(self, _=None):
        self.filter_text = self.filter_var.get().strip().lower()
        self._rebuild_tree()

    def selected_env(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.env_by_uid.get(sel[0])

    def _on_select(self, _=None):
        env = self.selected_env()
        has = env is not None
        for b in (self.btn_pkg, self.btn_open, self.btn_del, self.btn_model):
            b.config(state="normal" if has else "disabled")
        if not has:
            self.d_name.config(text="在上方列表中选择一个环境")
            self.d_meta.config(text="")
            self.d_path.config(text="")
            for w in (self.d_total, self.d_pkg, self.d_count):
                w.config(text="—")
            return
        ver = f"Python {env.version}" + ("（损坏，解释器无法运行）" if env.broken else "")
        self.d_name.config(text=env.name)
        self.d_meta.config(text=f"{env.type_label} · {ver}")
        self.d_path.config(text=env.root)
        self.d_total.config(text=sizes.format_size(env.total_size))
        self.d_pkg.config(text=sizes.format_size(env.packages_size))
        self.d_count.config(text=str(env.packages_count)
                            if env.packages_count >= 0 else "—")

    def _update_summary(self):
        total = sum(e.total_size for e in self.envs if e.total_size > 0)
        n = len(self.envs)
        self.summary_var.set(
            f"共发现 {n} 个环境 · 已统计合计占用 {sizes.format_size(total)}"
            f"　（提示：双击环境可查看包列表，点击列标题排序）")

    def _progress_stop(self):
        """停止像素猫跑动并隐藏（替代旧的绿色滚动条清理逻辑）"""
        self.progress.stop()

    # ---------- 扫描 ----------
    def start_scan(self):
        self.gen += 1
        self.envs = []
        self.env_by_uid = {}
        self.tree.delete(*self.tree.get_children())
        self.progress.start()
        self.summary_var.set("正在扫描，请稍候…")
        threading.Thread(target=self._scan_worker, args=(self.gen,), daemon=True).start()

    def _scan_worker(self, gen):
        try:
            envs = scanner.scan_all(
                progress=lambda t: self.q.put(("status", t, gen)))
        except Exception as e:
            self.q.put(("scan_error", str(e), gen))
            return
        for i, env in enumerate(envs):
            env.uid = f"e{i}"
            self.q.put(("env_added", env, gen))
        with ThreadPoolExecutor(max_workers=5) as ex:
            list(ex.map(self._size_one, envs))
        self.q.put(("scan_done", gen))

    def _size_one(self, env: scanner.PythonEnv):
        try:
            total = sizes.dir_size(env.root) if env.root and os.path.isdir(env.root) else 0
            pk = sizes.dir_size(env.site_packages) \
                if env.site_packages and os.path.isdir(env.site_packages) else 0
            cnt = pkgmod.count_packages(env.site_packages)
            self.q.put(("size", env, total, pk, cnt))
        except Exception as e:
            self.q.put(("size_error", env, str(e)))

    def on_packages_refreshed(self, env: scanner.PythonEnv):
        """包窗口刷新后，同步主列表中该环境的包大小/数量"""
        self.q.put(("size", env, env.total_size, env.packages_size, env.packages_count))

    # ---------- 消息轮询 ----------
    def _poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                self._dispatch(item)
        except queue.Empty:
            pass
        except tk.TclError:
            return  # 窗口已销毁
        try:
            self.after(80, self._poll)
        except tk.TclError:
            pass

    def _dispatch(self, item):
        kind = item[0]
        if kind == "status":
            _, text, gen = item
            if gen == self.gen:
                self.status_var.set(text)
        elif kind == "env_added":
            _, env, gen = item
            if gen != self.gen:
                return
            self.envs.append(env)
            self.env_by_uid[env.uid] = env
            if env.uid not in self.tree.get_children(""):
                tag = "broken" if env.broken else env.env_type
                self.tree.insert("", "end", iid=env.uid,
                                 values=self._row_values(env), tags=(tag,))
            self._update_summary()
        elif kind == "size":
            _, env, total, pk, cnt = item
            first = env.total_size < 0
            env.total_size, env.packages_size, env.packages_count = total, pk, cnt
            if self.tree.exists(env.uid):
                self.tree.item(env.uid, values=self._row_values(env))
            if first:
                self._update_summary()
                self._on_select()   # 详情卡片跟随刷新
        elif kind == "size_error":
            _, env, err = item
            env.total_size = env.packages_size = env.packages_count = -2
            _log(f"统计大小失败 {env.root}: {err}")
        elif kind == "scan_done":
            self._progress_stop()
            self.status_var.set("扫描完成")
            self.envs.sort(key=SORT_KEYS[self.sort_col], reverse=self.sort_desc)
            self._rebuild_tree()
            self._update_headers()
            self._update_summary()
            # 默认选中列表第一项（占用最大），详情卡片立即有内容
            kids = self.tree.get_children("")
            if kids and not self.tree.selection():
                self.tree.selection_set(kids[0])
        elif kind == "scan_error":
            _, err, gen = item
            if gen == self.gen:
                self._progress_stop()
                self.status_var.set(f"扫描失败：{err}")
        elif kind == "uninstaller_found":
            _, env, uninstall_string = item
            self.status_var.set("")
            self._launch_uninstaller(env, uninstall_string)
        elif kind == "delprog":
            self.status_var.set(f"正在删除… 已处理 {item[1]} 个文件")
        elif kind == "deleted":
            _, env, errors, cleaned = item
            self._progress_stop()
            if env in self.envs:
                self.envs.remove(env)
                self.env_by_uid.pop(env.uid, None)
                if self.tree.exists(env.uid):
                    self.tree.delete(env.uid)
            if errors:
                self.status_var.set("删除未完成（存在部分错误）")
                messagebox.showwarning(
                    "删除时出现部分错误",
                    "环境目录未能完全删除，请检查以下原因：\n\n" +
                    "\n".join(errors[:5]) +
                    "\n\n常见原因：需要管理员权限（Program Files）、文件被占用。\n"
                    "未执行深度清理，删除完整后可重新尝试。")
            else:
                extra = ""
                if cleaned:
                    parts = []
                    if cleaned.get("reg_deleted"):
                        parts.append(f"注册表键 {cleaned['reg_deleted']} 项")
                    if cleaned.get("shortcuts"):
                        parts.append(f"开始菜单快捷方式 {cleaned['shortcuts']} 个")
                    if cleaned.get("envtxt"):
                        parts.append(f"environments.txt 死条目 {cleaned['envtxt']} 行")
                    if parts:
                        extra = "\n\n同时已清理：" + "、".join(parts)
                        if cleaned.get("backup_dir"):
                            extra += f"\n注册表已备份到：{cleaned['backup_dir']}"
                    notes = [m for m in cleaned.get("messages", [])
                             if not m.startswith("已删除")]
                    if notes:
                        extra += "\n\n" + "\n".join(notes[:4])
                messagebox.showinfo("删除完成", f"环境 {env.name} 已成功删除。{extra}")
                self.status_var.set("删除完成")
            self._update_summary()
            self._on_select()

    # ---------- 操作 ----------
    def _show_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.env_by_uid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        env = self.env_by_uid[iid]
        self.menu.entryconfig(
            2, state="normal" if env.env_type == "official" else "disabled")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def on_view_packages(self):
        env = self.selected_env()
        if not env:
            messagebox.showinfo("提示", "请先在列表中选择一个环境。")
            return
        PackageWindow(self, self, env)

    def on_model_files(self):
        env = self.selected_env()
        if not env:
            messagebox.showinfo("提示", "请先在列表中选择一个环境。")
            return
        ModelDialog(self, self, env)

    def on_open_folder(self):
        env = self.selected_env()
        if not env:
            messagebox.showinfo("提示", "请先在列表中选择一个环境。")
            return
        if env.root and os.path.isdir(env.root):
            os.startfile(env.root)
        else:
            messagebox.showwarning("提示", "该环境的目录已不存在。")

    def on_official_uninstall(self):
        env = self.selected_env()
        if not env:
            return
        self.status_var.set("正在查找官方卸载程序…")
        def work():
            us = remover.find_uninstaller(env.root)
            self.q.put(("uninstaller_found", env, us))
        threading.Thread(target=work, daemon=True).start()

    def _launch_uninstaller(self, env, uninstall_string):
        if not uninstall_string:
            messagebox.showinfo(
                "未找到卸载程序",
                "注册表中没有该环境的官方卸载信息。\n"
                "你可以直接删除目录，或到 系统设置 → 应用 中卸载。")
            return
        if not messagebox.askyesno(
                "打开官方卸载程序",
                f"将启动官方卸载程序：\n{uninstall_string}\n\n是否继续？"):
            return
        try:
            subprocess.Popen(uninstall_string)
        except Exception as e:
            messagebox.showerror("启动失败", f"无法启动卸载程序：{e}")

    def on_delete(self):
        env = self.selected_env()
        if not env:
            messagebox.showinfo("提示", "请先在列表中选择一个环境。")
            return

        # 保护：不允许删除正在运行本程序的解释器
        if env.python_path and os.path.isfile(sys.executable):
            try:
                if os.path.normcase(os.path.realpath(env.python_path)) == \
                        os.path.normcase(os.path.realpath(sys.executable)):
                    messagebox.showerror(
                        "无法删除",
                        "该环境正是当前运行本程序所使用的解释器。\n"
                        "请先用其他 Python 重新启动本程序，再删除它。")
                    return
            except OSError:
                pass

        if env.env_type == "store":
            if messagebox.askyesno(
                    "商店版 Python",
                    "商店版应用不能直接删文件夹，需通过系统设置卸载。\n"
                    "是否打开 Windows 设置的“应用”页面？"):
                os.startfile("ms-settings:appsfeatures")
            return

        tip = ""
        if env.env_type == "official":
            tip = ("\n\n提示：这是通过安装程序安装的 Python，"
                   "推荐右键使用“打开官方卸载程序”，直接删目录会留下注册表残留。")
        if env.env_type == "conda":
            tip = ("\n\n提示：Conda 主环境目录较大（含包缓存 pkgs），删除后"
                   "其他 Conda 环境一般不受影响，但建议用 conda 命令管理。")

        dlg = ConfirmDialog(self, env)
        if not dlg.result:
            return
        if env.env_type == "official" and not messagebox.askyesno(
                "再次确认", "再次提醒：直接删除官方安装的 Python 可能留下残留，确定继续吗？"):
            return

        self.progress.start()
        self.status_var.set("正在删除…")
        threading.Thread(target=self._delete_worker,
                         args=(env, bool(getattr(dlg, "deep_clean", False))),
                         daemon=True).start()

    def _delete_worker(self, env: scanner.PythonEnv, deep: bool):
        errors = remover.robust_rmtree(
            env.root,
            progress=lambda n: self.q.put(("delprog", n)))
        cleaned = None
        if not os.path.isdir(env.root):
            # 目录已确认删除，才清理系统残留（保证不会清理仍在使用的环境）
            if deep:
                cleaned = cleaner.deep_clean(env.root)
            else:
                cleaned = {"reg_found": 0, "reg_deleted": 0, "shortcuts": 0,
                           "envtxt": cleaner.clean_environments_txt(),
                           "backup_dir": "", "messages": []}
        self.q.put(("deleted", env, errors, cleaned))


def run_selftest():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # windowed exe 无 stdout
    envs = scanner.scan_all(progress=lambda t: print("[扫描]", t))
    print(f"\n共发现 {len(envs)} 个 Python 环境：")
    for e in envs:
        flag = "  [损坏]" if e.broken else ""
        print(f"  {e.name:<26} {e.version:<12} {e.type_label:<10} {e.root}{flag}")
    for e in envs[:2]:
        if e.root and os.path.isdir(e.root):
            e.total_size = sizes.dir_size(e.root)
            print(f"\n[{e.name}] 环境总大小: {sizes.format_size(e.total_size)}")
        if e.site_packages and os.path.isdir(e.site_packages):
            e.packages_size = sizes.dir_size(e.site_packages)
            pkgs = pkgmod.list_packages(e.site_packages)
            print(f"[{e.name}] 包大小: {sizes.format_size(e.packages_size)}"
                  f"  包数量: {len(pkgs)}")
            for p in pkgs[:5]:
                print(f"    {p['name']:<26} {p['version']:<12} {p['size_text']}")
    print("\n命令行自检完成，接下来进行 2.5 秒的界面自检…")
    App(selftest=True).mainloop()
    print("界面自检通过（窗口正常创建并退出）。")


def main():
    if "--selftest" in sys.argv:
        _enable_dpi_awareness()
        run_selftest()
        return
    if sys.platform != "win32":
        print("本工具仅支持 Windows。")
        return
    _enable_dpi_awareness()
    try:
        App().mainloop()
    except Exception:
        traceback.print_exc()
        try:
            messagebox.showerror("Python 环境检查器", traceback.format_exc()[-1500:])
        except Exception:
            pass


if __name__ == "__main__":
    main()
