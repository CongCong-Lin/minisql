"""MiniSQL Studio 桌面窗口：布局对齐常见 SQL 客户端（对象树 / 查询 / 结果）。"""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from sql_compiler.errors import CompileError, ExecuteError
from studio.inspect import Diagnostic
from studio.session import StudioSession, format_cell, format_plan_tree, pretty

TEMPLATES = {
    "CREATE": (
        "CREATE TABLE student(id INT, name VARCHAR, score INT, team VARCHAR);\n"
        "CREATE TABLE course(student_id INT, title VARCHAR);"
    ),
    "INSERT": (
        "INSERT INTO student(id, name, score, team) VALUES(1, 'Ada', 85, 'A');\n"
        "INSERT INTO course(student_id, title) VALUES(1, '数据库');"
    ),
    "SELECT": "SELECT * FROM student WHERE id = 1;",
    "UPDATE": "UPDATE student SET score=score+5 WHERE team='A';",
    "DELETE": "DELETE FROM student WHERE id = 1;",
    "ORDER": "SELECT name, score FROM student ORDER BY score DESC, id ASC;",
    "JOIN": (
        "SELECT s.name, c.title, s.score\n"
        "FROM student s JOIN course c ON s.id=c.student_id\n"
        "ORDER BY s.id, c.title;"
    ),
    "GROUP": (
        "SELECT team, COUNT(*) AS n, SUM(score) AS total, AVG(score) AS mean\n"
        "FROM student GROUP BY team HAVING mean>80 ORDER BY total DESC;"
    ),
    "综合示例": (
        "CREATE TABLE student(id INT, name VARCHAR, score INT, team VARCHAR);\n"
        "CREATE TABLE course(student_id INT, title VARCHAR);\n"
        "INSERT INTO student(id, name, score, team) VALUES(1, 'Ada', 85, 'A');\n"
        "INSERT INTO student(id, name, score, team) VALUES(2, 'Bob', 92, 'A');\n"
        "INSERT INTO student(id, name, score, team) VALUES(3, 'Cara', 67, 'B');\n"
        "INSERT INTO course(student_id, title) VALUES(1, '数据库');\n"
        "INSERT INTO course(student_id, title) VALUES(2, '编译原理');\n"
        "UPDATE student SET score=score+5 WHERE team='A';\n"
        "SELECT name, score FROM student ORDER BY score DESC, id ASC;\n"
        "SELECT s.name, c.title FROM student s JOIN course c ON s.id=c.student_id ORDER BY s.id;\n"
        "SELECT team, COUNT(*) AS n, AVG(score) AS mean FROM student GROUP BY team HAVING mean>70;\n"
        "DELETE FROM student WHERE id = 3;\n"
        "SELECT * FROM student;"
    ),
}

DEFAULT_SQL = (
    "CREATE TABLE student(id INT, name VARCHAR);\n"
    "INSERT INTO student(id, name) VALUES(1, 'Ada');\n"
    "SELECT * FROM student;"
)

TITLE_BG = "#1f4e79"
TOOL_BG = "#f3f3f3"
PANE_BG = "#e7eef6"
GRID_LINE = "#5c6f80"
GRID_HEADER = "#c5d8ec"
GRID_CELL = "#ffffff"
GRID_ALT = "#e7eef6"
RESULT_HL = "#FFD54F"
RESULT_HL_FG = "#3E2723"


def _enable_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _work_area(root: tk.Misc) -> tuple[int, int, int, int]:
    """返回可用桌面宽、高及左上角，尽量避开任务栏。"""
    if sys.platform == "win32":
        try:
            import ctypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                return (
                    max(640, rect.right - rect.left),
                    max(400, rect.bottom - rect.top),
                    rect.left,
                    rect.top,
                )
        except Exception:
            pass
    width = max(640, int(root.winfo_screenwidth()))
    height = max(400, int(root.winfo_screenheight()))
    return width, height, 0, 0


def _attach_data_grid(parent: tk.Misc, columns: list[str], rows: list) -> None:
    """用深色缝绘制单元格，避免系统 Treeview 格子线几乎看不见。"""
    canvas = tk.Canvas(parent, background=GRID_CELL, highlightthickness=0, bd=0)
    sy = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
    sx = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=canvas.xview)
    canvas.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    sy.grid(row=0, column=1, sticky="ns")
    sx.grid(row=1, column=0, sticky="ew")
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)

    table = tk.Frame(canvas, background=GRID_LINE, bd=0, highlightthickness=0)
    window_id = canvas.create_window((0, 0), window=table, anchor="nw")

    display_cols = columns or [""]
    data_rows = rows or []

    def _wheel(event) -> str:
        if event.delta:
            canvas.yview_scroll(int(-event.delta / 120), "units")
        return "break"

    def put_cell(r: int, c: int, text: str, *, header: bool, alt: bool) -> None:
        bg = GRID_HEADER if header else (GRID_ALT if alt else GRID_CELL)
        font = ("Segoe UI", 10, "bold") if header else ("Segoe UI", 10)
        cell = tk.Label(
            table,
            text=text,
            font=font,
            background=bg,
            foreground="#1b1b1b",
            anchor="w",
            padx=12,
            pady=6,
            bd=0,
            highlightthickness=0,
        )
        cell.grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
        cell.bind("<MouseWheel>", _wheel)
        table.columnconfigure(c, weight=1, minsize=88)

    for col_index, name in enumerate(display_cols):
        put_cell(0, col_index, name, header=True, alt=False)
    if not data_rows:
        empty = tk.Label(
            table, text="（零行）", font=("Segoe UI", 10),
            background=GRID_CELL, foreground="#5a5a5a", anchor="w", padx=12, pady=8, bd=0,
        )
        empty.grid(row=1, column=0, columnspan=max(1, len(display_cols)),
                   sticky="nsew", padx=1, pady=1)
        empty.bind("<MouseWheel>", _wheel)
    else:
        for row_index, row in enumerate(data_rows):
            values = list(row) + [""] * max(0, len(display_cols) - len(row))
            for col_index, value in enumerate(values[:len(display_cols)]):
                put_cell(row_index + 1, col_index, format_cell(value),
                         header=False, alt=row_index % 2 == 1)

    def _sync(_event=None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))
        inner_width = table.winfo_reqwidth()
        view_width = canvas.winfo_width()
        canvas.itemconfigure(window_id, width=max(inner_width, view_width))

    table.bind("<Configure>", _sync)
    canvas.bind("<Configure>", _sync)
    canvas.bind("<MouseWheel>", _wheel)
    table.bind("<MouseWheel>", _wheel)


