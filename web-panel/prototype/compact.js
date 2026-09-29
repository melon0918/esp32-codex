// Compact layout mock only. No backend, broker, network, serial, or device calls.
(() => {
  const output = document.getElementById("console-output");
  const modal = document.getElementById("confirm-modal");
  const toast = document.getElementById("toast");
  let previousFocus = null;
  let toastTimer = 0;

  function announce(text) {
    toast.textContent = text;
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 2400);
  }

  function appendLine(kind, text) {
    const line = document.createElement("p");
    const time = document.createElement("span");
    const tag = document.createElement("span");
    const message = document.createElement("span");
    time.className = "log-time";
    time.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
    tag.className = `log-tag ${kind === "MOCK" ? "tag-mock" : "tag-info"}`;
    tag.textContent = kind;
    message.textContent = text;
    line.append(time, tag, message);
    output.append(line);
    output.scrollTop = output.scrollHeight;
  }

  function openConfirm(title, copy) {
    previousFocus = document.activeElement;
    document.getElementById("confirm-title").textContent = title;
    document.getElementById("confirm-copy").textContent = copy;
    modal.hidden = false;
    modal.querySelector("[data-close]").focus();
  }

  function closeConfirm() {
    modal.hidden = true;
    if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
  }

  document.querySelectorAll("[data-window-action]").forEach((button) => button.addEventListener("click", async () => {
    const api = window.pywebview && window.pywebview.api;
    if (!api) { announce("窗口控制仅在独立预览中可用"); return; }
    try {
      if (button.dataset.windowAction === "minimize") await api.minimize_window();
      if (button.dataset.windowAction === "close") await api.close_window();
    } catch (_) { announce("窗口操作未能完成"); }
  }));

  async function copyOutput() {
    const text = output.innerText.trim();
    if (!text) { announce("串口输出为空"); return; }
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("clipboard API unavailable");
      await navigator.clipboard.writeText(text);
      announce("串口输出已复制");
    } catch (_) {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(output);
      selection.removeAllRanges();
      selection.addRange(range);
      const copied = document.execCommand("copy");
      selection.removeAllRanges();
      announce(copied ? "串口输出已复制" : "已选中输出，请按 Ctrl+C 复制");
    }
  }

  document.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", closeConfirm));
  modal.addEventListener("click", (event) => { if (event.target === modal) closeConfirm(); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !modal.hidden) closeConfirm(); });
  document.getElementById("confirm-approve").addEventListener("click", () => {
    closeConfirm();
    appendLine("MOCK", "确认仅用于界面演示；没有向设备写入文件");
    announce("已完成 MOCK 操作预览");
  });

  document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", () => {
    const action = button.dataset.action;
    const messages = {
      workspace: ["MOCK", "工作区选择器仅作布局预览"],
      refresh: ["MOCK", "串口列表刷新仅作界面预览"],
      connect: ["MOCK", "连接按钮仅作界面预览；没有打开串口"],
      run: ["MOCK", "运行 main.py 仅作界面预览；没有发送设备命令"],
      interrupt: ["MOCK", "中断操作仅作界面预览"],
      stop: ["MOCK", "停止操作仅作界面预览"],
      scan: ["MOCK", "工作区扫描仅作界面预览"],
      clear: null,
    };
    if (action === "download" || action === "download-run") {
      openConfirm(action === "download" ? "确认下载 main.py？" : "确认下载并运行 main.py？",
        "这是静态界面预览。确认按钮不会连接设备、备份文件或写入程序。");
      return;
    }
    if (action === "clear") {
      output.replaceChildren();
      announce("已清空本页的模拟输出");
      return;
    }
    if (action === "copy") { copyOutput(); return; }
    const message = messages[action];
    if (message) {
      appendLine(message[0], message[1]);
      announce(message[1]);
    }
  }));

  document.getElementById("repl-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = document.getElementById("repl-input");
    const value = input.value.trim();
    if (!value) { announce("先输入一行演示命令"); input.focus(); return; }
    appendLine("MOCK", `REPL 仅作界面预览：${value}`);
    input.value = "";
    announce("命令没有发送到设备");
  });
})();
