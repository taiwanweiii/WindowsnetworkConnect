"""tkinter GUI:網卡清單、分流規則編輯、即時連線記錄。"""

import queue
import tkinter as tk
from tkinter import messagebox, ttk

from . import config as cfg
from . import sysproxy
from .interfaces import list_interfaces
from .proxy import ProxyServer
from .rules import DEFAULT_INTERFACE, Rule

MAX_LOG_LINES = 500


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("Network Router — TCP 分流器")
        root.geometry("900x700")
        root.minsize(760, 560)

        self.config = cfg.load()
        self.engine = cfg.engine_from_config(self.config)
        self.proxy: ProxyServer | None = None
        self.events: queue.Queue[dict] = queue.Queue()

        self._build_top()
        self._build_interfaces()
        self._build_rules()
        self._build_log()

        self.refresh_interfaces()
        self.refresh_rules()
        self.root.after(200, self._drain_events)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 介面建構 ----------

    def _build_top(self):
        # 第一排:監聽位址 / 啟動 / 狀態
        row1 = ttk.Frame(self.root, padding=(8, 8, 8, 2))
        row1.pack(fill="x")

        ttk.Label(row1, text="監聽位址").pack(side="left")
        self.port_var = tk.StringVar(
            value=f"{self.config['listen_host']}:{self.config['listen_port']}"
        )
        ttk.Entry(row1, textvariable=self.port_var, width=20).pack(
            side="left", padx=(4, 12)
        )

        self.start_btn = ttk.Button(row1, text="啟動 Proxy", command=self.toggle)
        self.start_btn.pack(side="left")

        self.status_var = tk.StringVar(value="● 停止")
        self.status_lbl = ttk.Label(row1, textvariable=self.status_var, foreground="red")
        self.status_lbl.pack(side="left", padx=12)

        # 第二排:系統 proxy 開關 / 預設出口
        row2 = ttk.Frame(self.root, padding=(8, 2, 8, 4))
        row2.pack(fill="x")

        # 啟動時順便把系統 proxy 指過來,Chrome / VS Code 不用各自設定
        self.sysproxy_var = tk.BooleanVar(value=self.config.get("set_system_proxy", False))
        self._sysproxy_active = False
        ttk.Checkbutton(
            row2, text="同時設定系統 Proxy", variable=self.sysproxy_var
        ).pack(side="left", padx=(0, 16))

        ttk.Label(row2, text="未命中規則時走").pack(side="left")
        self.default_iface_var = tk.StringVar(value=self.engine.default_interface)
        self.default_iface_combo = ttk.Combobox(
            row2, textvariable=self.default_iface_var, width=18, state="readonly"
        )
        self.default_iface_combo.pack(side="left", padx=(4, 0))
        self.default_iface_combo.bind("<<ComboboxSelected>>", self._on_default_iface)

    def _build_interfaces(self):
        frame = ttk.LabelFrame(self.root, text="網路介面 (出口選項)", padding=8)
        frame.pack(fill="x", padx=8, pady=4)

        self.iface_list = tk.Listbox(frame, height=4)
        self.iface_list.pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="重新整理", command=self.refresh_interfaces).pack(
            side="left", padx=8
        )

    def _build_rules(self):
        frame = ttk.LabelFrame(
            self.root, text="分流規則 (由上而下,第一條符合者生效)", padding=8
        )
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        columns = ("host", "port", "process", "interface", "note")
        headers = ("目標主機", "Port", "程式", "出口介面", "備註")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=7)
        for col, header in zip(columns, headers):
            self.tree.heading(col, text=header)
            self.tree.column(col, width=140 if col == "host" else 100)
        self.tree.pack(fill="both", expand=True)

        self.host_var = tk.StringVar(value="*")
        self.port_rule_var = tk.StringVar(value="0")
        self.process_var = tk.StringVar(value="*")
        self.iface_var = tk.StringVar(value=DEFAULT_INTERFACE)
        self.note_var = tk.StringVar()

        # 第一排:輸入欄位
        edit = ttk.Frame(frame)
        edit.pack(fill="x", pady=(8, 0))

        for label, var, width in (
            ("主機", self.host_var, 22),
            ("Port", self.port_rule_var, 6),
        ):
            ttk.Label(edit, text=label).pack(side="left")
            ttk.Entry(edit, textvariable=var, width=width).pack(side="left", padx=(2, 8))

        # 程式名稱用下拉選:選項來自實際經過 proxy 的程式 (也可手動輸入樣式)
        ttk.Label(edit, text="程式").pack(side="left")
        self.seen_processes: set[str] = set()
        self.process_combo = ttk.Combobox(
            edit, textvariable=self.process_var, width=16, values=["*"]
        )
        self.process_combo.pack(side="left", padx=(2, 2))
        ttk.Button(edit, text="掃描", width=5, command=self.scan_processes).pack(
            side="left", padx=(0, 8)
        )

        ttk.Label(edit, text="介面").pack(side="left")
        self.iface_combo = ttk.Combobox(
            edit, textvariable=self.iface_var, width=16, state="readonly"
        )
        self.iface_combo.pack(side="left", padx=(2, 8))

        ttk.Label(edit, text="備註").pack(side="left")
        ttk.Entry(edit, textvariable=self.note_var, width=10).pack(
            side="left", padx=(2, 0)
        )

        # 第二排:操作按鈕
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(6, 0))

        ttk.Button(actions, text="➕ 新增規則", command=self.add_rule).pack(side="left")
        ttk.Button(actions, text="刪除選取", command=self.delete_rule).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="上移", command=lambda: self.move_rule(-1)).pack(side="left")
        ttk.Button(actions, text="下移", command=lambda: self.move_rule(1)).pack(
            side="left", padx=6
        )

    def _build_log(self):
        frame = ttk.LabelFrame(self.root, text="即時連線", padding=8)
        frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self.log = tk.Text(frame, height=10, state="disabled", font=("Consolas", 9))
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("err", foreground="red")
        self.log.tag_configure("routed", foreground="blue")

    # ---------- 動作 ----------

    def toggle(self):
        if self.proxy and self.proxy.running:
            self.proxy.stop()
            self.proxy = None
            self._restore_sysproxy()
            self.status_var.set("● 停止")
            self.status_lbl.configure(foreground="red")
            self.start_btn.configure(text="啟動 Proxy")
            return

        try:
            host, _, port_str = self.port_var.get().partition(":")
            port = int(port_str or 8080)
            self.config["listen_host"] = host or "127.0.0.1"
            self.config["listen_port"] = port
            self.proxy = ProxyServer(
                self.engine, host=host or "127.0.0.1", port=port,
                on_event=self.events.put,
            )
            self.proxy.start()
        except (OSError, ValueError) as exc:
            msg = str(exc)
            # macOS: Errno 48 / Windows: WinError 10048 = port 被占用
            if getattr(exc, "errno", None) in (48, 98, 10048):
                msg += (
                    f"\n\nPort {port} 已被其他程式占用。"
                    "\n把上面的監聽位址改成別的 port 再啟動,"
                    "\n例如 127.0.0.1:18080"
                    "\n(VS Code 的 http.proxy 也要跟著改)"
                )
            messagebox.showerror("啟動失敗", msg)
            self.proxy = None
            return

        if self.sysproxy_var.get():
            try:
                sysproxy.enable(self.config["listen_host"], port)
                self._sysproxy_active = True
            except OSError as exc:
                messagebox.showwarning(
                    "系統 Proxy 設定失敗",
                    f"Proxy 已啟動,但系統 Proxy 沒設成功:\n{exc}\n\n"
                    "可以手動到系統設定把 Proxy 指到 "
                    f"{self.config['listen_host']}:{port}",
                )

        self.config["set_system_proxy"] = self.sysproxy_var.get()
        cfg.save(cfg.config_from_engine(self.config, self.engine))
        status = f"● 運行中 {self.config['listen_host']}:{port}"
        if self._sysproxy_active:
            status += "(系統 Proxy 已接管)"
        self.status_var.set(status)
        self.status_lbl.configure(foreground="green")
        self.start_btn.configure(text="停止 Proxy")

    def _restore_sysproxy(self):
        if not self._sysproxy_active:
            return
        try:
            sysproxy.disable()
        except OSError as exc:
            messagebox.showwarning(
                "還原系統 Proxy 失敗",
                f"{exc}\n\n請手動到系統設定把 Proxy 關掉,否則會沒網路。",
            )
        self._sysproxy_active = False

    def _on_default_iface(self, _event=None):
        self.engine.default_interface = self.default_iface_var.get()
        cfg.save(cfg.config_from_engine(self.config, self.engine))

    def refresh_interfaces(self):
        self.interfaces = list_interfaces()
        self.iface_list.delete(0, "end")
        for name, ip in self.interfaces.items():
            self.iface_list.insert("end", f"{name}  —  {ip}")
        options = [DEFAULT_INTERFACE, *self.interfaces.keys()]
        self.iface_combo["values"] = options
        self.default_iface_combo["values"] = options
        # 之前選的預設出口網卡如果不見了(例如手機拔掉),退回 default
        if self.default_iface_var.get() not in options:
            self.default_iface_var.set(DEFAULT_INTERFACE)
            self._on_default_iface()

    def scan_processes(self):
        import platform

        from .process_lookup import list_network_processes
        found = list_network_processes()
        self.seen_processes.update(found)
        self.process_combo["values"] = ["*", *sorted(self.seen_processes)]
        if not found and platform.system() == "Darwin":
            messagebox.showinfo(
                "掃描不到程式",
                "macOS 需要 root 權限才能看到其他程式的連線\n"
                "(用 sudo python3 main.py 啟動才掃得到)。\n\n"
                "Windows 上不需要管理員權限,直接就能掃到。",
            )

    def refresh_rules(self):
        self.tree.delete(*self.tree.get_children())
        for i, rule in enumerate(self.engine.rules):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(rule.host, rule.port or "任意", rule.process,
                        rule.interface, rule.note),
            )

    def add_rule(self):
        try:
            port = int(self.port_rule_var.get() or 0)
        except ValueError:
            messagebox.showerror("錯誤", "Port 必須是數字 (0 = 任意)")
            return
        self.engine.rules.append(Rule(
            host=self.host_var.get().strip() or "*",
            port=port,
            process=self.process_var.get().strip() or "*",
            interface=self.iface_var.get() or DEFAULT_INTERFACE,
            note=self.note_var.get().strip(),
        ))
        self._save_rules()

    def delete_rule(self):
        selected = self.tree.selection()
        for iid in sorted(selected, key=int, reverse=True):
            del self.engine.rules[int(iid)]
        self._save_rules()

    def move_rule(self, delta: int):
        selected = self.tree.selection()
        if len(selected) != 1:
            return
        i = int(selected[0])
        j = i + delta
        if not 0 <= j < len(self.engine.rules):
            return
        rules = self.engine.rules
        rules[i], rules[j] = rules[j], rules[i]
        self._save_rules()
        self.tree.selection_set(str(j))

    def _save_rules(self):
        self.refresh_rules()
        cfg.save(cfg.config_from_engine(self.config, self.engine))

    # ---------- 連線記錄 ----------

    def _drain_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                self._append_log(event)
        except queue.Empty:
            pass
        self.root.after(200, self._drain_events)

    def _append_log(self, event: dict):
        # 把實際看到的程式名收進「程式」下拉選單
        proc = event.get("process")
        if proc and proc != "?" and proc not in self.seen_processes:
            self.seen_processes.add(proc)
            self.process_combo["values"] = ["*", *sorted(self.seen_processes)]
        if event["ok"]:
            mark = "→" if event["interface"] != DEFAULT_INTERFACE else "·"
            line = (f"{event['time']} {mark} [{event['process']}] "
                    f"{event['host']}:{event['port']} ({event['protocol']}) "
                    f"走 {event['interface']}\n")
            tag = "routed" if event["interface"] != DEFAULT_INTERFACE else ""
        else:
            line = (f"{event['time']} ✗ [{event['process']}] "
                    f"{event['host']}:{event['port']} 失敗: {event['error']}\n")
            tag = "err"
        self.log.configure(state="normal")
        self.log.insert("end", line, tag)
        if int(self.log.index("end-1c").split(".")[0]) > MAX_LOG_LINES:
            self.log.delete("1.0", "2.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self):
        if self.proxy:
            self.proxy.stop()
        self._restore_sysproxy()
        cfg.save(cfg.config_from_engine(self.config, self.engine))
        self.root.destroy()


def run():
    root = tk.Tk()
    App(root)
    root.mainloop()
