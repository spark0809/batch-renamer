# -*- coding: utf-8 -*-
"""
批量重命名工具 (BatchRenamer)
功能：批量添加前缀/后缀、删除指定文本、替换文本、自动编号、修改扩展名、撤销操作。
支持把文件夹拖入窗口加载。
依赖：仅 Python 标准库 + tkinterdnd2（仅一个第三方库，用于支持拖拽）。
"""

import json
import os
import re
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinterdnd2 import DND_FILES, TkinterDnD

# Windows 文件名非法字符
ILLEGAL_CHARS = set('\\/:*?"<>|')

HISTORY_FILE_NAME = "rename_history.json"


# ---------------------------------------------------------------------------
# 核心逻辑（与界面无关，可独立测试）
# ---------------------------------------------------------------------------

def split_name(filename):
    """拆分文件名为主体和扩展名。无扩展名时 ext 为空串。"""
    stem, ext = os.path.splitext(filename)
    return stem, ext


def build_new_name(filename, rules, index):
    """
    按规则生成新文件名。规则生效顺序固定为：
    删除文本 -> 替换文本 -> 添加前后缀 -> 自动编号 -> 修改扩展名
    :param filename: 原文件名（不含路径）
    :param rules: 规则字典
    :param index: 文件在列表中的序号（从 0 开始），用于自动编号
    :return: 新文件名
    """
    stem, ext = split_name(filename)

    # 1. 删除文本（删除所有出现）
    if rules.get("delete_enabled") and rules.get("delete_text"):
        stem = stem.replace(rules["delete_text"], "")

    # 2. 替换文本
    if rules.get("replace_enabled") and rules.get("replace_from"):
        stem = stem.replace(rules["replace_from"], rules.get("replace_to", ""))

    # 3. 添加前缀 / 后缀（后缀加在扩展名之前）
    if rules.get("affix_enabled"):
        stem = rules.get("prefix", "") + stem + rules.get("suffix", "")

    # 4. 自动编号
    if rules.get("number_enabled"):
        try:
            start = int(rules.get("number_start", "1"))
        except ValueError:
            start = 1
        try:
            digits = max(1, int(rules.get("number_digits", "3")))
        except ValueError:
            digits = 3
        num = str(start + index).zfill(digits)
        sep = rules.get("number_sep", "-")
        if rules.get("number_position", "prefix") == "prefix":
            stem = num + sep + stem
        else:
            stem = stem + sep + num

    # 5. 修改扩展名
    if rules.get("ext_enabled"):
        mode = rules.get("ext_mode", "keep")
        if mode == "lower":
            ext = ext.lower()
        elif mode == "upper":
            ext = ext.upper()
        elif mode == "custom":
            custom = rules.get("ext_custom", "").strip()
            if custom and not custom.startswith("."):
                custom = "." + custom
            ext = custom

    return stem + ext


def check_name_valid(new_name):
    """返回 None 表示合法，否则返回错误原因。"""
    if not new_name or new_name in (".", ".."):
        return "名称为空"
    stem, _ = split_name(new_name)
    if not stem:
        return "主体名称为空"
    bad = ILLEGAL_CHARS.intersection(new_name)
    if bad:
        return "含非法字符 " + " ".join(sorted(bad))
    return None


def find_conflicts(pairs, existing_names):
    """
    检测重名冲突。
    :param pairs: [(old_name, new_name), ...]
    :param existing_names: 目录下全部文件名集合
    :return: {new_name: 原因} 冲突表
    """
    conflicts = {}
    seen = {}
    for old, new in pairs:
        if new in seen and seen[new] != old:
            conflicts[new] = "列表内重名"
        seen[new] = old
    # 目标名已存在即视为冲突（包括目标是另一个待改名文件的情况），
    # 宁可拦下也不能覆盖已有文件
    for old, new in pairs:
        if new != old and new in existing_names:
            conflicts.setdefault(new, "目标文件已存在")
    return conflicts


