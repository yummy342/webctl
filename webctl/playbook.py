"""§8 playbook 层：把一家控制台的流程固化成声明式步骤。

step = {
  "name": "打开 API Key 页",
  "expect": {"url_contains": "/api-keys", "ax_contains": "创建"},   # 预期观测
  "action": {"kind": "click", "target_name": "创建 Key"},           # 或坐标动作
  "sensitive": false,                                                # 敏感步：审计抑图（通道宣告）
  "human_stop": "authorization",                                     # 声明式人工停靠：到此步按该类让位
  "harvest": {"vault_name": "site_api_key"},                         # 一次性 key 回收步（runner 真执行）
}
三种特殊字段（human_stop / harvest / sensitive）runner 都必须真处理；
from_dict 加载期校验未知字段直接报错——声明了没人执行的字段不许静默跳过。
规则：每步先观测、验预期；**页面与预期不符 → 让位（低置信类，reason 标
playbook_mismatch），不即兴发挥**——控制台改版是常态，硬续可能误点删除类按钮。
动作以 target_name 定位（经 AX 树找元素取其归一化坐标），定位不到计一次
低置信信号；目标命中不可逆语义时先过 §5 动作级检测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .actions import Action
from .observe import Observation


@dataclass
class StepResult:
    name: str
    ok: bool
    detail: str = ""


_STEP_KEYS = {"name", "expect", "action", "sensitive", "harvest", "human_stop"}
_ACTION_KINDS = {"click", "type", "navigate", "scroll", "wait", "key", "upload", "download"}
# P1-7：expect 子键白名单。{"url_contain": ...}（拼写错误）曾被静默忽略，
# 预期检查实际没跑——这正是本模块宣称要杜绝的"声明了没人执行"类 bug。
_EXPECT_KEYS = {"url_contains", "ax_contains", "title_contains"}
# harvest 子键白名单。runner 只读 vault_name 与 extract_regex；随包的
# bailian.json 曾在 harvest 块里写了个 "via"（没人读）——正是本模块宣称
# 要杜绝的"声明了没人执行"类 bug，只是漏在子键这一层。探针配置在
# playbook 顶层的 probe 块，不在 harvest 里。
_HARVEST_KEYS = {"vault_name", "extract_regex"}


@dataclass
class Playbook:
    name: str
    steps: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # 原始文档（probe/revoke 等顶层配置）

    @classmethod
    def from_dict(cls, d: dict) -> "Playbook":
        """加载期校验（P0-2）：声明了却没人执行的字段比没声明更危险——
        未知步字段/未知动作/harvest 缺 vault_name 一律显式报错，不许静默跳过。"""
        if not isinstance(d, dict) or "name" not in d:
            raise ValueError("playbook 必须是含 name 的对象")
        for i, step in enumerate(d.get("steps", [])):
            if not isinstance(step, dict):
                raise ValueError(f"step[{i}] 不是对象")
            sname = step.get("name", f"step[{i}]")
            unknown = set(step) - _STEP_KEYS
            if unknown:
                raise ValueError(f"步「{sname}」含未实现字段 {sorted(unknown)}，拒绝加载")
            act = step.get("action")
            if act is not None and act.get("kind") not in _ACTION_KINDS:
                raise ValueError(f"步「{sname}」未知动作 kind: {act.get('kind')}")
            if step.get("harvest") is not None:
                hv = step["harvest"]
                if not isinstance(hv, dict) or not hv.get("vault_name"):
                    raise ValueError(f"步「{sname}」harvest 必须声明 vault_name")
                unknown_hv = set(hv) - _HARVEST_KEYS
                if unknown_hv:
                    raise ValueError(
                        f"步「{sname}」harvest 含未实现子键 {sorted(unknown_hv)}"
                        f"（可选 {sorted(_HARVEST_KEYS)}），拒绝加载")
                # P2-2：extract_regex 非法时旧代码在会话中途崩且无审计——加载期预编译
                if hv.get("extract_regex"):
                    try:
                        re.compile(hv["extract_regex"])
                    except re.error as e:
                        raise ValueError(f"步「{sname}」harvest.extract_regex 非法: {e}")
            if step.get("human_stop") is not None:
                hs = step["human_stop"]
                if not isinstance(hs, str):
                    raise ValueError(f"步「{sname}」human_stop 必须是让位类名字符串")
                # P2-1：human_stop 拼写错误（如 "autherization"）曾产生 stats 幽灵 key
                from .yields import ALL_CLASSES
                if hs not in ALL_CLASSES:
                    raise ValueError(
                        f"步「{sname}」human_stop 未知让位类「{hs}」，可选 {sorted(ALL_CLASSES)}")
            exp = step.get("expect")
            if exp is not None:
                if not isinstance(exp, dict):
                    raise ValueError(f"步「{sname}」expect 必须是对象")
                unknown_exp = set(exp) - _EXPECT_KEYS
                if unknown_exp:
                    raise ValueError(
                        f"步「{sname}」expect 含未知子键 {sorted(unknown_exp)}"
                        f"（可选 {sorted(_EXPECT_KEYS)}），拒绝加载")
        return cls(name=d["name"], steps=list(d.get("steps", [])), raw=d)


def check_expectation(step: dict, obs: Observation) -> str | None:
    """返回 None 表示符合预期；否则返回不符说明（触发让位）。"""
    exp = step.get("expect") or {}
    if "url_contains" in exp and exp["url_contains"] not in obs.url:
        return f"URL 不含预期「{exp['url_contains']}」，实际 {obs.url}"
    if "ax_contains" in exp:
        if not any(exp["ax_contains"] in t for t in obs.ax_texts()):
            return f"页面元素不含预期「{exp['ax_contains']}」"
    if "title_contains" in exp and exp["title_contains"] not in obs.title:
        return f"标题不含预期「{exp['title_contains']}」，实际「{obs.title}」"
    return None


def resolve_action(step: dict, obs: Observation) -> tuple[Action | None, object | None, str | None]:
    """把 step.action 解析成可执行 Action。
    返回 (action, target_node, error)。target_name 定位取 AX 元素坐标；
    定位失败返回 error（上层计低置信并让位）。"""
    spec = step.get("action") or {}
    kind = spec.get("kind")
    if kind is None:
        return None, None, None  # 纯观测步
    target = None
    x, y = spec.get("x"), spec.get("y")
    if "target_name" in spec:
        target = obs.find(spec["target_name"], spec.get("target_role"))
        if target is None:
            return None, None, f"定位不到目标「{spec['target_name']}」"
        if target.x is None or target.y is None:
            return None, target, f"目标「{spec['target_name']}」无几何坐标"
        x, y = target.x, target.y
    if kind == "click":
        return Action.click(x, y), target, None
    if kind == "type":
        return Action.type(x, y, spec.get("text", "")), target, None
    if kind == "navigate":
        if not spec.get("url"):
            return None, target, "navigate 缺 url"
        return Action.navigate(spec["url"]), target, None
    if kind == "scroll":
        return Action.scroll(spec.get("direction", "down"), spec.get("distance", 300)), target, None
    if kind == "wait":
        return Action.wait(spec.get("seconds", 1)), target, None
    if kind == "key":
        if not spec.get("key"):
            return None, target, "key 缺 key"
        return Action.press(spec["key"]), target, None
    if kind == "upload":
        if not spec.get("path"):
            return None, target, "upload 缺 path"
        return Action.upload(x, y, spec["path"]), target, None
    if kind == "download":
        return Action.download(x, y), target, None
    return None, target, f"未知动作 kind: {kind}"
