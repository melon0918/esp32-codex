// UI-8 live compact panel wiring. Loaded only by the trusted WebView host.
// The standalone compact.js remains a harmless visual-only MOCK prototype.
(() => {
  const $ = (id) => document.getElementById(id);
  const output = $('console-output');
  const modal = $('confirm-modal');
  const agentModal = $('agent-confirm-modal');
  const toast = $('toast');
  const api = () => window.pywebview && window.pywebview.api;
  let busy = false;
  let toastTimer = 0;
  let refreshTimer = 0;
  let latestSnapshot = null;
  let backendReady = false;
  let lastWorkspaceKey = null;
  let programSelectedByUser = false;
  let consoleBuffer = '';
  let consoleCursor = null;
  let activeAgentConfirmationId = null;

  $('program-select').addEventListener('change', () => { programSelectedByUser = true; });

  window.__esp32CompactLive = true;
  $('confirm-approve').textContent = '确认操作';
  document.querySelector('.dialog-eyebrow').textContent = '确认本次操作';

  function setStatus(message, error = false) {
    const footer = $('footer-note-text');
    if (footer) footer.textContent = message;
    if (footer) footer.classList.toggle('status-error', error);
  }

  function announce(message, error = false) {
    toast.textContent = message;
    toast.classList.toggle('toast-error', error);
    toast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3200);
  }

  function setBusy(value) {
    busy = value;
    applyControlAvailability();
  }

  function applyControlAvailability() {
    document.querySelectorAll('.workbench button, .workbench input, .workbench select').forEach((control) => {
      control.disabled = busy;
    });
    const snapshot = latestSnapshot || {};
    const usable = backendReady && typeof snapshot.simulated === 'boolean';
    const connected = Boolean(snapshot.status && snapshot.status.connected);
    const hasWorkspace = Boolean(snapshot.workspace && snapshot.workspace.path);
    const ports = Array.isArray(snapshot.ports) ? snapshot.ports : [];
    const unavailable = busy || !usable;

    $('connect-button').disabled = unavailable || (!connected && ports.length === 0);
    document.querySelectorAll('[data-action="run"], [data-action="interrupt"], [data-action="stop"]').forEach((button) => {
      button.disabled = unavailable || !connected;
    });
    document.querySelectorAll('[data-action="download"], [data-action="download-run"]').forEach((button) => {
      button.disabled = unavailable || !connected || !hasWorkspace;
    });
    $('repl-input').disabled = unavailable || !connected;
    document.querySelector('#repl-form button[type="submit"]').disabled = unavailable || !connected;
    $('policy-select').disabled = unavailable || !hasWorkspace;
    $('policy-apply').disabled = unavailable || !hasWorkspace;
    $('workspace-discover').disabled = unavailable;
    $('workspace-claim').disabled = unavailable;
    document.querySelectorAll('.workspace-candidate button').forEach((button) => {
      button.disabled = unavailable || busy;
    });
    const confirmationUnavailable = !backendReady || busy || !api() || !activeAgentConfirmationId;
    $('agent-confirm-approve').disabled = confirmationUnavailable;
    $('agent-confirm-reject').disabled = confirmationUnavailable;
  }

  function clearAgentConfirmation() {
    activeAgentConfirmationId = null;
    agentModal.hidden = true;
    applyControlAvailability();
  }

  function renderAgentConfirmations(rows) {
    const item = Array.isArray(rows)
      ? rows.find((row) => row && row.state === 'pending' && typeof row.id === 'string')
      : null;
    if (!item) {
      clearAgentConfirmation();
      return;
    }

    if (activeAgentConfirmationId !== item.id) {
      activeAgentConfirmationId = item.id;
      $('agent-confirm-title').textContent = item.title || item.action || 'Codex 请求操作';
      const effectNames = { control: '控制', write: '写入', workspace: '工作区配置' };
      const effectName = effectNames[item.effect] || item.effect || '未分类';
      $('agent-confirm-kind').textContent = `操作：${item.action || item.title || '未提供'} · 类型：${effectName}`;
      $('agent-confirm-target').textContent = item.target || '未提供目标';
      $('agent-confirm-impact').textContent = item.impact || '未提供影响说明';
      const context = [
        item.workspacePath || '未选择工作区',
        item.profile || '未知机型',
        item.entry || '无入口',
        `控制 epoch ${Number.isInteger(item.control_epoch) ? item.control_epoch : '未知'}`,
        `策略版本 ${item.policy_revision || 'missing'}`,
      ];
      $('agent-confirm-context').textContent = context.join(' · ');
      agentModal.hidden = false;
      // Require a deliberate click; Enter cannot accidentally approve the request.
      $('agent-confirm-reject').focus();
    }

    const remaining = Number.isInteger(item.expires_in_ms) ? Math.max(0, item.expires_in_ms) : 0;
    $('agent-confirm-expiry').textContent = remaining > 0
      ? `确认有效期约 ${Math.ceil(remaining / 1000)} 秒`
      : '确认已到期或即将失效；请拒绝或刷新状态。';
    applyControlAvailability();
  }

  async function resolveAgentConfirmation(approve) {
    const id = activeAgentConfirmationId;
    if (!id || busy || !api()) return;
    const method = approve ? api().approve_agent_confirmation : api().reject_agent_confirmation;
    if (typeof method !== 'function') {
      announce('面板宿主未提供 Agent 确认通道；请求未发送。', true);
      return;
    }

    $('agent-confirm-approve').disabled = true;
    $('agent-confirm-reject').disabled = true;
    try {
      const result = await method.call(api(), id);
      if (result && result.ok) {
        clearAgentConfirmation();
        announce(approve
          ? '面板已批准这一个 Agent 请求；正在等待 MCP 结果。'
          : '面板已拒绝这一个 Agent 请求；操作未执行。', !approve);
      } else {
        clearAgentConfirmation();
        announce('该确认已过期或上下文已变化；没有执行本次操作。', true);
      }
      await refreshSnapshot();
    } catch (_) {
      clearAgentConfirmation();
      announce('确认结果暂时无法读取；请先核对控制台状态，勿重复发送。', true);
      await refreshSnapshot();
    } finally {
      applyControlAvailability();
    }
  }

  function renderSnapshot(data) {
    if (!data || typeof data !== 'object') return;
    latestSnapshot = data;
    if (data.error) {
      backendReady = false;
      clearAgentConfirmation();
      applyControlAvailability();
      setStatus(data.error, true);
      return;
    }
    backendReady = typeof data.simulated === 'boolean';

    $('source-label').textContent = data.simulated ? 'MOCK · 模拟' : '真实设备';
    const workspace = data.workspace || {};
    const workspaceName = workspace.label || (workspace.profile === 'hiwonder' ? '幻尔小车' : '通用 ESP32');
    const path = workspace.path || '';
    const entry = String(workspace.entry || '').split(/[\\/]/).pop() || 'main.py';
    const label = $('workspace-label');
    label.replaceChildren();
    const title = document.createElement('span');
    title.textContent = path ? workspaceName : '未选择工作区';
    const subpath = document.createElement('small');
    subpath.textContent = path ? ` · ${path}` : ' · 选择或认领一个工作区';
    label.append(title, subpath);
    $('workspace-summary').textContent = path ? `${workspaceName} · ${entry}` : '未选择工作区';
    $('workspace-path').textContent = path || '未选择';
    if (document.activeElement !== $('workspace-root') && path && !$('workspace-root').value) {
      $('workspace-root').value = path;
    }
    if (document.activeElement !== $('claim-path') && path && !$('claim-path').value) {
      $('claim-path').value = path;
    }
    const profile = workspace.profile;
    if (profile && ['generic', 'hiwonder'].includes(profile) && document.activeElement !== $('profile-select')) {
      $('profile-select').value = profile;
    }
    const policy = (data.policy && data.policy.name) || 'confirm-write';
    const policyNames = {
      auto: '自动执行',
      'confirm-write': '写入前确认',
      'confirm-all': '每项确认',
    };
    $('policy-label').textContent = policyNames[policy] || policy;
    if (document.activeElement !== $('policy-select')) $('policy-select').value = policy;

    const program = $('program-select');
    const existingProgram = program.value;
    const workspaceKey = `${path}\n${entry}`;
    if (workspaceKey !== lastWorkspaceKey) {
      lastWorkspaceKey = workspaceKey;
      programSelectedByUser = false;
    }
    const entries = new Set([entry, 'main.py', 'robot.py', 'quad.py']);
    program.replaceChildren(...[...entries].map((name) => new Option(name, name)));
    program.value = programSelectedByUser && entries.has(existingProgram) ? existingProgram : entry;

    const ports = $('serial-select');
    const oldPort = ports.value;
    const available = Array.isArray(data.ports) ? data.ports.filter((item) => item && typeof item.device === 'string') : [];
    ports.replaceChildren();
    if (available.length === 0) {
      ports.add(new Option('未发现串口', ''));
    } else {
      available.forEach((item) => ports.add(new Option(
        `${item.device} · ${data.simulated ? '模拟串口' : '串口'}`, item.device)));
    }
    const connectedPort = data.status && data.status.connected ? data.status.port : '';
    if (connectedPort && [...ports.options].some((option) => option.value === connectedPort)) {
      ports.value = connectedPort;
    } else if ([...ports.options].some((option) => option.value === oldPort)) {
      ports.value = oldPort;
    }
    $('connect-button').textContent = connectedPort ? '断开' : '连接';
    $('connect-button').dataset.action = connectedPort ? 'disconnect' : 'connect';
    const connection = $('connection-chip');
    connection.classList.toggle('is-connected', Boolean(connectedPort));
    $('connection-label').textContent = connectedPort ? `已连接 · ${connectedPort}` : '未连接';
    $('console-live-label').textContent = connectedPort || (available[0] && available[0].device) || '未连接';

    renderAgentConfirmations(data.agentConfirmations);

    const consoleData = data.console && typeof data.console === 'object' ? data.console : null;
    const consoleText = consoleData && typeof consoleData.text === 'string' ? consoleData.text : '';
    const nextCursor = consoleData && Number.isInteger(consoleData.cursor) ? consoleData.cursor : null;
    if (consoleCursor === null || consoleData?.dropped === true ||
        (nextCursor !== null && nextCursor < consoleCursor)) {
      consoleBuffer = consoleText;
    } else if (nextCursor !== null && nextCursor > consoleCursor && consoleText) {
      consoleBuffer += consoleText;
    }
    if (nextCursor !== null) consoleCursor = nextCursor;
    if (consoleBuffer.length > 12000) consoleBuffer = consoleBuffer.slice(-12000);
    const renderedConsole = consoleBuffer || '暂无控制台输出';
    output.classList.add('has-live-output');
    if (output.textContent !== renderedConsole) {
      output.textContent = renderedConsole;
      output.scrollTop = output.scrollHeight;
    }
    document.querySelector('.dialog-eyebrow').textContent = data.simulated ? '模拟操作确认' : '真机操作确认';
    setStatus(data.console && data.console.dropped
      ? `${data.simulated ? '模拟' : '真机'}面板 · 较早的串口输出已滚出缓冲区`
      : data.simulated
        ? '面板操作需确认 · 模拟数据，不代表实体设备状态'
        : '真机已就绪 · 连接与控制由你在面板中触发');
    applyControlAvailability();
  }

  async function refreshSnapshot(showMessage = false) {
    if (busy || !api()) return;
    try {
      const snapshot = await api().get_snapshot();
      renderSnapshot(snapshot);
      if (showMessage) announce(snapshot.error || (snapshot.simulated ? '共享模拟状态已刷新' : '真实设备状态已刷新'), Boolean(snapshot.error));
    } catch (_) {
      backendReady = false;
      applyControlAvailability();
      setStatus('共享状态暂时不可用；请稍后刷新。', true);
      if (showMessage) announce('读取共享状态失败', true);
    }
  }

  function payloadFor(action) {
    if (action === 'connect') return { port: $('serial-select').value };
    if (action === 'repl') return { line: $('repl-input').value };
    if (action === 'download' || action === 'download_run') return { filename: $('program-select').value };
    if (action === 'workspace_select') return { path: $('workspace-root').value.trim() };
    if (action === 'workspace_claim') {
      return {
        path: $('claim-path').value.trim(),
        profile: $('profile-select').value,
        label: $('claim-label').value.trim(),
        entry: $('claim-entry').value.trim(),
      };
    }
    if (action === 'policy_set') return { policy: $('policy-select').value };
    return {};
  }

  async function perform(action, payload = payloadFor(action)) {
    if (busy || !api()) {
      if (!api()) announce('本地窗口宿主尚未就绪', true);
      return;
    }
    setBusy(true);
    setStatus(latestSnapshot && latestSnapshot.simulated ? '正在处理模拟操作…' : '正在处理真机操作…');
    try {
      const result = await api().perform(action, payload);
      renderSnapshot(result.snapshot);
      if (result.ok) announce(result.message || '操作完成');
      else announce(result.error || '操作未执行', true);
    } catch (_) {
      announce('操作失败；请检查共享状态后重试。', true);
      setStatus('操作失败；请检查共享状态后重试。', true);
    } finally {
      modal.hidden = true;
      setBusy(false);
    }
  }

  async function discoverWorkspaces() {
    if (busy || !api()) return;
    const root = $('workspace-root').value.trim() || (latestSnapshot && latestSnapshot.workspace.path) || '';
    if (!root) {
      announce('先输入要扫描的本机绝对路径', true);
      $('workspace-root').focus();
      return;
    }
    setBusy(true);
    setStatus('正在扫描本机工作区…');
    try {
      const result = await api().discover_workspaces(root);
      const list = $('workspace-list');
      list.replaceChildren();
      (result.workspaces || []).forEach((item) => {
        const row = document.createElement('div');
        row.className = 'workspace-candidate';
        const description = document.createElement('span');
        description.textContent = `${item.workspacePath} · ${item.profile} · ${item.entry}`;
        const choose = document.createElement('button');
        choose.className = 'text-button';
        choose.type = 'button';
        choose.textContent = item.claimed ? '切换' : '认领';
        choose.addEventListener('click', () => {
          if (item.claimed) {
            perform('workspace_select', { path: item.workspacePath });
          } else {
            $('claim-path').value = item.workspacePath;
            $('workspace-root').value = item.workspacePath;
            $('profile-select').value = ['generic', 'hiwonder'].includes(item.profile) ? item.profile : 'generic';
            $('claim-label').value = item.profileLabel || item.workspacePath.split(/[\\/]/).filter(Boolean).pop() || 'ESP32 项目';
            $('claim-entry').value = item.entry || '/main.py';
            announce('已填写认领信息；检查后点击“认领目录”');
          }
        });
        row.append(description, choose);
        list.append(row);
      });
      setStatus(`发现 ${result.workspaces.length} 个工作区${result.truncated ? ' · 结果已截断' : ''}`);
      if (result.workspaces.length === 0) announce('未发现已配置的工作区');
    } catch (_) {
      announce('工作区扫描失败；请确认路径为本机绝对路径', true);
      setStatus('工作区扫描失败；请确认路径为本机绝对路径。', true);
    } finally {
      setBusy(false);
    }
  }

  window.__ui4ShowConfirmation = (value) => {
    $('confirm-title').textContent = String(value.title || '确认本次操作');
    $('confirm-copy').textContent = `目标：${String(value.target || '当前工作区')}\n影响：${String(value.impact || '执行本次操作')}\n拒绝或关闭不会执行操作。`;
    modal.hidden = false;
    $('confirm-approve').focus();
  };

  async function resolveConfirmation(accept) {
    if (!api()) { modal.hidden = true; return; }
    try {
      const answer = accept ? await api().approve_current_operation() : await api().reject_current_operation();
      if (!answer.ok && !accept) setStatus('确认已失效；没有执行操作。', true);
    } catch (_) {
      setStatus('确认通道不可用；操作未执行。', true);
    }
    if (!accept) modal.hidden = true;
  }

  document.addEventListener('click', (event) => {
    if (!agentModal.hidden) {
      if (event.target.closest('#agent-confirm-approve')) {
        event.preventDefault();
        event.stopImmediatePropagation();
        resolveAgentConfirmation(true);
        return;
      }
      if (event.target.closest('#agent-confirm-reject') || event.target === agentModal) {
        event.preventDefault();
        event.stopImmediatePropagation();
        resolveAgentConfirmation(false);
        return;
      }
    }
    if (!modal.hidden) {
      if (event.target.closest('#confirm-approve')) {
        event.preventDefault(); event.stopImmediatePropagation();
        resolveConfirmation(true);
        return;
      }
      if (event.target.closest('[data-close]') || event.target === modal) {
        event.preventDefault(); event.stopImmediatePropagation();
        resolveConfirmation(false);
        return;
      }
    }
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    if (action === 'copy') return;
    if (action === 'clear') {
      event.preventDefault();
      event.stopImmediatePropagation();
      consoleBuffer = '';
      if (latestSnapshot?.console && Number.isInteger(latestSnapshot.console.cursor)) {
        consoleCursor = latestSnapshot.console.cursor;
      }
      output.textContent = '暂无控制台输出';
      announce('已清空当前面板输出');
      return;
    }
    event.preventDefault(); event.stopImmediatePropagation();
    if (action === 'workspace') {
      $('advanced-settings').open = true;
      $('workspace-root').focus();
    } else if (action === 'refresh') {
      refreshSnapshot(true);
    } else if (action === 'discover' || action === 'scan') {
      discoverWorkspaces();
    } else if (action === 'policy') {
      perform('policy_set');
    } else if (action === 'claim') {
      perform('workspace_claim');
    } else if (['connect', 'disconnect', 'run', 'interrupt', 'stop', 'download', 'download-run'].includes(action)) {
      perform(action === 'download-run' ? 'download_run' : action);
    }
  }, true);

  document.addEventListener('submit', (event) => {
    if (event.target.id !== 'repl-form') return;
    event.preventDefault(); event.stopImmediatePropagation();
    const line = $('repl-input').value.trim();
    if (!line) { announce('先输入一行 MicroPython 命令', true); $('repl-input').focus(); return; }
    perform('repl', { line });
  }, true);

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!agentModal.hidden) {
      event.preventDefault(); event.stopImmediatePropagation();
      resolveAgentConfirmation(false);
      return;
    }
    if (modal.hidden) return;
    event.preventDefault(); event.stopImmediatePropagation();
    resolveConfirmation(false);
  }, true);

  function initialize() {
    if (!api()) return false;
    refreshSnapshot();
    refreshTimer = window.setInterval(() => refreshSnapshot(), 2500);
    return true;
  }
  window.addEventListener('pywebviewready', initialize, { once: true });
  initialize();
  window.addEventListener('beforeunload', () => window.clearInterval(refreshTimer), { once: true });
})();