# ---------------------------------------------------------------------------
# 撤销历史（持久化到 JSON 文件）
# ---------------------------------------------------------------------------

class HistoryManager:
    """按批次记录重命名操作，支持逐级撤销。"""

    def __init__(self, path):
        self.path = path
        self.batches = self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
        return []

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.batches, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def push(self, pairs, folder):
        """记录一批操作，pairs 为 [(old_abs, new_abs), ...]"""
        self.batches.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "folder": folder,
            "items": [{"old": o, "new": n} for o, n in pairs],
        })
        self._save()

    def pop(self):
        if not self.batches:
            return None
        batch = self.batches.pop()
        self._save()
        return batch

    def count(self):
        return len(self.batches)

    def last_desc(self):
        if not self.batches:
            return ""
        b = self.batches[-1]
        return "%s（%d 个文件）" % (b["time"], len(b["items"]))


def undo_batch(batch):
    """
    回退一批重命名：把 new 改回 old。
    :return: (成功数, [(失败项, 原因), ...])
    """
    ok = 0
    failed = []
    for item in batch["items"]:
        old, new = item["old"], item["new"]
        if not os.path.exists(new):
            failed.append((new, "文件不存在（可能已被移动或改名）"))
            continue
        if os.path.exists(old):
            failed.append((new, "原文件名已被占用"))
            continue
        try:
            os.rename(new, old)
            ok += 1
        except OSError as e:
            failed.append((new, str(e)))
    return ok, failed


# ---------------------------------------------------------------------------
# 图形界面
# ---------------------------------------------------------------------------

