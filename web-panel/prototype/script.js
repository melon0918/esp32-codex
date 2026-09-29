// Static UI demonstration only. State lives in this page and is never persisted.
(() => {
  const views = [...document.querySelectorAll("[data-panel]")];
  const navButtons = [...document.querySelectorAll("[data-view]")];
  const modal = document.getElementById("confirm-modal");
  const toast = document.getElementById("toast");
  const toastMessage = document.getElementById("toast-message");
  const demoStatus = document.getElementById("demo-status");
  const consoleLines = document.getElementById("console-lines");
  const crumb = document.getElementById("crumb-current");
  let toastTimer;
  let busyTimer;
  let lastFocused;

  const labels = { overview: "工作台", workspace: "工作区", console: "控制台" };

  function showView(name) {
    if (!labels[name]) return;
    views.forEach((view) => {
      const active = view.dataset.panel === name;
      view.hidden = !active;
      view.classList.toggle("is-visible", active);
    });
    navButtons.forEach((button) => {
      const active = button.dataset.view === name;
      button.classList.toggle("is-active", active);
      if (button.classList.contains("rail-link")) {
        if (active) button.setAttribute("aria-current", "page");
        else button.removeAttribute("aria-current");
      }
    });
    crumb.textContent = labels[name];
  }

  function announce(message, kind = "success") {
    window.clearTimeout(toastTimer);
    toast.classList.toggle("error", kind === "error");
    toast.querySelector(".toast-icon").textContent = kind === "error" ? "!" : "✓";
    toastMessage.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3200);
  }

  function showBusy() {
    window.clearTimeout(busyTimer);
    demoStatus.className = "demo-status busy";
    demoStatus.textContent = "正在展示忙碌状态 · 仅页面动画，无后台任务";
    demoStatus.hidden = false;
    busyTimer = window.setTimeout(() => { demoStatus.hidden = true; }, 2400);
    announce("已进入忙碌状态预览");
  }

  function showError() {
    window.clearTimeout(busyTimer);
    demoStatus.className = "demo-status error";
    demoStatus.textContent = "演示错误：设备未连接 · 页面没有尝试连接设备";
    demoStatus.hidden = false;
    announce("已显示模拟错误状态", "error");
  }

  function openModal(action = "run") {
    lastFocused = document.activeElement;
    const copy = {
      run: {
        title: "运行 main.py？",
        description: "此操作会运行当前工作区的入口文件。请确认目标与影响后继续。",
        impact: "运行 main.py · 仅为模拟演示",
        safety: "演示确认不会连接设备或发出运行命令。",
      },
      download: {
        title: "下载 main.py 到设备？",
        description: "下载会写入设备入口文件。覆盖旧文件前必须完成严格备份与校验。",
        impact: "写入 main.py · 先严格备份，再校验",
        safety: "备份或校验失败时必须停止且不覆盖；本预览不会执行下载。",
      },
      "download-run": {
        title: "下载并运行 main.py？",
        description: "先严格备份并校验设备旧文件，再写入并运行此入口。",
        impact: "备份 → 下载 → 校验 → 运行",
        safety: "备份或校验失败时停止且不覆盖；本预览不会执行设备操作。",
      },
    }[action] || null;
    if (copy) {
      document.getElementById("confirm-title").textContent = copy.title;
      document.getElementById("confirm-copy").textContent = copy.description;
      document.getElementById("confirm-impact").textContent = copy.impact;
      document.getElementById("confirm-safety-note").textContent = copy.safety;
    }
    modal.hidden = false;
    modal.querySelector("[data-close-modal]").focus();
  }

  function closeModal() {
    modal.hidden = true;
    if (lastFocused && typeof lastFocused.focus === "function") lastFocused.focus();
  }

  function appendDemoLine(text) {
    const oldCursor = consoleLines.querySelector(".console-cursor");
    if (oldCursor) oldCursor.remove();
    const line = document.createElement("p");
    const count = consoleLines.querySelectorAll("p").length + 1;
    const number = String(count).padStart(2, "0");
    line.innerHTML = `<span class="line-no">${number}</span><span class="time">09:41:08</span><span class="log-info">[demo]</span><span></span>`;
    line.lastElementChild.textContent = text;
    consoleLines.append(line);
    const cursor = document.createElement("p");
    cursor.className = "console-cursor";
    cursor.innerHTML = `<span class="line-no">${String(count + 1).padStart(2, "0")}</span><span class="cursor-block"></span>`;
    consoleLines.append(cursor);
    document.getElementById("console-window").scrollTop = document.getElementById("console-window").scrollHeight;
  }

  navButtons.forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));

  document.querySelectorAll("[data-demo]").forEach((button) => {
    button.addEventListener("click", () => {
      switch (button.dataset.demo) {
        case "confirm": openModal(button.dataset.confirmAction || "run"); break;
        case "error": showError(); break;
        case "busy":
          showBusy();
          if (button.closest("#console")) appendDemoLine("演示命令已记录；没有串口活动");
          break;
        case "clear": consoleLines.replaceChildren(); announce("已清空页面中的样例文字"); break;
        default: break;
      }
    });
  });

  document.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", closeModal));
  modal.addEventListener("click", (event) => { if (event.target === modal) closeModal(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });
  document.querySelector("[data-confirm-demo]").addEventListener("click", () => {
    closeModal();
    announce("演示操作已确认 · 没有执行设备命令");
  });
  document.querySelector("[data-dismiss-toast]").addEventListener("click", () => {
    window.clearTimeout(toastTimer);
    toast.hidden = true;
  });
  document.querySelector(".brand-mark").addEventListener("click", (event) => {
    event.preventDefault();
    showView("overview");
  });
  showView("overview");
})();
