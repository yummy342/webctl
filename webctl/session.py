"""会话胶水：观测 → 让位检查 → 动作 → 审计，把套件串成一条 agent 回路。

单步流程（act）：
  1. 接管状态机不在 AI_RUNNING → 拒绝（§4：人操作期间 AI 不发动作）；
  2. 观测当前页；§5 页面级检测（含产品强制停靠）命中 → 让位 + 审计，返回决策；
  3. 动作目标先过不可逆/授权确认检测（§5 动作级）；
  4. 执行；被执行器拒绝（越界等）→ 审计记 action_rejected 留痕后抛错（P2-2）；
  5. 记审计：本步若涉及凭证（动作含占位符）→ 通道宣告抑图（P0-1），
     不靠页面内容启发式猜。

playbook 步骤的三种特殊形态（runner 必须真处理，不许静默跳过）：
  - "harvest": {...} —— 一次性 key 回收步：凭证通道读一次直入 vault + 探针
    验活；整步审计强制抑图；**取不到值即停**（后续步——尤其关弹窗——绝不许
    在没收回 key 时继续），验活不过按 §7 语义标 unverified 并继续（P0-2）；
  - "human_stop": "authorization" —— 声明式人工停靠：到此步直接按该类让位，
    不执行动作（playbook 作者知道哪一步是授权确认，不靠关键词猜）；
  - "sensitive": true —— 本步审计抑图（通道宣告）。

标签页策略（§4 R4）：context 新开的页一律收编进 session（audit tab_opened）；
页关闭记 tab_closed；**当前页被关 → 转 WAITING_HUMAN**（人把页面关了，
AI 不许对着已关的页硬续）。
人的动作经 human_act() 记录（actor=human），且必须用**当场新鲜观测**，
不许拿接管前的旧截图充数（P2-3，R7 证据链）。
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Page

from .actions import Action, ActionError, ActionExecutor, contains_placeholder
from .audit import AuditLog
from .credentials import FileVault, ProbeResult, harvest_key
from .observe import Observation, observe
from .playbook import Playbook, check_expectation, resolve_action
from .takeover import TakeoverMachine
from .yields import Signals, YieldDecision, YieldEngine

_TOKEN_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9_-]{32,})\b")

# 全局直连 opener：空 ProxyHandler = 明确禁用一切代理（含环境变量与系统设置）
_DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class AgentSession:
    page: Page
    executor: ActionExecutor
    yields: YieldEngine
    takeover: TakeoverMachine = field(default_factory=TakeoverMachine)
    audit: AuditLog | None = None
    last_observation: Observation | None = None
    vault: FileVault | None = None
    probe_config: dict | None = None       # playbook 顶层 probe（harvest 验活用）
    pages: list = field(default_factory=list)  # 收编的全部标签页（含主页面）
    harvest_window: bool = False           # harvest 执行窗口期：全程抑图
    # AI 动作执行窗口：动作期间发生的标签页关闭归给 ai，其余归给 human。
    # 原来 _on_tab_closed 无条件写 actor="human"，AI 点出来的 window.close()
    # 也被记成人工——对主打归属证据链的产品是实质缺陷。
    _ai_busy: bool = field(default=False, repr=False, init=False)

    @classmethod
    def create(cls, page: Page, *, base_dir: Path, session_id: str,
               engine: YieldEngine, vault=None,
               upload_dir: Path | None = None,
               download_dir: Path | None = None) -> "AgentSession":
        s = cls(
            page=page,
            executor=ActionExecutor(page, vault=vault, upload_dir=upload_dir,
                                    download_dir=download_dir),
            yields=engine,
            audit=AuditLog.open(base_dir, session_id),
            vault=vault,
        )
        s._adopt_tabs()
        return s

    # —— 标签页收编（§4 R4）——
    def _adopt_tabs(self) -> None:
        ctx = self.page.context
        self.pages = list(ctx.pages) or [self.page]

        def on_page(new_page):
            self.pages.append(new_page)
            if self.audit:
                self.audit.record(actor="ai", kind="tab_opened",
                                  action={"url": new_page.url})
            new_page.on("close", lambda p=new_page: self._on_tab_closed(p))

        ctx.on("page", on_page)
        self.page.on("close", lambda p=self.page: self._on_tab_closed(p))

    def _on_tab_closed(self, closed) -> None:
        if closed in self.pages:
            self.pages.remove(closed)
        if self.audit:
            # 归属按「关闭发生时 AI 是否正在执行动作」判，不再无条件算人工。
            # 残留局限：页面脚本自关且不在动作窗口内，仍会算成 human——
            # 进程内无法区分真实点击者，这一点写进已知取舍，不假装能证。
            self.audit.record(actor="ai" if self._ai_busy else "human",
                              kind="tab_closed", action={"url": closed.url})
        if closed is self.page and self.takeover.ai_may_act:
            obs = self.last_observation or Observation(
                url=closed.url, title="", screenshot_png=b"")
            self.takeover.ai_yield(obs, "当前标签页被关闭，转人工")

    def switch_page(self, page) -> None:
        """把活动页切到已收编的标签页（弹窗流/多标签流程用）。"""
        if page not in self.pages:
            self.pages.append(page)
        self.page = page
        self.executor.page = page

    # —— 观测 ——
    def observe_now(self, *, has_saved_credential: bool = False) -> tuple[Observation, Signals]:
        obs = observe(self.page)
        sig = Signals.from_page(self.page, has_saved_credential=has_saved_credential)
        self.last_observation = obs
        return obs, sig

    # —— AI 动作 ——
    def act(self, action: Action, *, target_node=None,
            has_saved_credential: bool = False,
            suppress_reason: str | None = None) -> YieldDecision | dict:
        """执行一条 AI 动作；被让位拦截时返回 YieldDecision。"""
        if not self.takeover.ai_may_act:
            raise RuntimeError(f"当前状态 {self.takeover.state.value}，AI 不得发动作")
        obs, sig = self.observe_now(has_saved_credential=has_saved_credential)
        d = self.yields.check_page(obs, sig) or self.yields.check_policy_stops(obs, sig)
        if d is not None:
            self.takeover.ai_yield(obs, d.describe())
            self._audit_yield(d, obs, sig)
            return d
        d = self.yields.check_action_target(target_node)
        if d is not None:
            self.takeover.ai_yield(obs, d.describe())
            self._audit_yield(d, obs, sig)
            return d
        before = obs.summary_hash()
        self._ai_busy = True
        try:
            result = self.executor.execute(action)
        except ActionError as e:
            if self.audit:  # 越界被拒也要留痕（§3 越界留痕）
                self.audit.record(actor="ai", kind="action_rejected",
                                  action={"kind": action.kind, "error": str(e)}, obs=obs)
            raise
        finally:
            # 动作窗口必须在两条出口上都关掉，否则一次失败的动作会让后续
            # 所有标签页关闭都被误记成 ai。异常路径已 record 过 action_rejected，
            # 归因窗口在这里收。
            self._ai_busy = False
        self.takeover.record_action({"kind": action.kind, "url": result.get("url")})
        if target_node is not None:
            self.yields.confidence.note_locate(True)  # 定位成功清零失败计数（P3）
        after_obs, _ = self.observe_now(has_saved_credential=has_saved_credential)
        changed = after_obs.summary_hash() != before
        self.yields.confidence.note_change(changed)
        if self.audit:
            # 凭证通道宣告：动作含占位符的当步、harvest 窗口期 → 强制抑图
            suppress = suppress_reason
            if suppress is None and (self.harvest_window or contains_placeholder(action.text)):
                suppress = "credential_channel（凭证注入/harvest 窗口期）"
            self.audit.record(actor="ai", kind="action",
                              action={"kind": action.kind, "x": action.x, "y": action.y,
                                      "url": action.url, "text": action.text,
                                      "downloaded": result.get("downloaded")},
                              obs=after_obs,
                              obs_diff="changed" if changed else "no-change",
                              has_password_field=sig.has_password_field,
                              suppress=suppress)
        return result

    # —— playbook ——
    def run_playbook(self, pb: Playbook) -> list[dict]:
        """逐步执行；任一步预期不符/定位失败/命中让位/harvest 取值失败即停。"""
        if pb.raw.get("probe"):
            self.probe_config = pb.raw["probe"]
        done: list[dict] = []
        for step in pb.steps:
            obs, sig = self.observe_now()
            mismatch = check_expectation(step, obs)
            if mismatch:
                d = YieldDecision("low_confidence", False, "spec",
                                  f"playbook_mismatch[{pb.name}/{step.get('name')}]: {mismatch}")
                self.yields.stats["low_confidence"] = self.yields.stats.get("low_confidence", 0) + 1
                self.takeover.ai_yield(obs, d.describe())
                self._audit_yield(d, obs, sig)
                done.append({"step": step.get("name"), "ok": False, "stopped": d.describe()})
                return done
            # 声明式人工停靠（授权确认等）：作者标注 > 关键词猜测
            if step.get("human_stop"):
                cls_name = step["human_stop"]
                hard = cls_name in self.yields.policy.mandatory_stops or cls_name in (
                    "real_name", "otp", "irreversible")
                d = YieldDecision(cls_name, hard, "policy" if hard else "spec",
                                  f"playbook 声明停靠[{pb.name}/{step.get('name')}]")
                self.yields.stats[cls_name] = self.yields.stats.get(cls_name, 0) + 1
                self.takeover.ai_yield(obs, d.describe())
                self._audit_yield(d, obs, sig)
                done.append({"step": step.get("name"), "ok": False, "stopped": d.describe()})
                return done
            # 一次性 key 回收步（P0-2）：真执行，取值失败即停
            if step.get("harvest"):
                r = self._run_harvest_step(pb, step, obs, sig)
                done.append(r)
                if not r["ok"]:
                    return done
                continue
            action, target, err = resolve_action(step, obs)
            if err:
                self.yields.confidence.note_locate(False)
                d = self.yields.confidence.decision() or YieldDecision(
                    "low_confidence", False, "spec", f"playbook 定位失败: {err}")
                # 预期不符步（:176）与 human_stop 步（:188）都计了刻度尺，
                # 这条漏计 → automatable_stats 少算低置信介入。
                self.yields.stats[d.cls] = self.yields.stats.get(d.cls, 0) + 1
                self.takeover.ai_yield(obs, d.describe())
                self._audit_yield(d, obs, sig)
                done.append({"step": step.get("name"), "ok": False, "stopped": d.describe()})
                return done
            if action is None:  # 纯观测步
                done.append({"step": step.get("name"), "ok": True, "detail": "observed"})
                continue
            r = self.act(action, target_node=target,
                         suppress_reason="playbook_sensitive_step" if step.get("sensitive") else None)
            if isinstance(r, YieldDecision):
                done.append({"step": step.get("name"), "ok": False, "stopped": r.describe()})
                return done
            done.append({"step": step.get("name"), "ok": True})
        return done

    def _run_harvest_step(self, pb: Playbook, step: dict, obs, sig) -> dict:
        """harvest 步：窗口期内抑图；从页面受控读一次 → vault → 探针验活。
        取不到值 = 停（绝不放行后续步）；验活不过 = unverified，继续。"""
        spec = step["harvest"]
        name = step.get("name")
        if self.vault is None:
            # 其余停靠路径（预期不符/定位失败/取值失败/human_stop）都会把状态
            # 迁到 WAITING_HUMAN；这条原来直接 return，于是 playbook 停了、
            # takeover 还留在 AI_RUNNING，外层以为还能继续发动作。
            d = YieldDecision("low_confidence", True, "spec",
                              f"harvest 步要求 session 配置 vault，未配置"
                              f"[{pb.name}/{name}]——停")
            self.takeover.ai_yield(obs, d.describe())
            self._audit_yield(d, obs, sig)
            return {"step": name, "ok": False, "stopped": d.describe()}
        vault_name = spec.get("vault_name")
        pattern = re.compile(spec.get("extract_regex") or _TOKEN_RE.pattern)
        self.harvest_window = True
        try:
            def extract_once() -> str:
                text = ""
                try:
                    text = self.page.inner_text("body", timeout=2000)
                except Exception:
                    pass
                m = pattern.search(text) or pattern.search(
                    "\n".join(self.last_observation.ax_texts() if self.last_observation else []))
                return m.group(0) if m else ""

            outcome = harvest_key(vault_name, extract_once, self.vault,
                                  self._build_probe())
        except ValueError as e:  # 没读到值：key 还在弹窗里，绝不能继续关弹窗
            d = YieldDecision("low_confidence", True, "spec",
                              f"harvest 取值失败[{pb.name}/{name}]: {e}——停，等人处理")
            self.takeover.ai_yield(obs, d.describe())
            self._audit_yield(d, obs, sig)
            return {"step": name, "ok": False, "stopped": d.describe()}
        finally:
            self.harvest_window = False
        if self.audit:
            self.audit.record(actor="ai", kind="harvest", obs=obs,
                              action={"vault_name": vault_name, "status": outcome.status},
                              suppress="credential_channel（harvest 窗口期）")
        detail = "verified" if outcome.status == "verified" else (
            f"unverified（值已入 vault 不删）：{outcome.review_message}")
        return {"step": name, "ok": True, "detail": detail}

    def _build_probe(self):
        cfg = self.probe_config or {}
        url = cfg.get("url")
        method = cfg.get("method", "GET")
        expect = cfg.get("expect_status", 200)

        def probe(value: str) -> ProbeResult:
            if not url:
                return ProbeResult(ok=False, error_type="no_probe_config",
                                   detail="playbook 未配置 probe，按未验活处理")
            headers = {}
            auth = cfg.get("auth")
            if auth:
                # P1-6：auth 形如 "头名: 头值 <<vault:NAME>>"，按声明的头名设 header。
                # 旧代码无视头名、无条件塞 Authorization——X-API-Key 类探针会把真 key
                # 发到错误位置，探针必失败、key 被误标 unverified。
                # 占位符换成刚回收的真值（探针直连，不经 vault 读回）。
                v = re.sub(r"<<vault:[^>]+>>", value, auth)
                if ":" in v:
                    hname, hval = v.split(":", 1)
                    hname, hval = hname.strip(), hval.strip()
                    if not hname:
                        raise ValueError(f"probe auth 头名为空: {auth!r}")
                    headers[hname] = hval
                else:
                    # 无冒号：沿用旧行为，整体作 Authorization 值（向后兼容）
                    headers["Authorization"] = v.strip()
            req = urllib.request.Request(url, method=method, headers=headers)
            try:
                # 探针带的是刚回收的真 key（Authorization 头）——必须直连，
                # 绝不走系统代理：代理是中间人，§7 的凭证面不许经它；
                # 且本机 VPN 代理会把本地/控制台地址答成 503，失败形态
                # 酷似「key 无效」，会误导人删 key（复验单根因 B）。
                with _DIRECT_OPENER.open(req, timeout=10) as resp:
                    return ProbeResult(ok=resp.status == expect, http_status=resp.status)
            except urllib.error.HTTPError as e:
                return ProbeResult(ok=False, http_status=e.code,
                                   error_type="http_error", detail=str(e))
            except Exception as e:
                return ProbeResult(ok=False, error_type=type(e).__name__, detail=str(e))

        return probe

    # —— 人的动作（接管期间由 UI 层调用）——
    def human_act(self, description: str, *, suppress: str | None = None) -> None:
        # P1-4：人工路径也要走抑图通道。人在接管期若处理了可见的一次性 key
        #（如 harvest 失败后人工复制），调用方应传 suppress 宣告敏感期；
        # 不传则走内容启发式兜底（宁抑勿漏）。
        obs = observe(self.page)  # 当场新鲜观测，不许用接管前的旧图（P2-3）
        self.last_observation = obs
        # act()（:160）与 _audit_yield（:321）都传了 has_password_field，唯独
        # 这条人工路径没传 → 接管期人在密码框上操作，截图带明文密码进审计。
        # suppress 是调用方的显式宣告，has_password_field 是页面事实，两者都要。
        try:
            has_pw = Signals.from_page(self.page).has_password_field
        except Exception:
            has_pw = False
        if self.audit:
            self.audit.record(actor="human", kind="human_action",
                              action={"desc": description}, obs=obs,
                              has_password_field=has_pw,
                              suppress=suppress)

    def _audit_yield(self, d: YieldDecision, obs: Observation, sig: Signals) -> None:
        if self.audit:
            self.audit.record(actor="ai", kind="yield", obs=obs,
                              yield_info={"cls": d.cls, "hard": d.hard, "source": d.source,
                                          "reason": d.reason,
                                          "suppress_screenshot": d.suppress_screenshot},
                              has_password_field=sig.has_password_field)
