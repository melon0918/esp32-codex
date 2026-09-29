"""Panel-origin approval gate. Never accepts model-supplied confirmed flags."""

from __future__ import annotations

from typing import Any, Callable

from .backend import PanelBackend


class PanelActions:
    def __init__(self, backend: PanelBackend, confirm: Callable[[str, str], bool]):
        self.backend = backend
        self.confirm = confirm

    def _approved(self, plan: dict[str, Any]) -> bool:
        command = plan["command"]
        policy = self.backend.current_policy()
        required = self.backend.needs_confirmation(policy, plan.get("effect", "write"), command)
        if not required:
            return True
        title = f"批准本次操作：{command}"
        body = (
            f"当前策略：{policy}\n"
            f"目标：{plan.get('target', '(unspecified)')}\n"
            f"影响：{plan.get('impact', '(unknown)')}\n\n"
            "仅同意这一笔操作；拒绝或关闭对话框不会执行。"
        )
        return self.confirm(title, body) is True

    def _perform(self, plan: dict[str, Any], apply: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        if not self._approved(plan):
            return {"ok": False, "error": "未批准本次操作；未执行。"}
        result = apply(plan)
        return {"ok": True, **result}

    def control(self, command: str, *, port: str = "", line: str = "") -> dict[str, Any]:
        plan = self.backend.plan_control(command, port=port, line=line)
        if plan.get("noop"):
            return {"ok": True, **plan["result"]}
        return self._perform(plan, self.backend.perform_control)

    def download(self, filename: str, *, run: bool = False) -> dict[str, Any]:
        plan = self.backend.plan_download(filename, run=run)
        return self._perform(plan, self.backend.perform_download)

    def workspace(self, action: str, path: str, *, profile: str = "generic",
                  label: str | None = None, entry: str | None = None) -> dict[str, Any]:
        plan = self.backend.plan_workspace(action, path, profile=profile, label=label, entry=entry)
        return self._perform(plan, self.backend.perform_workspace)

    def policy(self, value: str) -> dict[str, Any]:
        plan = self.backend.plan_policy(value)
        return self._perform(plan, self.backend.perform_policy)
