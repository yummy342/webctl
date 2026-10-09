"""§4 随时接管：三态 + 交接态的状态机。

  AI_RUNNING ──人点接管──→ HUMAN_CONTROL ──人点交回──→ HANDOFF ──→ AI_RUNNING
       │                                                        │
       └── AI 触发让位 ──→ WAITING_HUMAN ──人处理──→ HANDOFF ────┘

交接协议：
  1. 暂停时冻结 agent loop，保存 last_observation + action_history；
  2. 人操作期间 AI 不发任何动作（session 层据 state 拦截）；
  3. 交回时重新观测，与冻结观测做 diff，AI 从新状态继续，不从旧计划硬续。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .observe import Observation


class State(enum.Enum):
    AI_RUNNING = "ai_running"
    HUMAN_CONTROL = "human_control"
    WAITING_HUMAN = "waiting_human"
    HANDOFF = "handoff"


class TakeoverError(RuntimeError):
    pass


@dataclass
class HandoffDiff:
    url_before: str
    url_after: str
    added: list[str] = field(default_factory=list)    # 人操作后新出现的元素（role:name）
    removed: list[str] = field(default_factory=list)  # 人操作后消失的元素
    changed_url: bool = False

    def describe(self) -> str:
        parts = []
        if self.changed_url:
            parts.append(f"URL 变了：{self.url_before} → {self.url_after}")
        if self.added:
            parts.append("新增元素：" + "、".join(self.added[:10]))
        if self.removed:
            parts.append("消失元素：" + "、".join(self.removed[:10]))
        return "；".join(parts) if parts else "页面无可见变化"


def diff_observations(before: Observation, after: Observation) -> HandoffDiff:
    b = {(n.role, n.name) for n in before.ax_tree if n.name}
    a = {(n.role, n.name) for n in after.ax_tree if n.name}
    fmt = lambda s: [f"{r}:{n}" for r, n in sorted(s)]
    return HandoffDiff(
        url_before=before.url, url_after=after.url,
        added=fmt(a - b), removed=fmt(b - a),
        changed_url=before.url != after.url,
    )


@dataclass
class TakeoverMachine:
    state: State = State.AI_RUNNING
    frozen_observation: Observation | None = None
    action_history: list[dict] = field(default_factory=list)
    yield_reason: str | None = None  # AI 让位的原因（WAITING_HUMAN 时）

    @property
    def ai_may_act(self) -> bool:
        return self.state is State.AI_RUNNING

    def _require(self, *states: State, what: str) -> None:
        if self.state not in states:
            raise TakeoverError(f"{what} 在状态 {self.state.value} 不允许")

    def record_action(self, summary: dict) -> None:
        self.action_history.append(summary)

    def human_takeover(self, current: Observation) -> None:
        """人主动点接管。"""
        self._require(State.AI_RUNNING, what="人工接管")
        self.frozen_observation = current
        self.state = State.HUMAN_CONTROL

    def ai_yield(self, current: Observation, reason: str) -> None:
        """AI 触发让位（§5 命中），停下等人。"""
        self._require(State.AI_RUNNING, what="AI 让位")
        self.frozen_observation = current
        self.yield_reason = reason
        self.state = State.WAITING_HUMAN

    def human_release(self) -> None:
        """人点交回，进入交接态，等新观测做 diff。"""
        self._require(State.HUMAN_CONTROL, State.WAITING_HUMAN, what="交回")
        self.state = State.HANDOFF

    def complete_handoff(self, new: Observation) -> HandoffDiff:
        """交接完成：算 diff、回 AI_RUNNING。AI 必须先读 diff 再继续。"""
        self._require(State.HANDOFF, what="完成交接")
        assert self.frozen_observation is not None
        d = diff_observations(self.frozen_observation, new)
        self.frozen_observation = None
        self.yield_reason = None
        self.state = State.AI_RUNNING
        return d