class StudioApp(tk.Tk):
    """SQLyog 风格的 MiniSQL 桌面客户端。"""

    def __init__(self) -> None:
        super().__init__()
        self.title("MiniSQL Studio")
        self.configure(bg="#d6d6d6")
        self.session = StudioSession()
        self._last: dict | None = None
        self._diagnostics: list[Diagnostic] = []
        self._inspect_job: str | None = None
        self._tip: tk.Toplevel | None = None
        self._query_seq = 1
        self._query_index = 0
        self._queries = [self._make_query("查询 1", DEFAULT_SQL)]
        self._sash_placed = False
        self._result_tab_index: dict[str, int] = {}
        self._nb_updating = False
        self._locate_source = True
        self._highlight_sql: str | None = None
        self._build()
        self._fit_to_screen()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind_all("<F9>", lambda _e: self.execute())
        self.bind_all("<Control-Return>", lambda _e: self.execute())
        self.bind_all("<Control-n>", lambda _e: self.new_query())
        self.bind_all("<Control-N>", lambda _e: self.new_query())
        self.bind_all("<Control-w>", lambda _e: self.close_query())
        self.bind_all("<Control-W>", lambda _e: self.close_query())

    def _build(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", background=TITLE_BG, foreground="#ffffff",
                        font=("Segoe UI", 11, "bold"))
        style.configure("Sub.TLabel", background=TITLE_BG, foreground="#d6e8f8",
                        font=("Segoe UI", 9))
        style.configure("Head.TLabel", background=PANE_BG, font=("Segoe UI", 9, "bold"))
        style.configure("Tool.TFrame", background=TOOL_BG)
        style.configure("Tool.TLabel", background=TOOL_BG)
        style.configure("Tool.TCheckbutton", background=TOOL_BG)
        style.configure("Status.TFrame", background=TOOL_BG)
        style.configure("Status.TLabel", background=TOOL_BG, foreground="#5a5a5a")

        self._build_menu()
        self._build_title()
        self._build_toolbar()
        self._build_workspace()
        self._build_status()
        self._set_connected(False)

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="选择数据目录…", command=self._browse)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)
        query = tk.Menu(menu, tearoff=0)
        query.add_command(label="新建查询\tCtrl+N", command=self.new_query)
        query.add_command(label="关闭查询\tCtrl+W", command=self.close_query)
        query.add_separator()
        query.add_command(label="执行\tF9", command=self.execute)
        menu.add_cascade(label="查询", menu=query)
        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="关于", command=self._about)
        menu.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menu)

    def _build_title(self) -> None:
        bar = tk.Frame(self, bg=TITLE_BG)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="MiniSQL Studio", bg=TITLE_BG, fg="#ffffff",
                 font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=(12, 8), pady=6)
        self._title_sub = tk.Label(
            bar, text="桌面客户端  ·  SELECT / UPDATE / JOIN / GROUP BY 与编译产物",
            bg=TITLE_BG, fg="#d6e8f8", font=("Segoe UI", 9),
        )
        self._title_sub.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))

    def _build_toolbar(self) -> None:
        wrap = ttk.Frame(self, style="Tool.TFrame")
        wrap.pack(fill=tk.X)
        row1 = ttk.Frame(wrap, style="Tool.TFrame")
        row1.pack(fill=tk.X, padx=8, pady=(6, 2))
        ttk.Label(row1, text="模式", style="Tool.TLabel").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="database")
        self.mode_box = ttk.Combobox(
            row1, textvariable=self.mode_var, state="readonly", width=16,
            values=("database（页存储）", "compiler（JSON 目录）"),
        )
        self.mode_box.current(0)
        self.mode_box.pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(row1, text="数据目录", style="Tool.TLabel").pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value="data")
        self.dir_entry = ttk.Entry(row1, textvariable=self.dir_var, width=22)
        self.dir_entry.pack(side=tk.LEFT, padx=(4, 4))
        self.btn_browse = ttk.Button(row1, text="浏览…", command=self._browse, width=8)
        self.btn_browse.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_open = ttk.Button(row1, text="连接", command=self.connect)
        self.btn_open.pack(side=tk.LEFT, padx=2)
        self.btn_close = ttk.Button(row1, text="断开", command=self.disconnect)
        self.btn_close.pack(side=tk.LEFT, padx=2)
        self.btn_run = ttk.Button(row1, text="执行 (F9)", command=self.execute)
        self.btn_run.pack(side=tk.LEFT, padx=(8, 4))
        ttk.Button(row1, text="新建查询", command=self.new_query).pack(side=tk.LEFT)

        row2 = ttk.Frame(wrap, style="Tool.TFrame")
        row2.pack(fill=tk.X, padx=8, pady=(0, 6))
        for name in ("CREATE", "INSERT", "SELECT", "UPDATE", "DELETE"):
            ttk.Button(row2, text=name, command=lambda n=name: self._insert_template(n)).pack(
                side=tk.LEFT, padx=(0, 4)
            )
        row3 = ttk.Frame(wrap, style="Tool.TFrame")
        row3.pack(fill=tk.X, padx=8, pady=(0, 6))
        for name in ("ORDER", "JOIN", "GROUP", "综合示例"):
            ttk.Button(row3, text=name, command=lambda n=name: self._insert_template(n)).pack(
                side=tk.LEFT, padx=(0, 4)
            )
        self.show_tokens = tk.BooleanVar(value=True)
        self.show_ast = tk.BooleanVar(value=True)
        self.show_plan = tk.BooleanVar(value=True)
        self.show_opt = tk.BooleanVar(value=True)
        for text, var in (
            ("Tokens", self.show_tokens),
            ("AST", self.show_ast),
            ("Plan", self.show_plan),
            ("OptPlan", self.show_opt),
        ):
            ttk.Checkbutton(row3, text=text, variable=var, style="Tool.TCheckbutton").pack(
                side=tk.LEFT, padx=(8 if text == "Tokens" else 2, 0)
            )

    def _build_workspace(self) -> None:
        outer = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        self._outer_paned = outer
        outer.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        left = ttk.Frame(outer, width=220)
        head = tk.Frame(left, bg=PANE_BG)
        head.pack(fill=tk.X)
        tk.Label(
            head, text="对象浏览器  ·  右键打开表 / 双击生成 SELECT",
            bg=PANE_BG, font=("Segoe UI", 9, "bold"),
            wraplength=200, justify=tk.LEFT, anchor="w",
        ).pack(anchor=tk.W, padx=8, pady=5, fill=tk.X)
        tree_wrap = ttk.Frame(left)
        tree_wrap.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tree_wrap, show="tree", columns=("table", "column"))
        yscroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Button-3>", self._on_tree_right)
        self._menu_table: str | None = None
        self._table_menu = tk.Menu(self, tearoff=0)
        self._table_menu.add_command(label="打开表", command=self._open_menu_table)
        self._table_menu.add_command(label="在当前查询生成 SELECT *", command=self._script_menu_table)
        self._table_menu.add_command(label="在新查询中打开", command=self._open_menu_table_new)
        outer.add(left, weight=1)

        right = ttk.Panedwindow(outer, orient=tk.VERTICAL)
        self._right_paned = right
        outer.add(right, weight=5)

        editor = ttk.Frame(right)
        tab_host = tk.Frame(editor, bg="#e9e9e9")
        tab_host.pack(fill=tk.X)
        self.tab_canvas = tk.Canvas(tab_host, height=32, bg="#e9e9e9", highlightthickness=0)
        self.tab_scroll = ttk.Scrollbar(tab_host, orient=tk.HORIZONTAL, command=self.tab_canvas.xview)
        self.tab_canvas.configure(xscrollcommand=self._on_tab_scroll)
        self.tab_canvas.pack(fill=tk.X, expand=True)
        self.tabbar = tk.Frame(self.tab_canvas, bg="#e9e9e9")
        self._tab_window = self.tab_canvas.create_window((0, 0), window=self.tabbar, anchor="nw")
        self.tabbar.bind("<Configure>", self._sync_query_tab_strip)
        self._rebuild_query_tabs()
        self.inspect_wrap = tk.Frame(editor, bg="#f8d7da", highlightthickness=0)
        stripe = tk.Frame(self.inspect_wrap, bg="#c42b1c", width=4)
        stripe.pack(side=tk.LEFT, fill=tk.Y)
        self.inspect_text = tk.Text(
            self.inspect_wrap, height=2, wrap=tk.WORD, relief=tk.FLAT,
            font=("Segoe UI", 9), bg="#fff5f5", fg="#c42b1c",
            padx=8, pady=4, cursor="hand2",
        )
        self.inspect_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inspect_text.configure(state=tk.DISABLED)
        self.inspect_text.bind("<Button-1>", self._jump_inspect)
        self.sql_wrap = ttk.Frame(editor)
        self.sql_wrap.pack(fill=tk.BOTH, expand=True)
        self.sql = tk.Text(
            self.sql_wrap, wrap=tk.NONE, undo=True, font=("Consolas", 11),
            insertbackground="#1b1b1b", relief=tk.FLAT, padx=8, pady=8,
        )
        self.sql.tag_configure(
            "sql-error",
            foreground="#a80000",
            background="#ffd7d5",
            underline=True,
        )
        self.sql.tag_configure(
            "sql-result",
            background=RESULT_HL,
            foreground=RESULT_HL_FG,
        )
        self.sql.tag_raise("sql-error")
        self.sql.insert("1.0", DEFAULT_SQL)
        sx = ttk.Scrollbar(self.sql_wrap, orient=tk.HORIZONTAL, command=self.sql.xview)
        sy = ttk.Scrollbar(self.sql_wrap, orient=tk.VERTICAL, command=self.sql.yview)
        self.sql.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.sql.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        self.sql_wrap.rowconfigure(0, weight=1)
        self.sql_wrap.columnconfigure(0, weight=1)
        self.sql.bind("<KeyRelease>", self._on_sql_change)
        self.sql.bind("<ButtonRelease-1>", self._update_caret)
        self.sql.bind("<Motion>", self._on_sql_motion)
        self.sql.bind("<Leave>", lambda _e: self._hide_tip())
        self.sql.tag_bind("sql-error", "<Leave>", lambda _e: self._hide_tip())
        right.add(editor, weight=2)
        self.after(200, self._inspect_now)

        result = ttk.Frame(right)
        self.nb = ttk.Notebook(result)
        self.nb.pack(fill=tk.BOTH, expand=True)
        self.nb.bind("<<NotebookTabChanged>>", self._on_result_tab_changed)
        self.nb.bind("<Button-1>", self._on_result_tab_click)
        self._placeholder_tab()
        right.add(result, weight=3)
        try:
            outer.pane(left, minsize=140)
            outer.pane(right, minsize=280)
            right.pane(editor, minsize=110)
            right.pane(result, minsize=110)
        except tk.TclError:
            pass
        outer.bind("<Configure>", self._place_sashes)

    def _build_status(self) -> None:
        bar = ttk.Frame(self, style="Status.TFrame")
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.st_msg = ttk.Label(bar, text="未连接", style="Status.TLabel")
        self.st_time = ttk.Label(bar, text="耗时 —", style="Status.TLabel")
        self.st_rows = ttk.Label(bar, text="行数 —", style="Status.TLabel")
        self.st_pos = ttk.Label(bar, text="Ln 1, Col 1", style="Status.TLabel")
        self.st_conn = ttk.Label(bar, text="无会话", style="Status.TLabel")
        self.st_msg.pack(side=tk.LEFT, padx=8, pady=3)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        self.st_time.pack(side=tk.LEFT, padx=8)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        self.st_rows.pack(side=tk.LEFT, padx=8)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        self.st_pos.pack(side=tk.LEFT, padx=8)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, pady=2)
        self.st_conn.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

    def _placeholder_tab(self) -> None:
        self._reset_result_pane()

    def _reset_result_pane(self) -> None:
        self._nb_updating = True
        self._result_tab_index = {}
        self._clear_result_highlight()
        for tab in self.nb.tabs():
            self.nb.forget(tab)
        for child in self.nb.winfo_children():
            child.destroy()
        frame = ttk.Frame(self.nb)
        ttk.Label(
            frame,
            text="此查询尚未执行。按 F9 后，结果、消息和 Tokens / AST / Plan 会显示在这里。",
            foreground="#5a5a5a",
            padding=16,
        ).pack(anchor=tk.W)
        self.nb.add(frame, text="Messages")
        self._nb_updating = False

    def _make_query(self, title: str, sql: str = "") -> dict:
        return {
            "title": title,
            "sql": sql,
            "payload": None,
            "result_title": None,
            "st_msg": None,
            "st_time": None,
            "st_rows": None,
            "history": [],
            "locate_source": True,
        }

    def _restore_query_results(self, query: dict) -> None:
        payload = query.get("payload")
        self._last = payload
        if payload is None:
            self._reset_result_pane()
            self.st_time.configure(text="耗时 —")
            self.st_rows.configure(text="行数 —")
            if self.session.connected:
                self.st_msg.configure(text="此查询尚未执行")
            return
        self.st_time.configure(text=query.get("st_time") or "耗时 —")
        self.st_rows.configure(text=query.get("st_rows") or "行数 —")
        if query.get("st_msg"):
            self.st_msg.configure(text=query["st_msg"])
        self._show_payload(
            payload,
            result_title=query.get("result_title"),
            locate_source=bool(query.get("locate_source", query.get("result_title") is None)),
        )

    def _mode_value(self) -> str:
        raw = self.mode_var.get()
        return "compiler" if raw.startswith("compiler") else "database"

    def _set_connected(self, on: bool) -> None:
        self.btn_open.configure(state=tk.DISABLED if on else tk.NORMAL)
        self.btn_close.configure(state=tk.NORMAL if on else tk.DISABLED)
        self.btn_run.configure(state=tk.NORMAL if on else tk.DISABLED)
        self.mode_box.configure(state="disabled" if on else "readonly")
        self.dir_entry.configure(state=tk.DISABLED if on else tk.NORMAL)
        self.btn_browse.configure(state=tk.DISABLED if on else tk.NORMAL)
        if on:
            if self.session.mode == "compiler":
                self.st_msg.configure(text="已连接（compiler 只提交 CREATE）")
            else:
                self.st_msg.configure(text="已连接")
            self.st_conn.configure(text=f"{self.session.mode}  ·  {self.session.data_dir}")
        else:
            self.st_msg.configure(text="未连接")
            self.st_conn.configure(text="无会话")
            self._fill_tree([])

    def _fill_tree(self, tables: list[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        if not tables:
            root = self.tree.insert("", tk.END, text="（没有用户表，可用 CREATE TABLE 建表）", open=True)
            self.tree.item(root, tags=("empty",))
            return
        root = self.tree.insert("", tk.END, text="MiniSQL", open=True)
        for table in tables:
            node = self.tree.insert(root, tk.END, text=f"表 {table['name']}",
                                    values=(table["name"], ""), open=True, tags=("table",))
            for col in table["columns"]:
                self.tree.insert(node, tk.END, text=f"{col['name']} : {col['type']}",
                                 values=(table["name"], col["name"]), tags=("col",))

    def _table_from_item(self, item: str) -> str | None:
        tags = set(self.tree.item(item, "tags"))
        values = self.tree.item(item, "values")
        if ("table" in tags or "col" in tags) and values:
            return str(values[0])
        return None

    def _on_tree_double(self, _event=None) -> None:
        item = self.tree.focus()
        if not item:
            return
        tags = set(self.tree.item(item, "tags"))
        values = self.tree.item(item, "values")
        if "table" in tags and values:
            self._replace_sql(f"SELECT * FROM {values[0]};")
        elif "col" in tags and len(values) >= 2:
            self._replace_sql(f"SELECT {values[1]} FROM {values[0]};")

    def _on_tree_right(self, event) -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        table = self._table_from_item(item)
        if not table:
            return
        self.tree.selection_set(item)
        self.tree.focus(item)
        self._menu_table = table
        try:
            self._table_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._table_menu.grab_release()

    def _open_menu_table(self) -> None:
        if self._menu_table:
            self.open_table(self._menu_table)

    def _script_menu_table(self) -> None:
        if self._menu_table:
            self._replace_sql(f"SELECT * FROM {self._menu_table};")

    def _open_menu_table_new(self) -> None:
        if self._menu_table:
            self.new_query(f"SELECT * FROM {self._menu_table};")

    def _save_current_query(self) -> None:
        if not self._queries:
            return
        self._queries[self._query_index]["sql"] = self.sql.get("1.0", "end-1c")

    def _rebuild_query_tabs(self) -> None:
        for child in self.tabbar.winfo_children():
            child.destroy()
        for index, query in enumerate(self._queries):
            active = index == self._query_index
            bg = "#ffffff" if active else "#e9e9e9"
            holder = tk.Frame(self.tabbar, bg=bg, highlightbackground="#c5c5c5",
                              highlightthickness=1)
            tk.Button(
                holder, text=f"  {query['title']}  ", bd=0, relief=tk.FLAT,
                bg=bg, font=("Segoe UI", 9, "bold" if active else "normal"),
                command=lambda i=index: self.switch_query(i),
            ).pack(side=tk.LEFT)
            tk.Button(
                holder, text="×", bd=0, relief=tk.FLAT, bg=bg, fg="#666666",
                font=("Segoe UI", 9), padx=4,
                command=lambda i=index: self.close_query(i),
            ).pack(side=tk.LEFT)
            holder.pack(side=tk.LEFT, padx=(0, 1), fill=tk.Y)
        tk.Button(
            self.tabbar, text="  +  ", bd=0, relief=tk.FLAT, bg="#e9e9e9",
            font=("Segoe UI", 11), command=self.new_query,
        ).pack(side=tk.LEFT, fill=tk.Y)
        self._sync_query_tab_strip()

    def _on_tab_scroll(self, first, last) -> None:
        self.tab_scroll.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.tab_scroll.pack_forget()
        elif not self.tab_scroll.winfo_ismapped():
            self.tab_scroll.pack(fill=tk.X, side=tk.BOTTOM)

    def _sync_query_tab_strip(self, _event=None) -> None:
        try:
            self.tabbar.update_idletasks()
            height = max(28, self.tabbar.winfo_reqheight())
            bbox = self.tab_canvas.bbox("all")
            if bbox:
                self.tab_canvas.configure(scrollregion=bbox, height=height)
            else:
                self.tab_canvas.configure(height=height)
            self.tab_canvas.itemconfigure(self._tab_window, height=height)
        except tk.TclError:
            pass

    def _fit_to_screen(self) -> None:
        """按工作区缩放窗口，小屏直接最大化，避免控件被裁掉。"""
        self.update_idletasks()
        work_w, work_h, origin_x, origin_y = _work_area(self)
        max_w = max(640, work_w - 16)
        max_h = max(420, work_h - 16)
        width = min(1280, max_w)
        height = min(800, max_h)
        self.minsize(min(640, max_w), min(420, max_h))
        if width < 1100 or height < 680:
            try:
                self.state("zoomed")
                return
            except tk.TclError:
                pass
        x = origin_x + max(0, (work_w - width) // 2)
        y = origin_y + max(0, (work_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _place_sashes(self, event=None) -> None:
        if event is not None and event.widget is not self._outer_paned:
            return
        if self._sash_placed:
            return
        width = self._outer_paned.winfo_width()
        height = self._right_paned.winfo_height()
        if width < 80:
            return
        try:
            self._outer_paned.sashpos(0, max(150, min(240, int(width * 0.2))))
            if height > 80:
                self._right_paned.sashpos(0, max(120, min(int(height * 0.42), height - 120)))
            self._sash_placed = True
        except tk.TclError:
            pass

    def new_query(self, sql: str = "") -> None:
        """打开一个空白（或带初始 SQL）的新查询页签。"""
        self._save_current_query()
        self._query_seq += 1
        self._queries.append(self._make_query(f"查询 {self._query_seq}", sql))
        self._query_index = len(self._queries) - 1
        self._load_query(self._query_index)

    def switch_query(self, index: int) -> None:
        if index == self._query_index or not (0 <= index < len(self._queries)):
            return
        self._save_current_query()
        self._load_query(index)

    def close_query(self, index: int | None = None) -> None:
        if index is None:
            index = self._query_index
        if not (0 <= index < len(self._queries)):
            return
        if len(self._queries) == 1:
            self.sql.delete("1.0", tk.END)
            blank = self._make_query(self._queries[0]["title"], "")
            self._queries[0] = blank
            self._inspect_now()
            self._update_caret()
            self._restore_query_results(blank)
            return
        if index != self._query_index:
            self._save_current_query()
        del self._queries[index]
        if self._query_index > index:
            self._query_index -= 1
        elif self._query_index == index:
            self._query_index = min(index, len(self._queries) - 1)
        self._load_query(self._query_index)

    def _load_query(self, index: int) -> None:
        self._query_index = index
        self.sql.delete("1.0", tk.END)
        self.sql.insert("1.0", self._queries[index]["sql"])
        self._rebuild_query_tabs()
        self._inspect_now()
        self._update_caret()
        self._restore_query_results(self._queries[index])
        self.sql.focus_set()

    def _replace_sql(self, sql: str) -> None:
        self._clear_result_highlight()
        self.sql.delete("1.0", tk.END)
        self.sql.insert("1.0", sql)
        self._queries[self._query_index]["sql"] = sql
        self._schedule_inspect()

    def open_table(self, name: str) -> None:
        """右键「打开表」：查询该表全部行，不改编辑器里的 SQL。"""
        if not self.session.connected:
            return
        if self.session.mode == "compiler":
            messagebox.showinfo(
                "打开表",
                "compiler 模式只提交 CREATE，没有表内数据。\n请用 database 模式连接后再打开表。",
                parent=self,
            )
            return
        self._run_and_show(
            f"SELECT * FROM {name};",
            status=f"已打开表 {name}",
            result_title=f"表 {name}",
        )

    def _insert_template(self, name: str) -> None:
        self._replace_sql(TEMPLATES[name])

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(title="选择数据目录")
        if chosen:
            self.dir_var.set(chosen)

    def connect(self) -> None:
        try:
            tables = self.session.open(self.dir_var.get().strip() or "data", self._mode_value())
        except (CompileError, ExecuteError, OSError) as exc:
            messagebox.showerror("连接失败", str(exc), parent=self)
            self.st_msg.configure(text="连接失败")
            return
        self._set_connected(True)
        self._fill_tree(tables)
        self.st_msg.configure(text="连接成功")
        self._schedule_inspect()

    def disconnect(self) -> None:
        self.session.close()
        self._set_connected(False)
        self._schedule_inspect()

    def execute(self, _event=None) -> None:
        if not self.session.connected:
            return
        self._run_and_show(self.sql.get("1.0", "end-1c"))

    def _run_and_show(self, sql: str, *, status: str | None = None,
                      result_title: str | None = None) -> None:
        started = time.perf_counter()
        try:
            payload = self.session.run_sql(sql)
        except (CompileError, ExecuteError) as exc:
            messagebox.showerror("执行失败", str(exc), parent=self)
            self.st_msg.configure(text="执行失败")
            return
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        current = self._queries[self._query_index]
        current["payload"] = payload
        current["result_title"] = result_title
        current["locate_source"] = result_title is None
        current["history"] = current.get("history") or []
        current["history"].insert(0, (time.strftime("%H:%M:%S"), sql))
        current["history"] = current["history"][:50]
        query = next((item for item in payload["results"] if item.get("exec") and item["exec"]["is_query"]), None)
        rows = len(query["exec"]["rows"]) if query else 0
        current["st_time"] = f"耗时 {elapsed_ms} ms"
        current["st_rows"] = f"行数 {rows}"
        if payload["sql_failed"]:
            current["st_msg"] = "SQL 含失败语句"
        else:
            current["st_msg"] = status or "执行完成"
        self._last = payload
        self._fill_tree(payload["tables"])
        self.st_time.configure(text=current["st_time"])
        self.st_rows.configure(text=current["st_rows"])
        self.st_msg.configure(text=current["st_msg"])
        self._show_payload(
            payload,
            result_title=result_title,
            locate_source=result_title is None,
        )
        self._schedule_inspect()

    def _show_payload(self, payload: dict, *, result_title: str | None = None,
                      locate_source: bool = True) -> None:
        self._nb_updating = True
        self._locate_source = locate_source
        self._result_tab_index = {}
        self._clear_result_highlight()
        for tab in self.nb.tabs():
            self.nb.forget(tab)
        for child in self.nb.winfo_children():
            child.destroy()
        results = payload.get("results") or []
        first = None
        for index, item in enumerate(results):
            exec_result = item.get("exec")
            if exec_result and exec_result.get("is_query"):
                frame = self._grid_tab(exec_result)
                title = result_title if result_title and first is None else f"Result {index + 1}"
                self.nb.add(frame, text=title)
                tab_id = str(self.nb.tabs()[-1])
                if locate_source:
                    self._result_tab_index[tab_id] = index
                if first is None:
                    first = title
        self.nb.add(self._text_tab(self._messages(results, payload), error=payload.get("sql_failed")),
                    text="Messages")
        if self.show_tokens.get():
            self.nb.add(self._json_tab(results, "tokens"), text="Tokens")
        if self.show_ast.get():
            self.nb.add(self._json_tab(results, "ast"), text="AST")
        if self.show_plan.get():
            self.nb.add(self._plan_tab(results, "plan"), text="Plan")
        if self.show_opt.get():
            self.nb.add(self._plan_tab(results, "opt_plan"), text="Opt Plan")
        self.nb.add(self._text_tab(self._history_text()), text="History")
        self.nb.add(self._text_tab(pretty(payload.get("tables") or [])), text="Object Info")
        if first:
            for tab in self.nb.tabs():
                if self.nb.tab(tab, "text") == first:
                    self.nb.select(tab)
                    break
        self._nb_updating = False
        self._highlight_selected_result()

    def _grid_tab(self, exec_result: dict) -> ttk.Frame:
        frame = ttk.Frame(self.nb)
        cols = [str(c) for c in exec_result.get("columns") or []]
        rows = exec_result.get("rows") or []
        wrap = ttk.Frame(frame)
        wrap.pack(fill=tk.BOTH, expand=True)
        _attach_data_grid(wrap, cols, rows)
        msg = exec_result.get("message") or ""
        ttk.Label(frame, text=msg, foreground="#107c10", padding=(8, 4)).pack(anchor=tk.W)
        return frame

    def _text_tab(self, content: str, *, error: bool = False) -> ttk.Frame:
        frame = ttk.Frame(self.nb)
        text = tk.Text(frame, wrap=tk.WORD, font=("Consolas", 10), relief=tk.FLAT, padx=8, pady=8)
        sy = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=sy.set, fg="#c42b1c" if error else "#1b1b1b")
        text.insert("1.0", content or "(空)")
        text.configure(state=tk.DISABLED)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sy.pack(side=tk.RIGHT, fill=tk.Y)
        return frame

    def _json_tab(self, results: list[dict], key: str) -> ttk.Frame:
        chunks = []
        for index, item in enumerate(results):
            value = item.get(key)
            if value is not None:
                chunks.append(f"-- 语句 {index + 1} --\n{pretty(value)}")
        body = "\n\n".join(chunks) if chunks else "本批语句没有该阶段产物（例如词法失败不会保留 Token 展示）。"
        return self._text_tab(body)

    def _plan_tab(self, results: list[dict], key: str) -> ttk.Frame:
        chunks = []
        for index, item in enumerate(results):
            value = item.get(key)
            if value is not None:
                tree = format_plan_tree(value)
                chunks.append(f"-- 语句 {index + 1} --\n{tree}\n\n{pretty(value)}")
        body = "\n\n".join(chunks) if chunks else "本批语句没有该阶段产物（例如词法失败不会保留 Token 展示）。"
        return self._text_tab(body)

    def _on_result_tab_changed(self, _event=None) -> None:
        if self._nb_updating:
            return
        self._highlight_selected_result()

    def _on_result_tab_click(self, _event=None) -> None:
        if self._nb_updating:
            return
        self.after_idle(self._highlight_selected_result)

    def _highlight_selected_result(self) -> None:
        if not self._locate_source or not self._last:
            return
        try:
            tab = str(self.nb.select())
        except tk.TclError:
            return
        index = self._result_tab_index.get(tab)
        if index is None:
            return
        self._highlight_statement(index)

    def _highlight_statement(self, index: int) -> None:
        """跳转到产生该 Result 的语句，并用 IDEA 风格黄底标出整段 SQL。"""
        self._clear_result_highlight()
        results = (self._last or {}).get("results") or []
        if not (0 <= index < len(results)):
            return
        span = results[index].get("span")
        if not span:
            return
        start, end = span.get("start"), span.get("end")
        if not start or not end:
            return
        try:
            start_line = int(str(start).split(".")[0])
            end_line = int(str(end).split(".")[0])
            start = f"{start_line}.0"
            end = f"{end_line}.end"
            self.sql.tag_add("sql-result", start, end)
            try:
                self.sql.tag_add("sel", start, end)
            except tk.TclError:
                pass
            self.sql.tag_raise("sql-result")
            self.sql.tag_raise("sql-error")
            self.sql.mark_set(tk.INSERT, start)
            self.sql.see(end)
            self.sql.see(start)
            self.sql.focus_set()
            self._highlight_sql = self.sql.get("1.0", "end-1c")
            self._update_caret()
        except (tk.TclError, ValueError, TypeError):
            self._clear_result_highlight()

    def _clear_result_highlight(self) -> None:
        self._highlight_sql = None
        try:
            self.sql.tag_remove("sql-result", "1.0", tk.END)
        except tk.TclError:
            pass

    def _messages(self, results: list[dict], payload: dict) -> str:
        if not results:
            return "(空输入，无语句)"
        parts = []
        for index, item in enumerate(results):
            lines = [f"语句 {index + 1}: {'成功' if item.get('ok') else '失败'}"]
            if item.get("semantic_ok") is True:
                lines.append("SEMANTIC: OK")
            if item.get("exec"):
                lines.append("RESULT: " + item["exec"]["message"])
            if item.get("error"):
                lines.append(item["error"])
            parts.append("\n".join(lines))
        tail = "退出码含义：存在 SQL 失败。" if payload.get("sql_failed") else "全部成功。"
        return "\n\n".join(parts) + "\n\n" + tail

    def _history_text(self) -> str:
        items = (self._queries[self._query_index].get("history") or []) if self._queries else []
        if not items:
            return "(空)"
        return "\n".join(f"{stamp}  {' '.join(sql.split())[:200]}" for stamp, sql in items)

    def _on_sql_change(self, _event=None) -> None:
        self._update_caret()
        if self._highlight_sql is not None and self.sql.get("1.0", "end-1c") != self._highlight_sql:
            self._clear_result_highlight()
        self._schedule_inspect()

    def _schedule_inspect(self) -> None:
        if self._inspect_job is not None:
            self.after_cancel(self._inspect_job)
        self._inspect_job = self.after(280, self._inspect_now)

    def _inspect_now(self) -> None:
        self._inspect_job = None
        sql = self.sql.get("1.0", "end-1c")
        try:
            diagnostics = self.session.inspect(sql)
        except Exception:
            diagnostics = []
        self._diagnostics = diagnostics
        self.sql.tag_remove("sql-error", "1.0", tk.END)
        for item in diagnostics:
            try:
                self.sql.tag_add("sql-error", item.start, item.end)
            except tk.TclError:
                continue
        try:
            self.sql.tag_raise("sql-error")
        except tk.TclError:
            pass
        self._render_inspect_bar(diagnostics)

    def _render_inspect_bar(self, diagnostics: list[Diagnostic]) -> None:
        self.inspect_text.configure(state=tk.NORMAL)
        self.inspect_text.delete("1.0", tk.END)
        if not diagnostics:
            self.inspect_wrap.pack_forget()
            self.inspect_text.configure(state=tk.DISABLED)
            self._hide_tip()
            return
        lines = [f"发现 {len(diagnostics)} 个错误（点击可跳转到源码）"]
        lines.extend(item.message for item in diagnostics[:8])
        self.inspect_text.insert("1.0", "\n".join(lines))
        self.inspect_text.configure(state=tk.DISABLED)
        self.inspect_text.configure(height=min(6, 1 + len(diagnostics)))
        if not self.inspect_wrap.winfo_ismapped():
            self.inspect_wrap.pack(side=tk.BOTTOM, fill=tk.X, before=self.sql_wrap)

    def _jump_inspect(self, _event=None) -> str:
        if not self._diagnostics:
            return "break"
        item = self._diagnostics[0]
        try:
            index = self.inspect_text.index(f"@{_event.x},{_event.y}") if _event else "1.0"
            line = int(index.split(".")[0])
            if line >= 2:
                item = self._diagnostics[min(line - 2, len(self._diagnostics) - 1)]
        except (tk.TclError, ValueError, AttributeError):
            item = self._diagnostics[0]
        self.sql.mark_set(tk.INSERT, item.start)
        self.sql.see(item.start)
        self.sql.focus_set()
        return "break"

    def _on_sql_motion(self, event) -> None:
        try:
            index = self.sql.index(f"@{event.x},{event.y}")
        except tk.TclError:
            self._hide_tip()
            return
        hit = None
        for item in self._diagnostics:
            try:
                if self.sql.compare(index, ">=", item.start) and self.sql.compare(index, "<", item.end):
                    hit = item
                    break
            except tk.TclError:
                continue
        if hit is None:
            self._hide_tip()
            return
        self._show_tip(event, hit.message)

    def _show_tip(self, event, text: str) -> None:
        if self._tip is not None:
            for child in self._tip.winfo_children():
                if isinstance(child, tk.Label) and child.cget("text") == text:
                    return
            self._hide_tip()
        tip = tk.Toplevel(self)
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            tip, text=text, justify=tk.LEFT,
            wraplength=min(480, max(240, self.winfo_screenwidth() // 3)),
            background="#fff3cd", foreground="#5c1a1a",
            font=("Segoe UI", 9), relief=tk.SOLID, borderwidth=1,
            padx=8, pady=6,
        ).pack()
        x = event.x_root + 12
        y = event.y_root + 18
        tip.geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _update_caret(self, _event=None) -> None:
        line, col = self.sql.index(tk.INSERT).split(".")
        self.st_pos.configure(text=f"Ln {line}, Col {int(col) + 1}")

    def _about(self) -> None:
        messagebox.showinfo(
            "关于 MiniSQL Studio",
            "课程 MiniSQL 的桌面客户端。\n\n"
            "支持 compiler / database 两种模式：compiler 编译并提交 CREATE，\n"
            "database 才真正执行 INSERT / SELECT / UPDATE / DELETE / JOIN / GROUP BY。\n"
            "可查看 Tokens / AST / Plan / OptPlan；计划树会展开 child、left、right。\n\n"
            "快捷键：F9 或 Ctrl+Enter 执行当前查询；Ctrl+N 新建查询；Ctrl+W 关闭查询。\n"
            "点击下方 Result 页签会跳转到产生该结果的语句，并用黄色高亮标出。\n"
            "对象树中右键表名可打开表查看数据，也可在新查询中打开。\n"
            "输入时会像 IDE 一样标红错误并在编辑器下方给出提示。空值显示为 NULL。",
            parent=self,
        )

    def _on_close(self) -> None:
        if self._inspect_job is not None:
            self.after_cancel(self._inspect_job)
        self._hide_tip()
        try:
            self.session.close()
        except Exception:
            pass
        self.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.studio")
    parser.add_argument("--data-dir", default=None, help="启动后自动连接该数据目录")
    parser.add_argument("--mode", choices=("database", "compiler"), default="database")
    args = parser.parse_args(argv)
    _enable_dpi()
    app = StudioApp()
    if args.data_dir:
        app.dir_var.set(args.data_dir)
        if args.mode == "compiler":
            app.mode_box.current(1)
        app.connect()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