def app_dir():
    """历史记录存放目录：exe 旁边或脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


class RenamerApp(TkinterDnD.Tk):

    def __init__(self):
        super().__init__()
        self.title("批量重命名工具")
        self.geometry("1020x760")
        self.minsize(900, 680)

        self.folder = tk.StringVar()
        self.recursive = tk.BooleanVar(value=False)
        self.ext_filter = tk.StringVar()

        # 规则开关与参数
        self.delete_enabled = tk.BooleanVar(value=False)
        self.delete_text = tk.StringVar()
        self.replace_enabled = tk.BooleanVar(value=False)
        self.replace_from = tk.StringVar()
        self.replace_to = tk.StringVar()
        self.affix_enabled = tk.BooleanVar(value=False)
        self.prefix = tk.StringVar()
        self.suffix = tk.StringVar()
        self.number_enabled = tk.BooleanVar(value=False)
        self.number_start = tk.StringVar(value="1")
        self.number_digits = tk.StringVar(value="3")
        self.number_position = tk.StringVar(value="prefix")
        self.number_sep = tk.StringVar(value="-")
        self.ext_enabled = tk.BooleanVar(value=False)
        self.ext_mode = tk.StringVar(value="lower")
        self.ext_custom = tk.StringVar()

        self.files = []          # 文件名列表（有序，编号按此顺序）
        self.preview_rows = []   # (old, new, status)

        self.history = HistoryManager(os.path.join(app_dir(), HISTORY_FILE_NAME))

        self._build_ui()
        self._bind_traces()
        self._update_undo_button()

    # ---------------- 界面搭建 ----------------

    def _build_ui(self):
        # ============ 顶部工具栏：醒目的执行按钮 + 文件夹选择 ============
        top = ttk.Frame(self, padding=8)
        top.pack(fill=tk.X)

        # 大号绿色执行按钮（最显眼位置：顶部最左）
        self.run_btn = tk.Button(
            top, text="▶  执行重命名  (Ctrl+Enter)", command=self._execute,
            bg="#2ecc71", fg="white",
            activebackground="#27ae60", activeforeground="white",
            font=("Microsoft YaHei", 12, "bold"),
            relief="raised", bd=3, padx=24, pady=10, cursor="hand2",
        )
        self.run_btn.pack(side=tk.LEFT, padx=(0, 12))

        # 撤销按钮（小一些）
        self.undo_btn = ttk.Button(top, text="↺ 撤销上一次", command=self._undo)
        self.undo_btn.pack(side=tk.LEFT, padx=(0, 8))

        # 刷新按钮
        ttk.Button(top, text="刷新列表", command=self._load_files).pack(side=tk.LEFT)

        # 第二行：文件夹选择 + 过滤
        folder_bar = ttk.Frame(self, padding=(8, 0, 8, 4))
        folder_bar.pack(fill=tk.X)
        ttk.Label(folder_bar, text="目标文件夹:").pack(side=tk.LEFT)
        self.folder_entry = ttk.Entry(folder_bar, textvariable=self.folder)
        self.folder_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ttk.Button(folder_bar, text="浏览…", command=self._browse).pack(side=tk.LEFT)
        ttk.Checkbutton(folder_bar, text="包含子文件夹", variable=self.recursive,
                        command=self._load_files).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(folder_bar, text="  扩展名过滤:").pack(side=tk.LEFT, padx=(8, 0))
        flt = ttk.Entry(folder_bar, textvariable=self.ext_filter, width=14)
        flt.pack(side=tk.LEFT)
        flt.bind("<Return>", lambda e: self._load_files())
        ttk.Label(folder_bar, text="(如 jpg;png，空为全部)").pack(side=tk.LEFT)
        ttk.Button(folder_bar, text="加载文件", command=self._load_files).pack(side=tk.LEFT, padx=6)

        # 绑定快捷键 Ctrl+Enter
        self.bind("<Control-Return>", lambda e: self._execute())

        # 拖拽提示（覆盖在文件夹输入框上的可拖拽区域）
        self.drop_hint = tk.Label(
            self, text="📂 把文件夹拖到此处即可加载\n（也可拖到下方任意位置）",
            bg="#eaf4ff", fg="#3a7bd5",
            font=("Microsoft YaHei", 11, "bold"),
            relief="ridge", borderwidth=2, padx=20, pady=8,
        )
        self.drop_hint.place(relx=0.5, y=12, anchor="n")
        self.drop_hint.lower()  # 默认隐藏，仅在拖拽悬浮时显示

        # 中部：左侧规则 + 右侧预览
        mid = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        rules_box = ttk.Frame(mid, width=330)
        mid.add(rules_box, weight=0)
        right = ttk.Frame(mid)
        mid.add(right, weight=1)

        self._build_rules(rules_box)

        # 预览列表
        cols = ("old", "new", "status")
        self.tree = ttk.Treeview(right, columns=cols, show="headings")
        self.tree.heading("old", text="原文件名")
        self.tree.heading("new", text="新文件名（预览）")
        self.tree.heading("status", text="状态")
        self.tree.column("old", width=300)
        self.tree.column("new", width=300)
        self.tree.column("status", width=110, anchor=tk.CENTER)
        sb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("conflict", foreground="#c0392b")
        self.tree.tag_configure("invalid", foreground="#c0392b")
        self.tree.tag_configure("unchanged", foreground="#999999")

        # 底部：操作区（带浅绿色背景，醒目提示）
        bottom = tk.Frame(self, bg="#e8f8f0", bd=1, relief="solid", highlightbackground="#2ecc71", highlightthickness=1)
        bottom.pack(fill=tk.X, padx=8, pady=(4, 8))

        # 状态信息（左侧）
        self.status_var = tk.StringVar(value="📌 请选择文件夹或拖入文件夹后加载文件")
        tk.Label(bottom, textvariable=self.status_var, bg="#e8f8f0",
                 fg="#2c3e50", font=("Microsoft YaHei", 10),
                 padx=10, pady=10).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 底部也放一个执行按钮（双保险）
        run_btn2 = tk.Button(
            bottom, text="▶  执行重命名", command=self._execute,
            bg="#2ecc71", fg="white",
            activebackground="#27ae60", activeforeground="white",
            font=("Microsoft YaHei", 11, "bold"),
            relief="raised", bd=2, padx=20, pady=8, cursor="hand2",
        )
        run_btn2.pack(side=tk.RIGHT, padx=10, pady=6)

        # 注册拖拽目标：整个窗口 + 文件夹输入框都能接收
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)
        self.dnd_bind("<<DropEnter>>", self._on_drop_enter)
        self.dnd_bind("<<DropLeave>>", self._on_drop_leave)
        self.folder_entry.drop_target_register(DND_FILES)
        self.folder_entry.dnd_bind("<<Drop>>", self._on_drop)

    # ---------------- 拖拽支持 ----------------

    def _parse_drop_data(self, data):
        """
        tkinterdnd2 给出的 data 形如：
          - 普通路径:  C:/folder/file.txt
          - 含空格:    {C:/path with space/file.txt}
          - 多个文件:  {C:/a.txt} {C:/b.txt} C:/folder
        返回首个合法路径。
        """
        if not data:
            return ""
        # 用正则按空白分割，但保留 {xxx} 完整
        tokens = re.findall(r"\{[^}]*\}|\S+", data)
        for tok in tokens:
            path = tok.strip("{}") if tok.startswith("{") else tok
            if path and (os.path.isdir(path) or os.path.isfile(path)):
                return path
        return ""

    def _on_drop(self, event):
        path = self._parse_drop_data(event.data)
        self._on_drop_leave(event)  # 隐藏提示
        if not path:
            messagebox.showwarning("拖拽失败", "无法识别拖入的内容，请拖入文件夹或文件。")
            return
        # 拖入文件 → 取其所在目录
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self.folder.set(path)
        self._load_files()
        self.status_var.set("已通过拖拽加载文件夹：%s" % path)

    def _on_drop_enter(self, event):
        self.drop_hint.lift()
        self.drop_hint.configure(bg="#dceefc", fg="#1e6fd9")

    def _on_drop_leave(self, event):
        self.drop_hint.lower()

    def _build_rules(self, parent):
        # 删除文本
        f1 = ttk.LabelFrame(parent, text=" 删除文本 ", padding=6)
        f1.pack(fill=tk.X, pady=3)
        ttk.Checkbutton(f1, text="启用", variable=self.delete_enabled).pack(anchor=tk.W)
        row = ttk.Frame(f1); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="删除内容:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.delete_text).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(f1, text="删除文件名中所有出现的该文字", foreground="#888").pack(anchor=tk.W)

        # 替换文本
        f2 = ttk.LabelFrame(parent, text=" 替换文本 ", padding=6)
        f2.pack(fill=tk.X, pady=3)
        ttk.Checkbutton(f2, text="启用", variable=self.replace_enabled).pack(anchor=tk.W)
        row = ttk.Frame(f2); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="查找:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.replace_from).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = ttk.Frame(f2); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="替换为:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.replace_to).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 添加前后缀
        f3 = ttk.LabelFrame(parent, text=" 添加前缀 / 后缀 ", padding=6)
        f3.pack(fill=tk.X, pady=3)
        ttk.Checkbutton(f3, text="启用", variable=self.affix_enabled).pack(anchor=tk.W)
        row = ttk.Frame(f3); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="前缀:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.prefix).pack(side=tk.LEFT, fill=tk.X, expand=True)
        row = ttk.Frame(f3); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="后缀:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.suffix).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(f3, text="后缀添加在扩展名之前，可只填其一", foreground="#888").pack(anchor=tk.W)

        # 自动编号
        f4 = ttk.LabelFrame(parent, text=" 自动编号 ", padding=6)
        f4.pack(fill=tk.X, pady=3)
        ttk.Checkbutton(f4, text="启用", variable=self.number_enabled).pack(anchor=tk.W)
        row = ttk.Frame(f4); row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="起始值:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.number_start, width=6).pack(side=tk.LEFT)
        ttk.Label(row, text="  位数:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.number_digits, width=4).pack(side=tk.LEFT)
        ttk.Label(row, text="  分隔符:").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.number_sep, width=4).pack(side=tk.LEFT)
        row = ttk.Frame(f4); row.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(row, text="编号在前", variable=self.number_position,
                        value="prefix").pack(side=tk.LEFT)
        ttk.Radiobutton(row, text="编号在后", variable=self.number_position,
                        value="suffix").pack(side=tk.LEFT, padx=8)

        # 扩展名
        f5 = ttk.LabelFrame(parent, text=" 扩展名 ", padding=6)
        f5.pack(fill=tk.X, pady=3)
        ttk.Checkbutton(f5, text="启用", variable=self.ext_enabled).pack(anchor=tk.W)
        row = ttk.Frame(f5); row.pack(fill=tk.X, pady=2)
        ttk.Radiobutton(row, text="转小写", variable=self.ext_mode, value="lower").pack(side=tk.LEFT)
        ttk.Radiobutton(row, text="转大写", variable=self.ext_mode, value="upper").pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(row, text="自定义:", variable=self.ext_mode, value="custom").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.ext_custom, width=8).pack(side=tk.LEFT)

        ttk.Label(parent, text="规则按以下顺序生效：\n删除 → 替换 → 前后缀 → 编号 → 扩展名",
                  foreground="#888", justify=tk.LEFT).pack(anchor=tk.W, pady=6)

    # ---------------- 数据与预览 ----------------

    def _bind_traces(self):
        vars_to_watch = [
            self.delete_enabled, self.delete_text,
            self.replace_enabled, self.replace_from, self.replace_to,
            self.affix_enabled, self.prefix, self.suffix,
            self.number_enabled, self.number_start, self.number_digits,
            self.number_position, self.number_sep,
            self.ext_enabled, self.ext_mode, self.ext_custom,
        ]
        for v in vars_to_watch:
            v.trace_add("write", lambda *a: self._refresh_preview())

    def _collect_rules(self):
        return {
            "delete_enabled": self.delete_enabled.get(),
            "delete_text": self.delete_text.get(),
            "replace_enabled": self.replace_enabled.get(),
            "replace_from": self.replace_from.get(),
            "replace_to": self.replace_to.get(),
            "affix_enabled": self.affix_enabled.get(),
            "prefix": self.prefix.get(),
            "suffix": self.suffix.get(),
            "number_enabled": self.number_enabled.get(),
            "number_start": self.number_start.get(),
            "number_digits": self.number_digits.get(),
            "number_position": self.number_position.get(),
            "number_sep": self.number_sep.get(),
            "ext_enabled": self.ext_enabled.get(),
            "ext_mode": self.ext_mode.get(),
            "ext_custom": self.ext_custom.get(),
        }

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.folder.set(d)
            self._load_files()

    def _parse_filter(self):
        raw = self.ext_filter.get().strip()
        if not raw:
            return None
        exts = set()
        for part in raw.replace("，", ";").replace(",", ";").split(";"):
            part = part.strip().lstrip(".").lower()
            if part:
                exts.add("." + part)
        return exts or None

    def _walk_folder(self, root):
        """产出 (显示名, 绝对路径)。显示名相对目标文件夹。"""
        if self.recursive.get():
            for dirpath, _dirnames, filenames in os.walk(root):
                for fn in sorted(filenames):
                    ap = os.path.join(dirpath, fn)
                    yield os.path.relpath(ap, root), ap
        else:
            for fn in sorted(os.listdir(root)):
                ap = os.path.join(root, fn)
                if os.path.isfile(ap):
                    yield fn, ap

    def _load_files(self):
        root = self.folder.get().strip()
        if not root or not os.path.isdir(root):
            self.status_var.set("文件夹不存在，请重新选择")
            return
        exts = self._parse_filter()
        self.files = []
        for disp, ap in self._walk_folder(root):
            if exts is not None:
                _, e = split_name(os.path.basename(ap))
                if e.lower() not in exts:
                    continue
            self.files.append((disp, ap))
        self._refresh_preview()
        self.status_var.set("已加载 %d 个文件" % len(self.files))

    def _refresh_preview(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.preview_rows = []
        if not self.files:
            return
        rules = self._collect_rules()
        pairs = []
        existing = set(d for d, _ in self.files)
        for i, (disp, _ap) in enumerate(self.files):
            new_disp = build_new_name(disp, rules, i) if os.sep not in disp else \
                os.path.join(os.path.dirname(disp),
                             build_new_name(os.path.basename(disp), rules, i))
            pairs.append((disp, new_disp))
        conflicts = find_conflicts(pairs, existing)

        changed = 0
        blocked = False
        for old, new in pairs:
            if new == old:
                status, tag = "无变化", "unchanged"
            elif new in conflicts:
                status, tag = conflicts[new], "conflict"
                blocked = True
            else:
                err = check_name_valid(os.path.basename(new))
                if err:
                    status, tag = err, "invalid"
                    blocked = True
                else:
                    status, tag = "OK", ""
                    changed += 1
            self.preview_rows.append((old, new, status))
            self.tree.insert("", tk.END, values=(old, new, status), tags=(tag,) if tag else ())
        self._blocked = blocked
        self.status_var.set("共 %d 个文件，%d 个将被重命名%s"
                            % (len(pairs), changed, "，存在冲突/错误，已禁止执行" if blocked else ""))

    # ---------------- 执行与撤销 ----------------

    def _execute(self):
        if not self.preview_rows:
            messagebox.showinfo("提示", "请先加载文件")
            return
        if getattr(self, "_blocked", False):
            messagebox.showwarning("无法执行", "存在重名冲突或非法文件名，请调整规则后再试")
            return
        root = self.folder.get().strip()
        plan = []
        for old_disp, new_disp, status in self.preview_rows:
            if status == "OK":
                plan.append((os.path.join(root, old_disp), os.path.join(root, new_disp)))
        if not plan:
            messagebox.showinfo("提示", "没有需要重命名的文件")
            return
        if not messagebox.askyesno("确认", "即将重命名 %d 个文件，是否继续？\n（执行后可随时撤销）" % len(plan)):
            return

        ok, failed = 0, []
        done_pairs = []
        for old_abs, new_abs in plan:
            try:
                os.rename(old_abs, new_abs)
                done_pairs.append((old_abs, new_abs))
                ok += 1
            except OSError as e:
                failed.append((old_abs, str(e)))
        if done_pairs:
            self.history.push(done_pairs, root)
        self._update_undo_button()
        self._load_files()
        msg = "完成：成功 %d 个" % ok
        if failed:
            msg += "，失败 %d 个" % len(failed)
        self.status_var.set(msg)
        if failed:
            detail = "\n".join("%s\n  %s" % (os.path.basename(p), r) for p, r in failed[:10])
            messagebox.showwarning("部分失败", detail)

    def _undo(self):
        batch = self.history.pop()
        if not batch:
            messagebox.showinfo("提示", "没有可撤销的操作")
            return
        if not messagebox.askyesno(
                "确认撤销",
                "将撤销 %s 的操作（%d 个文件），是否继续？" % (batch["time"], len(batch["items"]))):
            self.history.batches.append(batch)  # 放回去
            self.history._save()
            return
        ok, failed = undo_batch(batch)
        self._update_undo_button()
        self._load_files()
        msg = "已撤销 %d 个文件" % ok
        if failed:
            msg += "，%d 个无法撤销" % len(failed)
        self.status_var.set(msg)
        if failed:
            detail = "\n".join("%s\n  %s" % (os.path.basename(p), r) for p, r in failed[:10])
            messagebox.showwarning("部分无法撤销", detail)

    def _update_undo_button(self):
        n = self.history.count()
        if n:
            self.undo_btn.config(state=tk.NORMAL,
                                 text="撤销上一次操作（可撤销 %d 批：%s）"
                                      % (n, self.history.last_desc()))
        else:
            self.undo_btn.config(state=tk.DISABLED, text="撤销上一次操作")


if __name__ == "__main__":
    app = RenamerApp()
    app.mainloop()
