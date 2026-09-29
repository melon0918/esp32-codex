"""Local pywebview hosts for the static and read-only mock panel prototypes."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys
import threading
import time
from datetime import datetime, timezone


TITLE = "ESP32 Codex 网页面板 · UI-2"
READONLY_TITLE = "ESP32 Codex 网页面板 · 只读 MOCK"
OPERATIONS_TITLE = "ESP32 Codex 网页面板 · 操作 MOCK"
COMPACT_TITLE = "ESP32 工作台"
ROOT = Path(__file__).resolve().parents[2]
PROTOTYPE = ROOT / "web-panel" / "prototype"
SERVER_DIR = ROOT / "mcp-server"
def webview2_runtime_dirs() -> list[str]:
    if os.name != "nt":
        return []
    system_drive = os.environ.get("SystemDrive") or Path(sys.executable).drive or "C:"
    system_root = Path(system_drive + "\\")
    candidates: list[Path] = []
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or str(system_root / "Program Files (x86)")
    program_files = os.environ.get("ProgramFiles") or str(system_root / "Program Files")
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    for base in (program_files_x86, program_files, local_app_data):
        candidates.append(Path(base) / "Microsoft" / "EdgeWebView" / "Application")
    found: list[str] = []
    for candidate in candidates:
        try:
            if candidate.is_dir():
                found.extend(
                    str(candidate / item.name)
                    for item in candidate.iterdir()
                    if item.is_dir() and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", item.name)
                )
        except OSError:
            continue
    return sorted(set(found))


def dependency_report() -> dict[str, object]:
    try:
        version = importlib.metadata.version("pywebview")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pywebviewAvailable": importlib.util.find_spec("webview") is not None,
        "pywebviewVersion": version,
        "webview2RuntimeDirs": webview2_runtime_dirs(),
        "observedWebView2RuntimeVersions": [Path(path).name for path in webview2_runtime_dirs()],
        "httpServer": False,
        "guiCreated": False,
    }


def load_inline_page() -> str:
    """Read the trusted UI-1 files and produce one self-contained HTML string."""
    html = (PROTOTYPE / "index.html").read_text(encoding="utf-8")
    css = (PROTOTYPE / "styles.css").read_text(encoding="utf-8")
    js = (PROTOTYPE / "script.js").read_text(encoding="utf-8")

    stylesheet = re.compile(r'<link\s+rel="stylesheet"\s+href="styles\.css"\s*>')
    script_ref = re.compile(r'<script\s+src="script\.js"\s+defer\s*>\s*</script>')
    html, css_count = stylesheet.subn(f"<style>\n{css}\n</style>", html, count=1)
    html, js_count = script_ref.subn("", html, count=1)
    if css_count != 1 or js_count != 1:
        raise RuntimeError("Expected one local stylesheet and one local script in UI-1 index.html")
    if re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", html, flags=re.IGNORECASE):
        raise RuntimeError("The local prototype contains a non-local resource reference")
    if "</script" in js.lower():
        raise RuntimeError("Cannot inline a script containing an HTML script closing tag")
    # An inline script in <head> runs immediately; the original external script
    # had defer. Place it at the end of <body> so UI-1 can bind its DOM controls.
    if html.count("</body>") != 1:
        raise RuntimeError("Expected one closing body tag in UI-1 index.html")
    html = html.replace("</body>", f"<script>\n{js}\n</script>\n</body>", 1)

    host_markup = """
      <section id="ui2-host-card" aria-label="UI-2 宿主消息验证">
        <div class="ui2-host-heading"><span class="ui2-host-mark">↔</span><div><strong>窗口宿主联调</strong><small>仅验证页面与 Python 的本地消息</small></div><span class="ui2-host-mock">MOCK</span></div>
        <div class="ui2-host-row"><span>页面 → Python</span><strong id="ui2-ping-result">等待页面就绪…</strong></div>
        <div class="ui2-host-row"><span>Python → 页面</span><strong id="ui2-state-result">等待宿主消息…</strong></div>
        <button id="ui2-ping-button" type="button" disabled>发送 ping <span>→</span></button>
      </section>
      <style>
        #ui2-host-card { margin: 14px 0 0; padding: 13px 15px; border: 1px solid #e8e5f4; border-radius: 12px; background: #fff; box-shadow: 0 3px 10px rgb(38 34 84 / 4%); }
        .ui2-host-heading { display: flex; align-items: center; gap: 9px; margin-bottom: 8px; }
        .ui2-host-mark { display: grid; width: 29px; height: 29px; place-items: center; border-radius: 9px; background: #f0edff; color: #6857d8; font-size: 17px; }
        .ui2-host-heading div { display: flex; min-width: 0; flex: 1; flex-direction: column; }
        .ui2-host-heading strong { color: #393a50; font-size: 12px; }
        .ui2-host-heading small { color: #686d80; font-size: 11px; }
        .ui2-host-mock { padding: 3px 7px; border-radius: 12px; background: #f3f0ff; color: #6752cf; font-size: 10px; font-weight: 800; }
        .ui2-host-row { display: flex; justify-content: space-between; gap: 10px; padding: 5px 0; border-top: 1px solid #f0eff5; color: #686d80; font-size: 11px; }
        .ui2-host-row strong { color: #4f5066; font-size: 11px; font-weight: 600; text-align: right; }
        #ui2-ping-button { min-height: 30px; margin-top: 7px; padding: 0 10px; border: 1px solid #e4e0f7; border-radius: 8px; background: #faf9ff; color: #5c4bc3; font-size: 11px; font-weight: 650; }
        #ui2-ping-button:disabled { opacity: .55; cursor: default; }
      </style>
    """
    host_script = """
      <script>
        (() => {
          const pingResult = document.getElementById('ui2-ping-result');
          const stateResult = document.getElementById('ui2-state-result');
          const button = document.getElementById('ui2-ping-button');
          window.__ui2HostReady = false;
          window.__ui2ReceiveState = (state) => {
            if (!state || state.source !== 'local-python-demo') return;
            stateResult.textContent = state.message;
          };
          async function sendPing(kind) {
            button.disabled = true;
            try {
              const reply = await window.pywebview.api.ping(kind);
              pingResult.textContent = reply.message;
            } catch (error) {
              pingResult.textContent = '宿主消息失败';
            } finally {
              button.disabled = false;
            }
          }
          button.addEventListener('click', () => sendPing('button'));
          window.addEventListener('pywebviewready', () => {
            window.__ui2HostReady = true;
            sendPing('startup');
          }, { once: true });
        })();
      </script>
    """
    html = html.replace("<footer class=\"page-footer\">", host_markup + "\n<footer class=\"page-footer\">", 1)
    html = html.replace("</body>", host_script + "\n</body>", 1)
    return html


def readonly_markup() -> str:
    """Add a read-only data surface without changing the accepted UI-1 prototype."""
    return r"""
      <section id="live-snapshot" aria-label="共享后端只读快照">
        <div class="live-head">
          <div><p class="eyebrow">LIVE READ ONLY</p><h2>共享状态快照</h2></div>
          <span class="live-source" id="live-source">读取中</span>
        </div>
        <div class="live-grid">
          <article><span>工作区</span><strong id="live-workspace">读取中…</strong><small id="live-profile"></small></article>
          <article><span>操作策略</span><strong id="live-policy">读取中…</strong><small id="live-policy-source"></small></article>
          <article><span>设备状态</span><strong id="live-status">读取中…</strong><small id="live-device"></small></article>
          <article><span>可见串口</span><strong id="live-ports">读取中…</strong><small>仅展示端口；不会自动连接</small></article>
        </div>
        <div class="live-console-head"><strong>共享控制台</strong><span id="live-console-meta"></span></div>
        <pre id="live-console">正在读取有界控制台快照…</pre>
        <div class="live-foot"><span id="live-refresh-state">页面只读；打开时不会连接设备</span><button id="live-refresh" type="button">刷新快照</button></div>
      </section>
      <style>
        #prototype-demo { margin: 0 0 20px; border: 1px solid #e9e5f1; border-radius: 11px; background: #f7f6fa; }
        #prototype-demo > summary { padding: 11px 14px; color:#57556d; font-size:12px; font-weight:700; cursor:pointer; }
        #prototype-demo > summary::marker { color:#796bc5; }
        #prototype-demo[open] > summary { border-bottom:1px solid #e9e5f1; }
        #prototype-demo > main { padding-top:14px; }
        #prototype-demo .demo-label { margin:0 0 12px; padding:9px 11px; border-radius:8px; background:#fff3d7; color:#755515; font-size:12px; line-height:1.5; }
      </style>
      <p class="demo-label">以下 UI-1 页面与按钮只是静态视觉演示，不代表当前共享后端状态；实时信息请以上方“共享状态快照”为准。</p>
      <style>
        #live-snapshot { margin: 14px 0 18px; padding: 17px 18px; border: 1px solid #ddd9ef; border-radius: 14px; background: linear-gradient(145deg,#fff 30%,#f8f7ff); box-shadow: 0 5px 18px rgb(38 34 84 / 5%); }
        .live-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:12px; }
        .live-head .eyebrow { margin:0 0 3px; color:#77718f; font-size:10px; letter-spacing:.12em; }
        .live-head h2 { margin:0; color:#29283b; font-size:18px; }
        .live-source { flex:none; border-radius:20px; padding:5px 9px; background:#eeeaff; color:#5544ba; font-size:11px; font-weight:750; }
        .live-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
        .live-grid article { min-width:0; padding:9px 11px; border:1px solid #efedf6; border-radius:10px; background:#fff; }
        .live-grid article > span,.live-grid small { display:block; color:#76778a; font-size:11px; }
        .live-grid strong { display:block; margin:4px 0 2px; color:#37364b; font-size:12px; overflow-wrap:anywhere; }
        .live-grid small { overflow-wrap:anywhere; }
        .live-console-head { display:flex; justify-content:space-between; gap:8px; margin:13px 0 6px; color:#454458; font-size:12px; }
        .live-console-head span { color:#858497; }
        #live-console { box-sizing:border-box; max-height:150px; min-height:56px; overflow:auto; margin:0; padding:10px 11px; border-radius:9px; background:#20212e; color:#e7e6f1; font:12px/1.55 Consolas,"Microsoft YaHei UI",monospace; white-space:pre-wrap; overflow-wrap:anywhere; }
        .live-foot { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:10px; color:#77778a; font-size:11px; }
        #live-refresh { flex:none; border:1px solid #e5e1f3; border-radius:8px; padding:6px 10px; background:#fff; color:#5544ba; font:inherit; cursor:pointer; }
        #live-snapshot.live-error { border-color:#f2cfcc; background:#fffafa; }
        #live-snapshot.live-error .live-source { background:#fff0ee; color:#a34138; }
        @media (max-width:520px) { #live-snapshot { padding:13px; } .live-grid { grid-template-columns:1fr; } }
      </style>
    """


def readonly_script() -> str:
    """Page code consumes a single fixed, no-argument snapshot method."""
    return r"""
      <script>
        (() => {
          const root = document.getElementById('live-snapshot');
          const byId = (id) => document.getElementById(id);
          const put = (id, value) => { byId(id).textContent = String(value ?? '—'); };
          function render(data) {
            root.classList.toggle('live-error', Boolean(data.error));
            put('live-source', data.simulated ? 'MOCK · 模拟数据' : '真实桥 · 只读');
            put('live-workspace', data.workspace.path || '未选择工作区');
            put('live-profile', [data.workspace.label, data.workspace.profile, data.workspace.entry].filter(Boolean).join(' · '));
            put('live-policy', data.policy.name || '不可用');
            put('live-policy-source', data.policy.source || '默认策略');
            put('live-status', data.status.connected ? '已连接' : '未连接');
            put('live-device', [data.status.port, data.status.firmware, data.status.busy ? '忙碌' : '空闲'].filter(Boolean).join(' · ') || '暂无设备信息');
            put('live-ports', data.ports.length ? data.ports.map((port) => port.device).join('、') : '未发现串口');
            put('live-console-meta', `${data.console.text.length} 字符 · 游标 ${data.console.cursor}`);
            put('live-console', data.console.text || '暂无控制台输出');
            put('live-refresh-state', data.error ? `读取失败：${data.error}` : (data.console.dropped ? '较早控制台内容已滚动移出缓冲区' : '页面只读；打开时不会连接设备'));
          }
          async function refresh() {
            const button = byId('live-refresh');
            button.disabled = true;
            try { render(await window.pywebview.api.get_snapshot()); }
            catch (_) { root.classList.add('live-error'); put('live-source','本地宿主不可用'); put('live-refresh-state','无法读取共享状态；可稍后重试'); }
            finally { button.disabled = false; }
          }
          byId('live-refresh').addEventListener('click', refresh);
          window.addEventListener('pywebviewready', () => {
            refresh();
            window.setInterval(refresh, 3000);
          }, { once: true });
        })();
      </script>
    """


def load_readonly_page() -> str:
    page = load_inline_page()
    page = page.replace('<main>', readonly_markup() + '\n<main>', 1)
    page = page.replace('<main>', '<details id="prototype-demo"><summary>展开静态界面演示（与当前后端数据隔离）</summary>\n<main>', 1)
    page = page.replace('</main>', '</main>\n</details>', 1)
    page = page.replace('</body>', readonly_script() + '\n</body>', 1)
    return page


def load_operations_page() -> str:
    """Add the UI-4 controls only to the mock operations host, not the read-only host."""
    page = load_readonly_page()
    page = page.replace('<details id="prototype-demo">', operations_markup() + '\n<details id="prototype-demo">', 1)
    return page.replace('</body>', operations_script() + '\n</body>', 1)


def load_compact_operations_page() -> str:
    """Inline the reviewed compact page and its live, fixed-API adapter."""
    html = (PROTOTYPE / "compact.html").read_text(encoding="utf-8")
    css = (PROTOTYPE / "compact.css").read_text(encoding="utf-8")
    js = (PROTOTYPE / "compact.js").read_text(encoding="utf-8")
    live_js = (PROTOTYPE / "compact_live.js").read_text(encoding="utf-8")

    html, css_count = re.subn(
        r'<link\s+rel="stylesheet"\s+href="compact\.css"\s*/?>',
        "<style>\n" + css + "\n</style>", html, count=1, flags=re.I,
    )
    html, js_count = re.subn(
        r'<script\s+src="compact\.js"\s*>\s*</script>',
        "<script>\n" + js + "\n</script>", html, count=1, flags=re.I,
    )
    if css_count != 1 or js_count != 1:
        raise RuntimeError("Compact panel must have exactly one local CSS and JS reference")
    if html.count("</body>") != 1 or "</script" in live_js.lower():
        raise RuntimeError("Compact panel host script cannot be safely inlined")
    if re.search(r"(?:src|href)\s*=\s*['\"](?:https?:)?//", html, flags=re.I):
        raise RuntimeError("Compact panel resources must remain local and inline")
    return html.replace("</body>", "<script>\n" + live_js + "\n</script>\n</body>", 1)


def operations_markup() -> str:
    """UI-4 controls; every value is displayed as text, never trusted HTML."""
    return r'''
      <section id="ui4-operations" aria-label="共享后端操作面板">
        <div class="live-head"><div><p class="eyebrow">MOCK ACTIONS · UI-4</p><h2>操作与确认</h2></div><span class="live-source">仅模拟</span></div>
        <div class="ui4-section">
          <h3>工作区</h3>
          <label>扫描目录 <input id="ui4-root" type="text" autocomplete="off" placeholder="输入本机绝对目录"></label>
          <button id="ui4-discover" type="button">发现工作区</button>
          <div id="ui4-workspaces" role="list"></div>
          <div class="ui4-inline"><label>认领配置 <select id="ui4-profile"><option value="generic">通用 ESP32</option><option value="hiwonder">幻尔小车</option></select></label>
            <label>名称 <input id="ui4-label" maxlength="128" value="我的 ESP32 项目"></label>
            <label>入口 <input id="ui4-entry" maxlength="240" value="/main.py"></label>
            <button id="ui4-claim" type="button">认领输入目录</button></div>
        </div>
        <div class="ui4-section">
          <h3>策略与设备</h3>
          <label>操作策略 <select id="ui4-policy"><option value="confirm-write">confirm-write · 写入确认</option><option value="confirm-all">confirm-all · 全部确认</option><option value="auto">auto · 自动执行</option></select></label>
          <button id="ui4-set-policy" type="button">更新策略</button>
          <label>串口 <select id="ui4-port"><option value="">刷新后选择</option></select></label>
          <div class="ui4-actions">
            <button data-ui4-action="connect">连接</button><button data-ui4-action="disconnect">断开</button>
            <button data-ui4-action="run">运行入口</button><button data-ui4-action="interrupt">中断</button><button data-ui4-action="stop">停止</button>
          </div>
          <label>单行 REPL <input id="ui4-repl" maxlength="512" autocomplete="off" placeholder="输入一行 MicroPython"></label>
          <button id="ui4-send" type="button">发送 REPL 行</button>
        </div>
        <div class="ui4-section">
          <h3>下载工作区程序</h3>
          <label>文件名 <input id="ui4-filename" maxlength="255" value="main.py"></label>
          <div class="ui4-actions"><button data-ui4-action="download">严格备份后下载</button><button data-ui4-action="download_run">严格备份、下载并运行</button></div>
        </div>
        <p id="ui4-action-status" role="status" aria-live="polite">连接默认 mock broker 后可操作；页面打开不会自动连接。</p>
      </section>
      <div id="ui4-confirm" hidden>
        <section role="dialog" aria-modal="true" aria-labelledby="ui4-confirm-title">
          <p class="eyebrow">确认本次操作</p><h2 id="ui4-confirm-title"></h2>
          <div><strong>目标</strong><p id="ui4-confirm-target"></p></div>
          <div><strong>影响</strong><p id="ui4-confirm-impact"></p></div>
          <p>拒绝或关闭不会执行本次操作。确认仅作用于当前这一笔面板操作。</p>
          <button id="ui4-confirm-reject" type="button">取消</button><button id="ui4-confirm-approve" type="button">确认操作</button>
        </section>
      </div>
      <div id="ui4-agent-confirm" hidden>
        <section role="dialog" aria-modal="true" aria-labelledby="ui4-agent-confirm-title">
          <p class="eyebrow">Codex 操作 · 等待面板用户确认</p><h2 id="ui4-agent-confirm-title"></h2>
          <div><strong>目标</strong><p id="ui4-agent-confirm-target"></p></div>
          <div><strong>影响与参数</strong><p id="ui4-agent-confirm-impact"></p></div>
          <div><strong>工作区上下文</strong><p id="ui4-agent-confirm-context"></p></div>
          <p id="ui4-agent-confirm-expiry"></p>
          <p>确认只批准上面这一笔请求。拒绝、过期或上下文改变都不会执行。</p>
          <button id="ui4-agent-confirm-reject" type="button">拒绝</button><button id="ui4-agent-confirm-approve" type="button">批准本次操作</button>
        </section>
      </div>
      <style>
        #ui4-operations{margin:14px 0 18px;padding:17px 18px;border:1px solid #ddd9ef;border-radius:14px;background:#fff;box-shadow:0 5px 18px rgb(38 34 84 / 5%)}
        .ui4-section{margin-top:13px;padding-top:12px;border-top:1px solid #efedf6}.ui4-section h3{margin:0 0 9px;color:#37364b;font-size:13px}
        #ui4-operations label{display:inline-flex;align-items:center;gap:7px;margin:4px 10px 4px 0;color:#626277;font-size:12px}
        #ui4-operations input,#ui4-operations select{box-sizing:border-box;max-width:100%;min-height:32px;padding:5px 8px;border:1px solid #dedbea;border-radius:7px;background:#fff;color:#37364b;font:inherit}
        #ui4-operations button,#ui4-confirm button{min-height:32px;margin:4px 5px 4px 0;padding:5px 11px;border:1px solid #e2def1;border-radius:8px;background:#faf9ff;color:#5146a8;font:inherit;font-size:12px;cursor:pointer}
        #ui4-operations button:disabled{opacity:.55;cursor:wait}.ui4-actions{display:flex;flex-wrap:wrap;gap:3px}.ui4-inline{margin-top:8px}
        #ui4-workspaces{display:grid;gap:5px;margin-top:8px}.ui4-workspace{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:7px 9px;border:1px solid #eeecf4;border-radius:8px;overflow-wrap:anywhere;font-size:11px}
        #ui4-action-status{color:#69687d;font-size:12px;line-height:1.5}.ui4-error{color:#a34138!important}
        #ui4-confirm[hidden]{display:none}#ui4-confirm{position:fixed;z-index:9999;inset:0;display:grid;place-items:center;padding:20px;background:rgb(23 22 38 / 48%)}
        #ui4-confirm>section{box-sizing:border-box;width:min(520px,100%);padding:24px;border:1px solid #e2def0;border-radius:16px;background:#fff;box-shadow:0 18px 60px rgb(23 22 38 / 25%)}
        #ui4-confirm h2{margin:0 0 16px;color:#29283b;font-size:20px}#ui4-confirm p{overflow-wrap:anywhere;white-space:pre-wrap;color:#626277;font-size:13px;line-height:1.55}
        #ui4-confirm strong{color:#39384d;font-size:12px}#ui4-confirm-approve{background:#5c4bc3!important;color:#fff!important}
        #ui4-agent-confirm[hidden]{display:none}#ui4-agent-confirm{position:fixed;z-index:10000;inset:0;display:grid;place-items:center;padding:20px;background:rgb(23 22 38 / 58%)}
        #ui4-agent-confirm>section{box-sizing:border-box;width:min(560px,100%);max-height:90vh;overflow:auto;padding:24px;border:1px solid #e2def0;border-radius:16px;background:#fff;box-shadow:0 18px 60px rgb(23 22 38 / 25%)}
        #ui4-agent-confirm h2{margin:0 0 16px;color:#29283b;font-size:20px}#ui4-agent-confirm p{overflow-wrap:anywhere;white-space:pre-wrap;color:#626277;font-size:13px;line-height:1.55}
        #ui4-agent-confirm strong{color:#39384d;font-size:12px}#ui4-agent-confirm button{min-height:36px;margin:4px 5px 4px 0;padding:6px 12px;border:1px solid #e2def1;border-radius:8px;background:#faf9ff;color:#5146a8;font:inherit;font-size:12px;cursor:pointer}
        #ui4-agent-confirm-approve{background:#5c4bc3!important;color:#fff!important}
      </style>
    '''


def operations_script() -> str:
    """UI-4 client uses named actions and strict payloads; approval is a no-arg UI event."""
    return r'''
      <script>
        (() => {
          const api = () => window.pywebview.api;
          const $ = (id) => document.getElementById(id);
          const status = $('ui4-action-status');
          const dialog = $('ui4-confirm');
          const agentDialog = $('ui4-agent-confirm');
          let busy = false;
          let activeAgentConfirmation = null;
          let snapshotTimer = null;
          const put = (id, value) => { $(id).textContent = String(value ?? ''); };
          function setStatus(message, error=false) { put('ui4-action-status',message); status.classList.toggle('ui4-error',error); }
          function setBusy(value) { busy=value; document.querySelectorAll('#ui4-operations button').forEach((b)=>{b.disabled=value;}); }
          function payloadFor(action) {
            if (action==='connect') return {port:$('ui4-port').value};
            if (action==='repl') return {line:$('ui4-repl').value};
            if (action==='workspace_select') return {path:$('ui4-root').value};
            if (action==='workspace_claim') return {path:$('ui4-root').value,profile:$('ui4-profile').value,label:$('ui4-label').value,entry:$('ui4-entry').value};
            if (action==='policy_set') return {policy:$('ui4-policy').value};
            if (action==='download'||action==='download_run') return {filename:$('ui4-filename').value};
            return {};
          }
          async function perform(action) {
            if (busy) return;
            setBusy(true); setStatus('正在处理面板操作…');
            try {
              const result=await api().perform(action,payloadFor(action));
              render(result.snapshot);
              setStatus(result.ok ? result.message : result.error, !result.ok);
            } catch (_) { setStatus('操作失败；请刷新共享状态后重试。',true); }
            finally { dialog.hidden=true; setBusy(false); }
          }
          function render(data) {
            if (!data || data.error) return;
            const ports=$('ui4-port'); const current=ports.value;
            ports.replaceChildren(new Option('选择串口…',''));
            (data.ports||[]).forEach((p)=>ports.add(new Option(p.device,p.device)));
            if ([...ports.options].some((o)=>o.value===current)) ports.value=current;
            $('ui4-policy').value=(data.policy&&data.policy.name)||'confirm-write';
            if (data.workspace&&data.workspace.path) $('ui4-root').value=data.workspace.path;
            renderAgentConfirmations(data.agentConfirmations||[]);
          }
          function renderAgentConfirmations(items) {
            const item=Array.isArray(items)?items.find((row)=>row&&row.state==='pending'):null;
            if(!item){activeAgentConfirmation=null;agentDialog.hidden=true;return;}
            if(activeAgentConfirmation!==item.id){
              activeAgentConfirmation=item.id;
              put('ui4-agent-confirm-title',item.title||item.action||'Codex 请求操作');
              put('ui4-agent-confirm-target',item.target||'未提供目标');
              put('ui4-agent-confirm-impact',item.impact||'未提供影响说明');
              put('ui4-agent-confirm-context',`${item.workspacePath||'未选择工作区'} · ${item.profile||'未知机型'} · ${item.entry||'无入口'} · epoch ${item.control_epoch} · policy ${item.policy_revision||'missing'}`);
              agentDialog.hidden=false;
              $('ui4-agent-confirm-approve').focus();
            }
            put('ui4-agent-confirm-expiry',`确认有效期剩余约 ${Math.max(0,Math.ceil((item.expires_in_ms||0)/1000))} 秒`);
          }
          async function refreshOperationsSnapshot(){
            try{render(await api().get_snapshot());}
            catch(_){setStatus('共享状态不可用。',true);}
          }
          async function resolveAgentConfirmation(approve){
            const id=activeAgentConfirmation;
            if(!id)return;
            const approveButton=$('ui4-agent-confirm-approve');
            const rejectButton=$('ui4-agent-confirm-reject');
            approveButton.disabled=true;rejectButton.disabled=true;
            try{
              const result=approve?await api().approve_agent_confirmation(id):await api().reject_agent_confirmation(id);
              if(result&&result.ok){
                setStatus(approve?'面板用户已批准本次请求；正在等待 MCP 返回结果。':'面板用户已拒绝本次请求；未执行。',!approve);
                activeAgentConfirmation=null;agentDialog.hidden=true;
              }else{
                setStatus('该确认已过期或上下文已变化；未执行。',true);
                activeAgentConfirmation=null;agentDialog.hidden=true;
              }
              await refreshOperationsSnapshot();
            }catch(_){
              setStatus('确认状态不可用；未执行。',true);
              activeAgentConfirmation=null;agentDialog.hidden=true;
            }finally{approveButton.disabled=false;rejectButton.disabled=false;}
          }
          window.__ui4ShowConfirmation=(value)=>{
            put('ui4-confirm-title',value.title); put('ui4-confirm-target',value.target); put('ui4-confirm-impact',value.impact); dialog.hidden=false; $('ui4-confirm-approve').focus();
          };
          $('ui4-confirm-approve').addEventListener('click',async()=>{try{await api().approve_current_operation();}catch(_){dialog.hidden=true;setStatus('确认请求已失效；未执行。',true);}});
          $('ui4-confirm-reject').addEventListener('click',async()=>{try{await api().reject_current_operation();}finally{dialog.hidden=true;setStatus('已取消；未执行。');}});
          dialog.addEventListener('click',(event)=>{if(event.target===dialog)$('ui4-confirm-reject').click();});
          $('ui4-agent-confirm-approve').addEventListener('click',()=>resolveAgentConfirmation(true));
          $('ui4-agent-confirm-reject').addEventListener('click',()=>resolveAgentConfirmation(false));
          agentDialog.addEventListener('click',(event)=>{if(event.target===agentDialog)resolveAgentConfirmation(false);});
          document.addEventListener('keydown',(event)=>{
            if(event.key==='Escape'&&!agentDialog.hidden)resolveAgentConfirmation(false);
            else if(event.key==='Escape'&&!dialog.hidden)$('ui4-confirm-reject').click();
          });
          $('ui4-discover').addEventListener('click',async()=>{
            if(busy)return; setBusy(true); setStatus('正在扫描本地工作区…');
            try {
              const found=await api().discover_workspaces($('ui4-root').value);
              const list=$('ui4-workspaces'); list.replaceChildren();
              found.workspaces.forEach((item)=>{const row=document.createElement('div');row.className='ui4-workspace';row.setAttribute('role','listitem');const name=document.createElement('span');name.textContent=`${item.workspacePath} · ${item.profile} · ${item.entry}`;const choose=document.createElement('button');choose.textContent='选择';choose.addEventListener('click',()=>performWithCurrentBusy('workspace_select',{path:item.workspacePath}));row.append(name,choose);list.append(row);});
              setStatus(`发现 ${found.workspaces.length} 个工作区${found.truncated?'；结果已截断':''}`);
            } catch (_) {setStatus('扫描失败；请检查目录并确认使用本机绝对路径。',true);}
            finally {setBusy(false);}
          });
          async function performWithCurrentBusy(action,payload){
            if(busy)return; setBusy(true); setStatus('正在处理面板操作…');
            try{const result=await api().perform(action,payload);render(result.snapshot);setStatus(result.ok?result.message:result.error,!result.ok);}
            catch(_){setStatus('操作失败；请刷新共享状态后重试。',true);}finally{dialog.hidden=true;setBusy(false);}
          }
          $('ui4-claim').addEventListener('click',()=>perform('workspace_claim'));
          $('ui4-set-policy').addEventListener('click',()=>perform('policy_set'));
          $('ui4-send').addEventListener('click',()=>perform('repl'));
          document.querySelectorAll('[data-ui4-action]').forEach((button)=>button.addEventListener('click',()=>perform(button.dataset.ui4Action)));
          // The UI-1 rail is always visible, while its sample pages are folded.
          // When folded, navigate to actual live sections instead of silently
          // selecting an invisible static sample page.
          document.addEventListener('click',(event)=>{
            const link=event.target.closest('.rail-link[data-view],.brand-mark');
            if(!link||$('prototype-demo').open)return;
            const view=link.dataset.view||'overview';
            const target={overview:'live-snapshot',workspace:'ui4-operations',console:'live-console'}[view];
            if(!target)return;
            event.preventDefault();event.stopImmediatePropagation();
            document.querySelectorAll('.rail-link[data-view]').forEach((item)=>{
              const active=item.dataset.view===view;
              item.classList.toggle('is-active',active);
              if(active)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');
            });
            put('crumb-current',{overview:'工作台',workspace:'工作区',console:'控制台'}[view]);
            $(target).scrollIntoView({block:view==='console'?'center':'start'});
          },true);
          // This script runs late in the document; pywebviewready may already
          // have fired. Initialize exactly once in either case.
          let initialized=false;
          function initializeOperations(){
            if(initialized||!window.pywebview?.api)return;
            initialized=true;
            refreshOperationsSnapshot();
            snapshotTimer=window.setInterval(()=>{if(!busy)refreshOperationsSnapshot();},1000);
          }
          window.addEventListener('pywebviewready',initializeOperations,{once:true});
          initializeOperations();
        })();
      </script>
    '''


def _short_text(value: object, limit: int = 1024) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def public_snapshot(
    raw: dict[str, object], *, simulated: bool, include_confirmations: bool = False,
) -> dict[str, object]:
    """Allowlist bounded display fields; never forward backend metadata or credentials."""
    workspace = raw.get("workspace") if isinstance(raw.get("workspace"), dict) else {}
    workspace_info = workspace.get("info") if isinstance(workspace.get("info"), dict) else {}
    status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
    policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    ports_obj = raw.get("ports") if isinstance(raw.get("ports"), dict) else {}
    console = raw.get("console") if isinstance(raw.get("console"), dict) else {}
    ports = ports_obj.get("ports") if isinstance(ports_obj.get("ports"), list) else []
    safe_ports = []
    for item in ports[:32]:
        if isinstance(item, dict):
            device = _short_text(item.get("device"), 128)
            if device:
                safe_ports.append({"device": device})
    result = {
        "simulated": bool(simulated),
        "workspace": {
            "path": _short_text(workspace.get("workspacePath"), 2048),
            "profile": _short_text(workspace.get("profile"), 64),
            "label": _short_text(workspace_info.get("profileLabel") or workspace_info.get("label"), 128),
            "entry": _short_text(workspace_info.get("entry"), 256),
        },
        "policy": {
            "name": _short_text(policy.get("policy"), 64),
            "source": _short_text(policy.get("source"), 256),
        },
        "status": {
            "connected": status.get("connected") is True,
            "port": _short_text(status.get("port"), 128),
            "firmware": _short_text(status.get("firmware"), 128),
            "busy": status.get("busy") is True,
        },
        "ports": safe_ports,
        "console": {
            "text": _short_text(console.get("text"), 12000),
            "cursor": console.get("cursor") if type(console.get("cursor")) is int and console.get("cursor") >= 0 else 0,
            "dropped": console.get("dropped") is True,
        },
    }
    if include_confirmations:
        rows = raw.get("agentConfirmations")
        safe = []
        for item in rows[:4] if isinstance(rows, list) else []:
            if not isinstance(item, dict) or item.get("state") != "pending":
                continue
            confirmation_id = item.get("id")
            if not isinstance(confirmation_id, str) or not 20 <= len(confirmation_id) <= 128:
                continue
            safe.append({
                "id": confirmation_id,
                "state": "pending",
                "action": _short_text(item.get("action"), 80),
                "effect": _short_text(item.get("effect"), 24),
                "title": _short_text(item.get("title"), 256),
                "target": _short_text(item.get("target"), 2048),
                "impact": _short_text(item.get("impact"), 4096),
                "workspacePath": _short_text(item.get("workspacePath"), 2048),
                "profile": _short_text(item.get("profile"), 32),
                "entry": _short_text(item.get("entry"), 256),
                "control_epoch": item.get("control_epoch") if type(item.get("control_epoch")) is int else -1,
                "policy_revision": _short_text(item.get("policy_revision"), 64),
                "expires_in_ms": item.get("expires_in_ms") if type(item.get("expires_in_ms")) is int else 0,
            })
        result["agentConfirmations"] = safe
    return result


class ReadOnlyApi:
    """Expose only a bounded snapshot read; no RPC, device, file, or credential API."""

    def __init__(self, backend: object):
        self._backend = backend
        self._lock = threading.Lock()

    def get_snapshot(self) -> dict[str, object]:
        with self._lock:
            try:
                raw = self._backend.snapshot()  # type: ignore[attr-defined]
                if not isinstance(raw, dict):
                    raise RuntimeError("backend returned an invalid snapshot")
                return public_snapshot(raw, simulated=bool(self._backend.simulated))  # type: ignore[attr-defined]
            except Exception:
                return {
                    "simulated": True,
                    # Bridge/workspace exception details can include user paths or
                    # transport diagnostics. The web page receives no raw detail.
                    "error": "共享状态暂时不可用",
                    "workspace": {"path": "", "profile": "", "label": "", "entry": ""},
                    "policy": {"name": "不可用", "source": ""},
                    "status": {"connected": False, "port": "", "firmware": "", "busy": False},
                    "ports": [],
                    "console": {"text": "", "cursor": 0, "dropped": False},
                }


class PanelOperationsApi(ReadOnlyApi):
    """Finite action surface bound to this user's configured shared bridge lease."""

    _ACTION_FIELDS = {
        "connect": {"port"}, "disconnect": set(), "run": set(), "interrupt": set(),
        "stop": set(), "repl": {"line"}, "workspace_select": {"path"},
        "workspace_claim": {"path", "profile", "label", "entry"},
        "policy_set": {"policy"}, "download": {"filename"}, "download_run": {"filename"},
    }
    _MESSAGES = {
        "connect": "已连接所选串口。",
        "disconnect": "已断开面板持有的连接。",
        "run": "已向设备发送运行请求。",
        "interrupt": "已发送中断请求；桥返回不等于物理停止确认。",
        "stop": "已发送停止请求；桥响应不代表电机实际停转。",
        "repl": "单行 REPL 请求已发送；请查看串口输出。",
        "workspace_select": "已切换共享工作区。",
        "workspace_claim": "已创建工作区配置。",
        "policy_set": "已更新工作区策略。",
        "download": "下载完成；严格备份与写入校验已通过。",
        "download_run": "下载与运行请求已完成；严格备份与写入校验已通过。",
    }
    _MOCK_MESSAGES = {
        "connect": "已连接模拟端口。",
        "disconnect": "已断开面板持有的模拟连接。",
        "run": "已向模拟器发送运行请求。",
        "interrupt": "已向模拟器发送中断请求；这不是实体设备状态确认。",
        "stop": "已向模拟器发送停止请求；这不是实体电机停转确认。",
        "repl": "单行 REPL 请求已由模拟器记录；代码未在实体设备执行。",
        "workspace_select": "已切换共享模拟工作区。",
        "workspace_claim": "已在模拟工作区创建配置。",
        "policy_set": "已更新模拟工作区策略。",
        "download": "模拟下载完成；已按夹具验证严格备份与校验结果。",
        "download_run": "模拟下载并运行完成；已按夹具验证严格备份与校验结果。",
    }
    _RESULT_FIELDS = {
        "connected", "interrupted", "stopped", "ran", "sent", "alreadyConnected",
        "alreadyDisconnected", "targetExisted", "backupVerified", "backupSize",
        "size", "compileOk", "policy", "profile", "workspacePath", "entry", "sha256",
    }

    def __init__(self, backend: object, window: object, *, confirmation_timeout: float = 120.0):
        super().__init__(backend)
        self._window = window
        self._confirmation_timeout = confirmation_timeout
        self._action_lock = threading.Lock()
        self._condition = threading.Condition()
        self._pending_confirmation: dict[str, str] | None = None
        self._resolution: bool | None = None
        self._closed = False
        self._assert_backend_ready()

    def _assert_backend_ready(self) -> None:
        bridge = getattr(self._backend, "bridge", None)
        if bridge is None:
            raise RuntimeError("panel bridge is unavailable")
        if bridge.mode == "mock":
            if not bridge.simulated:
                raise RuntimeError("mock panel requires an isolated mock bridge")
            return
        if bridge.mode != "bridge" or bridge.simulated:
            raise RuntimeError("real panel requires an explicitly configured real bridge")

    def get_snapshot(self) -> dict[str, object]:
        with self._lock:
            try:
                raw = self._backend.snapshot()  # type: ignore[attr-defined]
                if not isinstance(raw, dict):
                    raise RuntimeError("backend returned an invalid snapshot")
                return public_snapshot(
                    raw, simulated=bool(self._backend.simulated), include_confirmations=True  # type: ignore[attr-defined]
                )
            except Exception:
                return {
                    "simulated": True, "error": "共享状态暂时不可用",
                    "workspace": {"path": "", "profile": "", "label": "", "entry": ""},
                    "policy": {"name": "不可用", "source": ""},
                    "status": {"connected": False, "port": "", "firmware": "", "busy": False},
                    "ports": [], "console": {"text": "", "cursor": 0, "dropped": False},
                    "agentConfirmations": [],
                }

    def approve_agent_confirmation(self, confirmation_id: object) -> dict[str, object]:
        return self._resolve_agent_confirmation(confirmation_id, approve=True)

    def reject_agent_confirmation(self, confirmation_id: object) -> dict[str, object]:
        return self._resolve_agent_confirmation(confirmation_id, approve=False)

    def _resolve_agent_confirmation(self, confirmation_id: object, *, approve: bool) -> dict[str, object]:
        try:
            safe_id = self._safe_string(confirmation_id, "confirmation id", maximum=128)
            if not 20 <= len(safe_id) <= 128:
                raise ValueError("invalid confirmation id")
            result = self._backend.resolve_agent_confirmation(safe_id, approve=approve)  # type: ignore[attr-defined]
            return {
                "ok": result.get("ok") is True,
                "state": _short_text(result.get("state"), 32),
                "decisionSource": _short_text(result.get("decision_source"), 32),
            }
        except Exception:
            return {"ok": False, "state": "unavailable"}

    def _message(self, action: str) -> str:
        messages = self._MOCK_MESSAGES if self._backend.simulated else self._MESSAGES  # type: ignore[attr-defined]
        return messages[action]

    @staticmethod
    def _safe_string(value: object, name: str, *, minimum: int = 1, maximum: int = 2048) -> str:
        if not isinstance(value, str) or not minimum <= len(value) <= maximum:
            raise ValueError(f"invalid {name}")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError(f"invalid {name}")
        return value

    def discover_workspaces(self, root: object) -> dict[str, object]:
        self._assert_backend_ready()
        try:
            path = self._safe_string(root, "workspace root")
            if not Path(path).expanduser().is_absolute():
                raise ValueError("workspace root must be absolute")
            raw = self._backend.discover_workspaces(path)  # type: ignore[attr-defined]
            candidates = raw.get("workspaces") if isinstance(raw, dict) else None
            safe = []
            for item in candidates[:128] if isinstance(candidates, list) else []:
                if not isinstance(item, dict):
                    continue
                safe.append({
                    "workspacePath": _short_text(item.get("workspacePath"), 2048),
                    "profile": _short_text(item.get("profile"), 16),
                    "entry": _short_text(item.get("entry"), 240),
                    "profileLabel": _short_text(item.get("profileLabel"), 128),
                    "claimed": item.get("claimed") is True,
                })
            return {"workspaces": safe, "truncated": raw.get("truncated") is True}
        except Exception:
            raise ValueError("工作区扫描失败；请检查本机绝对目录") from None

    def _confirmation(self, title: str, body: str) -> bool:
        lines = body.splitlines()
        target = next((line.removeprefix("目标：") for line in lines if line.startswith("目标：")), "当前目标")
        impact = next((line.removeprefix("影响：") for line in lines if line.startswith("影响：")), "本次操作")
        summary = {"title": title[:256], "target": target[:2048], "impact": impact[:4096]}
        with self._condition:
            if self._closed or self._pending_confirmation is not None:
                return False
            self._resolution = None
            self._pending_confirmation = summary
        try:
            script = "window.__ui4ShowConfirmation(" + json.dumps(summary, ensure_ascii=True) + ")"
            self._window.evaluate_js(script)  # type: ignore[attr-defined]
        except Exception:
            self.reject_current_operation()
        deadline = time.monotonic() + self._confirmation_timeout
        with self._condition:
            while self._resolution is None and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            accepted = self._resolution is True and not self._closed
            self._resolution = None
            self._pending_confirmation = None
            return accepted

    def approve_current_operation(self) -> dict[str, bool]:
        """Resolve only the currently displayed host-created confirmation; no caller token/flag."""
        with self._condition:
            if self._pending_confirmation is None or self._closed or self._resolution is not None:
                return {"ok": False}
            self._resolution = True
            self._condition.notify_all()
            return {"ok": True}

    def reject_current_operation(self) -> dict[str, bool]:
        with self._condition:
            if self._pending_confirmation is None or self._resolution is not None:
                return {"ok": False}
            self._resolution = False
            self._condition.notify_all()
            return {"ok": True}

    def _validate_payload(self, action: object, payload: object) -> tuple[str, dict[str, object]]:
        if not isinstance(action, str) or action not in self._ACTION_FIELDS:
            raise ValueError("不支持的面板操作")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict) or set(payload) != self._ACTION_FIELDS[action]:
            raise ValueError("操作参数不符合固定字段要求")
        result = dict(payload)
        if action == "connect":
            result["port"] = self._safe_string(result["port"], "port", maximum=128)
        elif action == "repl":
            result["line"] = self._safe_string(result["line"], "REPL line", maximum=512)
            if "\n" in result["line"] or "\r" in result["line"] or not result["line"].strip():
                raise ValueError("REPL 只接受一条非空命令行")
        elif action in {"workspace_select", "workspace_claim"}:
            result["path"] = self._safe_string(result["path"], "workspace path")
            if not Path(result["path"]).expanduser().is_absolute():
                raise ValueError("工作区必须使用本机绝对路径")
            if action == "workspace_claim":
                if result.get("profile") not in {"generic", "hiwonder"}:
                    raise ValueError("不支持的工作区 profile")
                for field, maximum in (("label", 128), ("entry", 240)):
                    if field in result:
                        result[field] = self._safe_string(result[field], field, maximum=maximum)
                if "entry" in result and (not result["entry"].startswith("/") or ".." in result["entry"].split("/")):
                    raise ValueError("入口路径无效")
        elif action == "policy_set":
            if result["policy"] not in {"auto", "confirm-write", "confirm-all"}:
                raise ValueError("不支持的操作策略")
        elif action in {"download", "download_run"}:
            result["filename"] = self._safe_string(result["filename"], "filename", maximum=255)
        return action, result

    def perform(self, action: object, payload: object = None) -> dict[str, object]:
        """Execute a named PanelActions operation, never arbitrary broker RPC."""
        self._assert_backend_ready()
        try:
            name, fields = self._validate_payload(action, payload)
        except Exception as error:
            return {"ok": False, "error": str(error), "snapshot": self.get_snapshot()}
        if not self._action_lock.acquire(blocking=False):
            return {"ok": False, "error": "已有面板操作正在处理。", "snapshot": self.get_snapshot()}
        try:
            self._assert_backend_ready()
            from panel.actions import PanelActions
            actions = PanelActions(self._backend, self._confirmation)  # type: ignore[arg-type]
            if name in {"connect", "disconnect", "run", "interrupt", "stop"}:
                result = actions.control(name, **fields)
            elif name == "repl":
                result = actions.control("send", line=str(fields["line"]))
            elif name == "workspace_select":
                result = actions.workspace("select", str(fields["path"]))
            elif name == "workspace_claim":
                result = actions.workspace("claim", str(fields["path"]), profile=str(fields["profile"]),
                                           label=fields.get("label"), entry=fields.get("entry"))
            elif name == "policy_set":
                result = actions.policy(str(fields["policy"]))
            elif name in {"download", "download_run"}:
                result = actions.download(str(fields["filename"]), run=name == "download_run")
            else:  # defensive in case the whitelist and dispatch drift apart
                raise ValueError("unsupported panel action")
            snapshot = self.get_snapshot()
            if result.get("ok") is True:
                raw_details = {key: value for key, value in result.items() if key in self._RESULT_FIELDS}
                details: dict[str, object] = {}
                for key, value in raw_details.items():
                    if type(value) in {bool, int}:
                        details[key] = value
                    elif isinstance(value, str):
                        details[key] = value[:2048]
                return {"ok": True, "message": self._message(name), "details": details, "snapshot": snapshot}
            return {"ok": False, "error": _short_text(result.get("error"), 256) or "未执行。", "snapshot": snapshot}
        except Exception as error:
            from bridge_client import is_outcome_unknown

            message = (
                "桥命令响应未确认，操作结果未知。请先刷新状态，不要重放可能已执行的命令。"
                if is_outcome_unknown(error)
                else "操作失败；请检查连接、工作区与策略后刷新状态。"
            )
            return {"ok": False, "error": message, "snapshot": self.get_snapshot()}
        finally:
            with self._condition:
                self._pending_confirmation = None
                self._resolution = None
            self._action_lock.release()

    def _on_window_closed(self) -> None:
        with self._condition:
            self._closed = True
            self._resolution = False
            self._condition.notify_all()


class CompactPanelApi(PanelOperationsApi):
    """UI-8 compact window adds only its own minimize and close controls."""

    def minimize_window(self) -> bool:
        if self._window is None:
            return False
        self._window.minimize()  # type: ignore[attr-defined]
        return True

    def close_window(self) -> bool:
        if self._window is None:
            return False
        self._window.destroy()  # type: ignore[attr-defined]
        return True


class DemoApi:
    """Tiny fixed API surface; it cannot access files, broker, or devices."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ping_count = 0
        self.last_kind: str | None = None

    def ping(self, kind: str) -> dict[str, object]:
        if not isinstance(kind, str) or kind not in {"startup", "button"}:
            raise ValueError("Only fixed UI-2 demo ping kinds are accepted")
        with self._lock:
            self.ping_count += 1
            self.last_kind = kind
            number = self.ping_count
        return {"ok": True, "message": f"pong · {kind} · #{number} · MOCK"}


def send_host_state(window: object, result: dict[str, object], closed: threading.Event, done: threading.Event) -> None:
    """Push a fixed status message after the in-memory document is ready."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not closed.is_set():
        try:
            ready = window.evaluate_js("Boolean(window.__ui2HostReady)")  # type: ignore[attr-defined]
            if ready:
                state = {"source": "local-python-demo", "message": "Python 已推送：宿主就绪 · MOCK"}
                window.evaluate_js(f"window.__ui2ReceiveState({json.dumps(state, ensure_ascii=False)})")  # type: ignore[attr-defined]
                result["pythonToPageStateSent"] = True
                done.set()
                return
        except Exception as error:  # the renderer can still be starting up
            result["hostMessageWaitError"] = type(error).__name__
        time.sleep(0.1)
    result["pythonToPageStateSent"] = False
    result["hostMessageWaitTimeout"] = not closed.is_set()
    done.set()


def write_exit_report(path: Path | None, report: dict[str, object]) -> None:
    if path is None:
        return
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_demo(exit_report: Path | None) -> int:
    if os.name != "nt":
        print("UI-2 demo requires Windows and the installed WebView2 Runtime.", file=sys.stderr)
        return 2
    try:
        import webview
    except Exception as error:
        print(f"Cannot import pywebview: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    runtime_dirs = webview2_runtime_dirs()
    if not runtime_dirs:
        print("WebView2 Runtime was not found in the standard installation directories.", file=sys.stderr)
        return 2

    api = DemoApi()
    page = load_inline_page()
    report: dict[str, object] = {
        "title": TITLE,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "pywebview": importlib.metadata.version("pywebview"),
        "webview2RuntimeDirs": runtime_dirs,
        "observedWebView2RuntimeVersions": [Path(path).name for path in runtime_dirs],
        "httpServer": False,
        "htmlSource": "inline-memory",
        "mode": "mock-demo",
        "guiCreated": True,
        "pageToPythonPingCount": 0,
        "pythonToPageStateSent": False,
    }
    closed = threading.Event()
    message_done = threading.Event()
    window = webview.create_window(
        TITLE,
        html=page,
        js_api=api,
        width=700,
        height=700,
        min_size=(480, 500),
        background_color="#f6f6fa",
    )
    window.events.closed += closed.set
    webview.start(send_host_state, (window, report, closed, message_done), debug=False, http_server=False, private_mode=True)
    message_done.wait(timeout=2)
    report["pageToPythonPingCount"] = api.ping_count
    report["lastPingKind"] = api.last_kind
    report["closedAt"] = datetime.now(timezone.utc).isoformat()
    report["processReturnedAfterWindowClosed"] = True
    write_exit_report(exit_report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def run_readonly(workspace: str | None, profile: str, scenario: str) -> int:
    if os.name != "nt":
        print("UI-3 read-only window requires Windows and the installed WebView2 Runtime.", file=sys.stderr)
        return 2
    try:
        import webview
    except Exception as error:
        print(f"Cannot import pywebview: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    runtime_dirs = webview2_runtime_dirs()
    if not runtime_dirs:
        print("WebView2 Runtime was not found in the standard installation directories.", file=sys.stderr)
        return 2

    server_dir = str(SERVER_DIR)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    try:
        from panel.backend import PanelBackend
        backend = PanelBackend.default_mock(workspace=workspace, profile=profile, scenario=scenario)
    except Exception as error:
        print(f"Cannot initialize mock PanelBackend: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

    api = ReadOnlyApi(backend)
    try:
        window = webview.create_window(
            READONLY_TITLE,
            html=load_readonly_page(),
            js_api=api,
            width=860,
            height=760,
            min_size=(520, 560),
            background_color="#f6f6fa",
        )
        report = {
            "title": READONLY_TITLE,
            "mode": "mock-readonly",
            "mockScenario": scenario,
            "profile": profile,
            "workspaceProvided": bool(workspace),
            "httpServer": False,
            "autoConnect": False,
            "pageApi": ["get_snapshot()"],
            "webview2RuntimeVersions": [Path(path).name for path in runtime_dirs],
        }
        print(json.dumps(report, ensure_ascii=False))
        webview.start(debug=False, http_server=False, private_mode=True)
        return 0
    finally:
        # PanelBackend owns exactly this client's broker lease. Broker closes its
        # bridge only after the final client releases its lease.
        backend.close()


def _fit_operations_window(work_left: int, work_top: int, work_width: int, work_height: int, dpi: int) -> dict[str, int]:
    """Fit the 900x820 design to physical work area at the selected monitor DPI."""
    scale = max(1.0, dpi / 96.0)
    logical_width = max(1, int(work_width / scale))
    logical_height = max(1, int(work_height / scale))
    width = max(420, min(900, logical_width - 32))
    height = max(300, min(820, logical_height - 32))
    return {
        "width": width, "height": height,
        "x": round(work_left / scale + (logical_width - width) / 2),
        "y": round(work_top / scale + (logical_height - height) / 2),
        "minWidth": 420, "minHeight": 300, "displayDpi": dpi,
    }


def operations_window_geometry() -> dict[str, int]:
    """Query a physical desktop work area under temporary per-monitor DPI awareness."""
    if os.name != "nt":
        return _fit_operations_window(0, 0, 1200, 900, 96)
    import ctypes
    from ctypes import wintypes

    class Rect(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.GetDpiForSystem.restype = wintypes.UINT
    user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    prior = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        work = Rect()
        dpi = int(user32.GetDpiForSystem()) or 96
        if user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0):
            return _fit_operations_window(work.left, work.top,
                                          work.right - work.left, work.bottom - work.top, dpi)
        return _fit_operations_window(0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1), dpi)
    finally:
        if prior:
            user32.SetThreadDpiAwarenessContext(prior)


def compact_window_geometry() -> dict[str, int]:
    """Center the compact 450x500 design within the existing work-area fit."""
    large = operations_window_geometry()
    width = min(450, large["width"])
    height = min(500, large["height"])
    return {
        "width": width,
        "height": height,
        "x": large["x"] + round((large["width"] - width) / 2),
        "y": large["y"] + round((large["height"] - height) / 2),
        "minWidth": min(420, width),
        "minHeight": min(360, height),
        "displayDpi": large["displayDpi"],
    }


def run_operations(workspace: str | None, profile: str, scenario: str,
                   *, backend: object | None = None, mode: str = "mock") -> int:
    """Open the compact panel against the configured shared mock or real bridge."""
    if os.name != "nt":
        print("UI-4 operations window requires Windows and the installed WebView2 Runtime.", file=sys.stderr)
        return 2
    try:
        import webview
    except Exception as error:
        print(f"Cannot import pywebview: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    runtime_dirs = webview2_runtime_dirs()
    if not runtime_dirs:
        print("WebView2 Runtime was not found in the standard installation directories.", file=sys.stderr)
        return 2
    server_dir = str(SERVER_DIR)
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    try:
        from panel.backend import PanelBackend
        if backend is None:
            backend = PanelBackend.default_mock(workspace=workspace, profile=profile, scenario=scenario)
        api = CompactPanelApi(backend, window=None)
    except Exception as error:
        print(f"Cannot initialize {mode} panel: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    try:
        geometry = compact_window_geometry()
        window = webview.create_window(
            COMPACT_TITLE, html=load_compact_operations_page(), js_api=api,
            width=geometry["width"], height=geometry["height"],
            x=geometry["x"], y=geometry["y"],
            min_size=(geometry["minWidth"], geometry["minHeight"]),
            resizable=True, frameless=True, easy_drag=False, shadow=True,
            background_color="#f5f5fa",
        )
        api._window = window
        window.events.closed += api._on_window_closed
        print(json.dumps({
            "title": COMPACT_TITLE, "mode": mode,
            "mockScenario": scenario if mode == "mock" else None, "profile": profile,
            "workspaceProvided": bool(workspace), "httpServer": False,
            "autoConnect": False, "windowGeometry": geometry,
            "pageApi": ["get_snapshot()", "discover_workspaces(root)", "perform(action,payload)",
                        "approve_current_operation()", "reject_current_operation()",
                        "approve_agent_confirmation(id)", "reject_agent_confirmation(id)",
                        "minimize_window()", "close_window()"],
            "webview2RuntimeVersions": [Path(path).name for path in runtime_dirs],
        }, ensure_ascii=False))
        webview.start(debug=False, http_server=False, private_mode=True)
        return 0
    finally:
        api._on_window_closed()
        backend.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UI-2 pywebview mock-only host probe/demo")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true", help="inspect dependencies without creating a GUI")
    group.add_argument("--demo", action="store_true", help="open the visible mock-only UI-2 window")
    group.add_argument("--readonly", action="store_true", help="open the UI-3 shared-backend read-only mock window")
    group.add_argument("--operations", action="store_true", help="open the UI-4 mock-only operations window")
    parser.add_argument("--workspace", help="optional explicit workspace path; must match active broker configuration")
    parser.add_argument("--profile", choices=("generic", "hiwonder"), default="generic")
    parser.add_argument("--mock-scenario", choices=("readonly", "normal", "busy", "control", "fileops", "fileops-no-capability"), default="fileops")
    parser.add_argument("--exit-report", type=Path, help="write a small JSON close report after the window exits")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.probe:
        result = dependency_report()
        print(json.dumps(result, ensure_ascii=False))
        if os.name != "nt" or not result["pywebviewAvailable"] or not result["webview2RuntimeDirs"]:
            return 2
        return 0
    if args.demo:
        return run_demo(args.exit_report)
    if args.exit_report:
        parser.error("--exit-report is only available with --demo")
    if args.workspace:
        workspace = Path(args.workspace).expanduser()
        if not workspace.is_absolute() or not workspace.is_dir():
            parser.error("--workspace must be an existing absolute directory")
        args.workspace = str(workspace.resolve())
    if args.operations:
        return run_operations(args.workspace, args.profile, args.mock_scenario)
    return run_readonly(args.workspace, args.profile, args.mock_scenario)


if __name__ == "__main__":
    raise SystemExit(main())
