"""Tk information panel. No Tk root, thread, device, or window is created at import."""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk, messagebox
from typing import Any, Callable

from .backend import PanelBackend
from .actions import PanelActions


class InformationPanel(ttk.Frame):
    """Local control window; Agent requests use a separate one-shot human dialog."""

    _FONT_FAMILY = "Microsoft YaHei UI"

    _COLORS = {
        "page": "#f3f6fb", "surface": "#ffffff", "ink": "#172033",
        "muted": "#64748b", "line": "#dbe3ef", "accent": "#315ee8",
        "accent_active": "#254bc4", "console": "#111827",
        "console_ink": "#d1fae5", "mock_bg": "#fff3d6", "mock_ink": "#7a4a00",
        "live_bg": "#e3f7ed", "live_ink": "#11633e",
    }

    def __init__(self, root: tk.Tk, backend: PanelBackend,
                 confirm: Callable[[str, str], bool] | None = None,
                 confirm_agent: Callable[[dict[str, Any]], bool] | None = None):
        self._configure_styles(root)
        super().__init__(root, padding=8, style="Panel.TFrame")
        self.root = root
        self.backend = backend
        self.actions = PanelActions(
            backend, confirm or (lambda title, prompt: messagebox.askyesno(
                title, prompt, parent=self.root
            )),
        )
        self._initial_fields = False
        self._closed = False
        self._agent_confirmation_id: str | None = None
        self._confirm_agent = confirm_agent or self._confirm_agent_dialog
        self._poll_token: str | None = None
        self.pack(fill="both", expand=True)

        root.title("ESP32 Codex 控制面板")
        root.geometry("700x700")
        root.minsize(660, 700)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self._values: dict[str, tk.StringVar] = {}
        for key in ("workspace", "profile", "source", "policy", "device", "ports", "busy"):
            self._values[key] = tk.StringVar(master=root, value="读取中")

        self._make_header()
        self._make_status()
        self._make_console()
        self._make_controls()

        footer = ttk.Frame(self, style="Panel.TFrame")
        footer.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        footer.columnconfigure(0, weight=1)
        self.error_var = tk.StringVar(master=root, value="")
        ttk.Label(footer, textvariable=self.error_var, wraplength=500,
                  style="Error.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.refresh_button = ttk.Button(footer, text="刷新状态", command=self.refresh,
                                         style="Accent.TButton")
        self.refresh_button.grid(row=0, column=1, sticky="e")
        root.protocol("WM_DELETE_WINDOW", self.close)

    def _confirm_agent_dialog(self, item: dict[str, Any]) -> bool:
        prompt = (
            f"目标：{item.get('target', '未提供目标')}\n"
            f"影响与参数：{item.get('impact', '未提供说明')}\n"
            f"工作区：{item.get('workspacePath') or '未选择'}\n"
            f"机型/入口：{item.get('profile', '未知')} / {item.get('entry', '无')}\n"
            f"代次：{item.get('control_epoch')}；策略 revision：{item.get('policy_revision')}\n"
            f"有效期：约 {max(0, int(item.get('expires_in_ms', 0)) // 1000)} 秒\n\n"
            "仅批准这一次 Agent 请求。拒绝或关闭不会执行。"
        )
        return messagebox.askyesno(
            str(item.get("title") or "Codex 请求操作"), prompt,
            parent=self.root, icon="warning",
        )

    def _review_agent_confirmations(self, rows: object) -> None:
        pending = next((item for item in rows if isinstance(item, dict)
                        and item.get("state") == "pending"), None) if isinstance(rows, list) else None
        if pending is None:
            self._agent_confirmation_id = None
            return
        confirmation_id = pending.get("id")
        if not isinstance(confirmation_id, str) or confirmation_id == self._agent_confirmation_id:
            return
        self._agent_confirmation_id = confirmation_id
        try:
            approve = self._confirm_agent(pending) is True
            result = self.backend.resolve_agent_confirmation(confirmation_id, approve=approve)
            if result.get("ok") is not True:
                self.error_var.set("确认已过期或工作区状态已变化；操作未执行。")
            elif not approve:
                self.error_var.set("已拒绝 Agent 请求；操作未执行。")
            else:
                self.error_var.set("已批准这一笔 Agent 请求；等待 MCP 返回结果。")
        except Exception:
            self.error_var.set("确认状态不可用；操作未执行。")
        finally:
            self._agent_confirmation_id = None

    def _configure_styles(self, root: tk.Tk) -> None:
        for name in (
            "TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont",
            "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont",
            "TkIconFont", "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(name, root=root).configure(
                    family=self._FONT_FAMILY, size=10
                )
            except tk.TclError:
                continue
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        c = self._COLORS
        root.configure(background=c["page"])
        style.configure("Panel.TFrame", background=c["page"])
        style.configure("Surface.TFrame", background=c["surface"])
        style.configure("Panel.TLabel", background=c["page"], foreground=c["ink"], font=(self._FONT_FAMILY, 10))
        style.configure("Muted.TLabel", background=c["page"], foreground=c["muted"], font=(self._FONT_FAMILY, 10))
        style.configure("Title.TLabel", background=c["page"], foreground=c["ink"], font=(self._FONT_FAMILY, 20, "bold"))
        style.configure("Eyebrow.TLabel", background=c["page"], foreground=c["muted"], font=(self._FONT_FAMILY, 9, "bold"))
        style.configure("Card.TLabelframe", background=c["surface"], bordercolor=c["line"], relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=c["surface"], foreground=c["muted"], font=(self._FONT_FAMILY, 10, "bold"))
        style.configure("CardValue.TLabel", background=c["surface"], foreground=c["ink"], font=(self._FONT_FAMILY, 12, "bold"))
        style.configure("CardDetail.TLabel", background=c["surface"], foreground=c["muted"], font=(self._FONT_FAMILY, 10))
        style.configure("MockBadge.TLabel", background=c["mock_bg"], foreground=c["mock_ink"], padding=(10, 5), font=(self._FONT_FAMILY, 10, "bold"))
        style.configure("LiveBadge.TLabel", background=c["live_bg"], foreground=c["live_ink"], padding=(10, 5), font=(self._FONT_FAMILY, 10, "bold"))
        style.configure("Error.TLabel", background=c["page"], foreground="#b42318", font=(self._FONT_FAMILY, 10))
        style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff", borderwidth=0, padding=(14, 6), font=(self._FONT_FAMILY, 10, "bold"))
        style.map("Accent.TButton", background=[("active", c["accent_active"]), ("disabled", "#9aa9cf")])
        style.configure("TButton", foreground=c["ink"], padding=(11, 5), font=(self._FONT_FAMILY, 10))
        style.map("TButton", background=[("active", "#e8eefb")])
        style.configure("TNotebook", background=c["page"], borderwidth=0)
        style.configure("TNotebook.Tab", background="#e8edf5", foreground=c["muted"], padding=(14, 6), font=(self._FONT_FAMILY, 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", c["surface"])], foreground=[("selected", c["accent"])])
        style.configure("TLabel", foreground=c["ink"], font=(self._FONT_FAMILY, 10))
        style.configure("TEntry", padding=4, fieldbackground="#ffffff", font=(self._FONT_FAMILY, 10))
        style.configure("TCombobox", padding=4, fieldbackground="#ffffff", font=(self._FONT_FAMILY, 10))
        style.configure("TCheckbutton", background=c["surface"], foreground=c["ink"], font=(self._FONT_FAMILY, 10))

    def _make_header(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(0, weight=1)
        title = ttk.Frame(header, style="Panel.TFrame")
        title.grid(row=0, column=0, sticky="w")
        ttk.Label(title, text="ESP32  /  CODEX", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(title, text="设备控制面板", style="Title.TLabel").pack(anchor="w", pady=(1, 0))
        ttk.Label(title, text="本地连接 · 状态与操作", style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        self.source_badge = ttk.Label(header, text="正在读取来源…", style="MockBadge.TLabel")
        self.source_badge.grid(row=0, column=1, sticky="e", padx=(12, 0), pady=(8, 0))
        ttk.Label(header, textvariable=self._values["source"], style="Muted.TLabel",
                  wraplength=230, justify="right").grid(
            row=1, column=1, sticky="e", padx=(12, 0), pady=(5, 0)
        )

    def _make_status(self) -> None:
        status = ttk.Frame(self, style="Panel.TFrame")
        status.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        status.columnconfigure(0, weight=1, uniform="status")
        status.columnconfigure(1, weight=1, uniform="status")
        workspace = ttk.LabelFrame(status, text="当前工作区", style="Card.TLabelframe", padding=8)
        workspace.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        ttk.Label(workspace, textvariable=self._values["workspace"], style="CardValue.TLabel", wraplength=300).pack(anchor="w")
        ttk.Label(workspace, textvariable=self._values["profile"], style="CardDetail.TLabel", wraplength=300).pack(anchor="w", pady=(5, 0))
        ttk.Label(workspace, text="操作策略", style="CardDetail.TLabel").pack(anchor="w", pady=(9, 0))
        ttk.Label(workspace, textvariable=self._values["policy"], style="CardValue.TLabel").pack(anchor="w")
        device = ttk.LabelFrame(status, text="设备连接", style="Card.TLabelframe", padding=8)
        device.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ttk.Label(device, textvariable=self._values["device"], style="CardValue.TLabel", wraplength=300).pack(anchor="w")
        ttk.Label(device, textvariable=self._values["ports"], style="CardDetail.TLabel", wraplength=300).pack(anchor="w", pady=(5, 0))
        ttk.Label(device, textvariable=self._values["busy"], style="CardDetail.TLabel").pack(anchor="w", pady=(7, 0))

    def _make_console(self) -> None:
        heading = ttk.Frame(self, style="Panel.TFrame")
        heading.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        heading.columnconfigure(0, weight=1)
        ttk.Label(heading, text="串口控制台", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(heading, text="只读", style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        console_group = ttk.Frame(self)
        console_group.grid(row=3, column=0, sticky="nsew")
        console_group.rowconfigure(0, weight=1)
        console_group.columnconfigure(0, weight=1)
        self.console = tk.Text(console_group, height=3, wrap="word", state="disabled",
                               background=self._COLORS["console"], foreground=self._COLORS["console_ink"],
                               insertbackground="#ffffff", selectbackground=self._COLORS["accent"],
                               relief="flat", borderwidth=0, padx=12, pady=8,
                               font=(self._FONT_FAMILY, 10), highlightthickness=1,
                               highlightbackground=self._COLORS["line"], highlightcolor=self._COLORS["accent"])
        scrollbar = ttk.Scrollbar(console_group, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=scrollbar.set)
        self.console.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def _make_controls(self) -> None:
        tabs = ttk.Notebook(self)
        tabs.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        device = ttk.Frame(tabs, padding=8, style="Surface.TFrame")
        workspace = ttk.Frame(tabs, padding=8, style="Surface.TFrame")
        tabs.add(device, text="设备操作")
        tabs.add(workspace, text="工作区与策略")
        device.columnconfigure(1, weight=1)
        device.columnconfigure(2, weight=1)
        self.port_var = tk.StringVar(master=self.root, value="")
        self.port_select = ttk.Combobox(device, textvariable=self.port_var, state="readonly", width=14)
        ttk.Label(device, text="串口").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.port_select.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(device, text="连接", command=lambda: self._do_control("connect"),
                   style="Accent.TButton").grid(row=0, column=2, sticky="ew", padx=(0, 6))
        ttk.Button(device, text="断开", command=lambda: self._do_control("disconnect")).grid(row=0, column=3, sticky="ew")
        action_buttons = ttk.Frame(device, style="Surface.TFrame")
        action_buttons.grid(row=1, column=0, columnspan=4, sticky="w", pady=(3, 3))
        for index, (caption, operation) in enumerate((
            ("运行", "run"), ("中断", "interrupt"), ("停止", "stop")
        )):
            ttk.Button(action_buttons, text=caption, command=lambda op=operation: self._do_control(op)).grid(
                row=0, column=index, padx=(0, 7)
            )
        self.repl_var = tk.StringVar(master=self.root, value="")
        ttk.Label(device, text="REPL 单行").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(device, textvariable=self.repl_var, width=35).grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(0, 8)
        )
        ttk.Button(device, text="发送", command=lambda: self._do_control("send"),
                   style="Accent.TButton").grid(row=2, column=3, sticky="ew")
        self.file_var = tk.StringVar(master=self.root, value="")
        self.run_after_var = tk.BooleanVar(master=self.root, value=False)
        ttk.Label(device, text="下载 .py").grid(row=3, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(device, textvariable=self.file_var, width=22).grid(row=3, column=1, sticky="ew", padx=(0, 8))
        ttk.Checkbutton(device, text="校验后运行", variable=self.run_after_var).grid(row=3, column=2, sticky="w")
        ttk.Button(device, text="下载", command=self._do_download,
                   style="Accent.TButton").grid(row=3, column=3, sticky="ew")

        workspace.columnconfigure(1, weight=1)
        workspace.columnconfigure(2, weight=1)
        self.workspace_var = tk.StringVar(master=self.root, value="")
        self.profile_var = tk.StringVar(master=self.root, value="generic")
        self.label_var = tk.StringVar(master=self.root, value="")
        self.entry_var = tk.StringVar(master=self.root, value="/main.py")
        self.policy_var = tk.StringVar(master=self.root, value="confirm-write")
        ttk.Label(workspace, text="工作区目录").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self.workspace_select = ttk.Combobox(workspace, textvariable=self.workspace_var, state="normal")
        self.workspace_select.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=5)
        ttk.Button(workspace, text="选择", command=lambda: self._do_workspace("select"),
                   style="Accent.TButton").grid(row=0, column=3, sticky="ew", padx=(0, 6))
        ttk.Button(workspace, text="识别", command=self._discover_workspaces).grid(row=0, column=4, sticky="ew")
        ttk.Label(workspace, text="机型").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(workspace, textvariable=self.profile_var, state="readonly",
                     values=("generic", "hiwonder"), width=12).grid(row=1, column=1, sticky="w", pady=5)
        ttk.Label(workspace, text="标签 / 入口").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(workspace, textvariable=self.label_var, width=18).grid(row=2, column=1, sticky="ew", padx=(0, 8), pady=5)
        ttk.Entry(workspace, textvariable=self.entry_var, width=15).grid(row=2, column=2, sticky="ew", padx=(0, 8), pady=5)
        ttk.Button(workspace, text="认领", command=lambda: self._do_workspace("claim"),
                   style="Accent.TButton").grid(row=2, column=3, sticky="ew")
        ttk.Label(workspace, text="操作策略").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Combobox(workspace, textvariable=self.policy_var, state="readonly",
                     values=("auto", "confirm-write", "confirm-all"), width=17).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Button(workspace, text="应用策略", command=self._do_policy).grid(row=3, column=2, sticky="ew")

    @staticmethod
    def _workspace_summary(path: Any) -> str:
        """Keep status-card paths to one readable line; the editor keeps the full path."""
        if not isinstance(path, str) or not path:
            return "（尚未选择工作区）"
        if len(path) <= 38:
            return path
        parts = [part for part in path.replace("/", "\\").split("\\") if part]
        tail = "\\".join(parts[-2:]) if parts else path
        return "…\\" + tail[-34:]

    def _discover_workspaces(self) -> None:
        try:
            listed = self.backend.discover_workspaces(self.workspace_var.get())
            candidates = [item["workspacePath"] for item in listed.get("workspaces", [])
                          if isinstance(item, dict) and isinstance(item.get("workspacePath"), str)]
            self.workspace_select.configure(values=candidates)
            self.error_var.set(f"只读识别到 {len(candidates)} 个工作区；请选择后再明确切换。")
        except Exception as exc:
            self.error_var.set(f"工作区识别失败：{str(exc)[:200]}")

    def _show_action_result(self, result: dict[str, Any], command: str) -> None:
        self.refresh()
        if result.get("ok") and command == "select":
            self.workspace_var.set(result.get("workspacePath", ""))
            self.profile_var.set(result.get("profile", "generic"))
            self.entry_var.set(result.get("entry", "/main.py"))
            self.policy_var.set(self.backend.current_policy())
        elif result.get("ok") and command == "policy_set":
            self.policy_var.set(result.get("policy", "confirm-write"))
        if not result.get("ok"):
            self.error_var.set(str(result.get("error") or "操作未执行")[:300])
        elif command in {"stop", "interrupt", "disconnect"}:
            self.error_var.set("桥响应已返回；不能据此确认实体运动已停止。")
        else:
            self.error_var.set(f"{command} 已返回结果；请检查状态和模拟标记。")

    def _run_action(self, command: str, operation: Callable[[], dict[str, Any]]) -> None:
        try:
            result = operation()
        except Exception as exc:
            self.error_var.set(f"{command} 未执行：{str(exc)[:240]}")
            return
        self._show_action_result(result, command)

    def _do_control(self, command: str) -> None:
        self._run_action(command, lambda: self.actions.control(
            command, port=self.port_var.get(), line=self.repl_var.get()
        ))

    def _do_download(self) -> None:
        self._run_action("download", lambda: self.actions.download(
            self.file_var.get(), run=self.run_after_var.get()
        ))

    def _do_workspace(self, action: str) -> None:
        self._run_action(action, lambda: self.actions.workspace(
            action, self.workspace_var.get(), profile=self.profile_var.get(),
            label=self.label_var.get() or None, entry=self.entry_var.get() or None,
        ))

    def _do_policy(self) -> None:
        self._run_action("policy_set", lambda: self.actions.policy(self.policy_var.get()))

    def refresh(self) -> bool:
        if self._closed:
            return False
        try:
            snapshot = self.backend.snapshot()
        except Exception as exc:
            self.error_var.set(f"读取失败：{str(exc)[:200]}")
            return False
        self.error_var.set("")
        workspace: dict[str, Any] = snapshot.get("workspace") or {}
        info = workspace.get("info") or {}
        status: dict[str, Any] = snapshot.get("status") or {}
        ports: dict[str, Any] = snapshot.get("ports") or {}
        policy: dict[str, Any] = snapshot.get("policy") or {}
        console: dict[str, Any] = snapshot.get("console") or {}
        self._review_agent_confirmations(snapshot.get("agentConfirmations"))
        simulated = snapshot.get("simulated") is True
        self._values["workspace"].set(self._workspace_summary(workspace.get("workspacePath")))
        profile = info.get("profileLabel") or workspace.get("profile") or "未知机型"
        entry = info.get("entry") or "（尚未指定入口）"
        self._values["profile"].set(f"{profile} · {entry}")
        source = snapshot.get("source") or "unknown"
        self._values["source"].set(f"{source} / {'模拟数据，不是实体设备' if simulated else '桥进程数据'}")
        self.source_badge.configure(
            text="MOCK  ·  模拟数据" if simulated else "LIVE  ·  桥进程数据",
            style="MockBadge.TLabel" if simulated else "LiveBadge.TLabel",
        )
        self._values["policy"].set(
            f"{policy.get('policy', '未知')} · {policy.get('source', 'default')}"
        )
        if status.get("ok") is False:
            self._values["device"].set(f"状态读取失败：{status.get('error', '未知错误')}")
        else:
            connected = status.get("connected") is True
            prefix = "模拟" if simulated else "桥报告"
            self._values["device"].set(
                f"{prefix}{'已连接' if connected else '未连接'} · {status.get('port') or '无端口'}"
            )
        if ports.get("ok") is False:
            self._values["ports"].set(f"获取失败：{ports.get('error', '未知错误')}")
        else:
            rows = ports.get("ports") or []
            self._values["ports"].set(
                "、".join(str(row.get("device")) for row in rows if isinstance(row, dict))
                or "（无可用端口）"
            )
        self._values["busy"].set("忙碌" if status.get("busy") is True else "空闲 / 未确认")
        rows = ports.get("ports") or []
        available = [str(row.get("device")) for row in rows if isinstance(row, dict) and row.get("device")]
        self.port_select.configure(values=available)
        if self.port_var.get() not in available:
            self.port_var.set(available[0] if available else "")
        if not self._initial_fields:
            self.workspace_var.set(workspace.get("workspacePath") or "")
            self.profile_var.set(workspace.get("profile") or "generic")
            self.entry_var.set(info.get("entry") or "/main.py")
            self.policy_var.set(policy.get("policy") or "confirm-write")
            self._initial_fields = True
        chunk = console.get("text", "")
        if console.get("dropped"):
            self._rewrite_console("（前序控制台数据已被缓冲区丢弃）\n")
        if isinstance(chunk, str) and chunk:
            self._append_console(chunk)
        return True

    def _rewrite_console(self, text: str) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.insert("end", text)
        self.console.configure(state="disabled")

    def _append_console(self, text: str) -> None:
        self.console.configure(state="normal")
        self.console.insert("end", text)
        if int(self.console.index("end-1c").split(".")[0]) > 700:
            self.console.delete("1.0", "200.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    def start_polling(self, interval_ms: int = 1500) -> None:
        if self._closed:
            return
        def tick() -> None:
            self._poll_token = None
            if not self._closed:
                self.refresh()
                self._poll_token = self.root.after(interval_ms, tick)
        if self._poll_token is None:
            self._poll_token = self.root.after(interval_ms, tick)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_token is not None:
            self.root.after_cancel(self._poll_token)
            self._poll_token = None
        try:
            self.backend.close()
        finally:
            self.root.destroy()


def create_information_panel(
    root: tk.Tk, backend: PanelBackend, *, auto_refresh: bool = True,
    confirm: Callable[[str, str], bool] | None = None,
    confirm_agent: Callable[[dict[str, Any]], bool] | None = None,
) -> InformationPanel:
    panel = InformationPanel(root, backend, confirm=confirm, confirm_agent=confirm_agent)
    panel.refresh()
    if auto_refresh:
        panel.start_polling()
    return panel
