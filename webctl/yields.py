"""§5 让位触发器：AI 自动停下等人工，不硬闯。

定版 9 类（v1.1 §5 的 8 类 + 2026-10-08 审查轮新增第 9 类；不合并原则不变
——支付测「走到钱的决策点」、不可逆测「撞上破坏性按钮」，口径不同，合并后
统计不可恢复；新增授权确认是给 F4 三停靠之三补一个家，不是合并）：
  1 captcha 图形/滑块验证码   2 login_wall 登录墙     3 payment 支付页
  4 low_confidence 低置信     5 risk_control 风控     6 real_name 实名（硬）
  7 otp 一次性验证码（硬）     8 irreversible 不可逆动作（硬）
  9 authorization 授权确认（规格层条件；代操作策略层强制停靠）

分层（2026-10-08 裁定）：6/7/8 是**规格级硬让位**（谁用这套件都成立）；
其余是条件让位。产品可经 ProductPolicy 追加**产品级强制停靠**
（如代操作产品把 login_wall 升为强制停靠——技术上可注入凭据，但 F4 三停靠
定版要求交还本人）。让位引擎因此分两层：规格检测 + 产品策略。

刻度尺：按 9 类分别统计介入次数；「待自动化」分母 = 除 real_name、otp
外的 7 类（这两类本就不该被自动化掉）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .browser import last_nav_status
from .observe import AXNode, Observation

CLS_CAPTCHA = "captcha"
CLS_LOGIN = "login_wall"
CLS_PAYMENT = "payment"
CLS_LOW_CONF = "low_confidence"
CLS_RISK = "risk_control"
CLS_REALNAME = "real_name"
CLS_OTP = "otp"
CLS_IRREVERSIBLE = "irreversible"
CLS_CONSENT = "authorization"

ALL_CLASSES = [CLS_CAPTCHA, CLS_LOGIN, CLS_PAYMENT, CLS_LOW_CONF,
               CLS_RISK, CLS_REALNAME, CLS_OTP, CLS_IRREVERSIBLE, CLS_CONSENT]
SPEC_HARD = {CLS_REALNAME, CLS_OTP, CLS_IRREVERSIBLE}
NON_AUTOMATABLE = {CLS_REALNAME, CLS_OTP}  # 不计入「待自动化」分母

_CAPTCHA_WORDS = ["captcha", "人机验证", "滑块", "拖动滑块", "geetest", "hcaptcha", "recaptcha"]
_REALNAME_WORDS = ["实名认证", "身份证", "上传身份证", "人脸识别", "刷脸", "实人认证"]
_OTP_WORDS = ["短信验证码", "邮箱验证码", "动态验证码", "一次性验证码", "one-time code", "验证码已发送"]
_PAYMENT_WORDS = ["收银台", "结算", "立即付款", "确认付款", "checkout", "支付金额", "应付"]
_RISK_WORDS = ["异常流量", "访问受限", "账号已被封", "操作过于频繁", "too many requests", "rate limit"]
_IRREVERSIBLE_WORDS = ["删除", "吊销", "解绑", "注销", "清空", "撤销授权",
                       "delete", "revoke", "unbind", "remove", "destroy"]
# 授权确认（第 9 类）：同意授权类按钮/同意页。主判据是 playbook 的 human_stop
# 标注与动作目标命中；页面级只作补充网（须与「访问你的」类范围词共现）。
_CONSENT_WORDS = ["同意授权", "授权确认", "同意并授权", "授权并继续", "确认授权",
                  "authorize access", "grant permission", "allow access"]
_CONSENT_SCOPE_WORDS = ["访问你的", "请求访问", "申请获取", "获取你的", "权限范围"]
_AMOUNT_RE = re.compile(r"[¥￥$]\s?\d+(\.\d{2})?")
_LOGIN_URL_RE = re.compile(r"/(login|signin|sign-in|auth/login|sso)([/?#]|$)", re.I)


@dataclass
class Signals:
    """从活页面抽出的检测信号（与浏览器解耦，便于单测）。"""
    page_text: str = ""
    http_status: int | None = None
    has_password_field: bool = False
    has_file_input: bool = False
    has_otp_input: bool = False          # autocomplete=one-time-code 等
    has_saved_credential: bool = False   # 本站有 vault 凭据（登录墙条件用）

    @classmethod
    def from_page(cls, page, *, has_saved_credential: bool = False) -> "Signals":
        text = ""
        status = None
        has_pw = has_file = has_otp = False
        try:
            text = page.inner_text("body", timeout=1500)
        except Exception:
            pass
        # 风控判据靠真实导航状态码，不是靠页面文案猜。此前这里恒为 None，
        # check_page 的 429 分支在真实回路里永不触发（只有单测手工构造才亮）。
        try:
            status = last_nav_status(page)
        except Exception:
            status = None
        try:
            has_pw = page.locator("input[type='password']").count() > 0
            has_file = page.locator("input[type='file']").count() > 0
            has_otp = page.locator("input[autocomplete='one-time-code']").count() > 0
        except Exception:
            pass
        return cls(page_text=text, http_status=status, has_password_field=has_pw,
                   has_file_input=has_file, has_otp_input=has_otp,
                   has_saved_credential=has_saved_credential)


@dataclass
class ProductPolicy:
    """产品级强制停靠：在规格条件让位之上，指定哪些类在本产品里必须停。"""
    mandatory_stops: set[str] = field(default_factory=set)

    @classmethod
    def daichaozuo(cls) -> "ProductPolicy":
        """选型线代操作产品：三停靠（登录/实名/授权）中的登录在此升格。"""
        return cls(mandatory_stops={CLS_LOGIN, CLS_CONSENT})


@dataclass
class YieldDecision:
    cls: str
    hard: bool              # 硬让位：必须交人；条件让位：停下等确认，但可由策略放行
    source: str             # "spec" | "policy"
    reason: str
    suppress_screenshot: bool = False  # 实名类：让位期暂停截图留存

    def describe(self) -> str:
        return f"[{self.cls}|{'硬' if self.hard else '条件'}|{self.source}] {self.reason}"


@dataclass
class ConfidenceTracker:
    """低置信信号：连续定位失败计数 + 动作后页面无变化计数。"""
    locate_failures: int = 0
    no_change_streak: int = 0

    def note_locate(self, ok: bool) -> None:
        self.locate_failures = 0 if ok else self.locate_failures + 1

    def note_change(self, changed: bool) -> None:
        self.no_change_streak = 0 if changed else self.no_change_streak + 1

    def decision(self) -> YieldDecision | None:
        if self.locate_failures >= 3:
            return YieldDecision(CLS_LOW_CONF, False, "spec",
                                 f"同一元素连续 {self.locate_failures} 次定位失败")
        if self.no_change_streak >= 2:
            return YieldDecision(CLS_LOW_CONF, False, "spec",
                                 f"连续 {self.no_change_streak} 次动作后页面无变化")
        return None


def _hit(words: list[str], text: str) -> str | None:
    low = text.lower()
    for w in words:
        if w.lower() in low:
            return w
    return None


class YieldEngine:
    def __init__(self, policy: ProductPolicy | None = None):
        self.policy = policy or ProductPolicy()
        self.confidence = ConfidenceTracker()
        self.stats: dict[str, int] = {c: 0 for c in ALL_CLASSES}

    # —— 页面级检测（动作前）——
    def check_page(self, obs: Observation, sig: Signals) -> YieldDecision | None:
        text = (obs.title + "\n" + "\n".join(obs.ax_texts()) + "\n" + sig.page_text)
        d: YieldDecision | None = None
        # 顺序即优先级：实名 > OTP > 风控 > 验证码 > 支付 > 登录墙
        w = _hit(_REALNAME_WORDS, text)
        if w:
            d = YieldDecision(CLS_REALNAME, True, "spec", f"页面含实名要素「{w}」",
                              suppress_screenshot=True)
        elif sig.has_otp_input or (w := _hit(_OTP_WORDS, text)):
            d = YieldDecision(CLS_OTP, True, "spec", "一次性验证码（码在用户设备上）")
        elif sig.http_status == 429 or (w := _hit(_RISK_WORDS, text)):
            d = YieldDecision(CLS_RISK, False, "spec", f"风控信号「{w or 'HTTP 429'}」")
        elif (w := _hit(_CAPTCHA_WORDS, text)):
            d = YieldDecision(CLS_CAPTCHA, False, "spec", f"验证码信号「{w}」")
        elif (w := _hit(_PAYMENT_WORDS, text)) and _AMOUNT_RE.search(text):
            d = YieldDecision(CLS_PAYMENT, False, "spec", f"支付页信号「{w}」+ 金额")
        elif (w := _hit(_CONSENT_WORDS, text)) and _hit(_CONSENT_SCOPE_WORDS, text):
            d = YieldDecision(CLS_CONSENT, False, "spec", f"授权同意页信号「{w}」")
        elif (_LOGIN_URL_RE.search(obs.url) or sig.has_password_field) and not sig.has_saved_credential:
            d = YieldDecision(CLS_LOGIN, False, "spec", "登录墙且无本站保存凭据")
        if d is None:
            d = self.confidence.decision()
        if d is not None:
            d = self._apply_policy(d, obs, sig)
            if d is not None:
                self.stats[d.cls] = self.stats.get(d.cls, 0) + 1
        return d

    def _apply_policy(self, d: YieldDecision, obs: Observation, sig: Signals) -> YieldDecision | None:
        """产品策略层：被产品列为强制停靠的类（代操作：登录、授权确认）
        一律升格为硬让位。（登录在有凭据时 check_page 不产生决策，
        那条路径由 check_policy_stops 补上。）"""
        if d.cls in self.policy.mandatory_stops and not d.hard:
            return YieldDecision(d.cls, True, "policy", d.reason + "；产品策略强制停靠", d.suppress_screenshot)
        return d

    def check_policy_stops(self, obs: Observation, sig: Signals) -> YieldDecision | None:
        """单独给 session 用：页面本身不命中条件让位时，查产品强制停靠
        （登录墙 + 有凭据 → 规格不让，代操作产品要让）。"""
        if CLS_LOGIN in self.policy.mandatory_stops:
            if _LOGIN_URL_RE.search(obs.url) or sig.has_password_field:
                self.stats[CLS_LOGIN] = self.stats.get(CLS_LOGIN, 0) + 1
                return YieldDecision(CLS_LOGIN, True, "policy",
                                     "登录为身份行为，产品策略强制停靠交还本人")
        return None

    # —— 动作级检测（点之前）——
    def check_action_target(self, node: AXNode | None) -> YieldDecision | None:
        """不可逆动作类：目标元素名含破坏性语义 → 硬让位，与置信度无关。"""
        if node is None or not node.name:
            return None
        w = _hit(_IRREVERSIBLE_WORDS, node.name)
        if w:
            self.stats[CLS_IRREVERSIBLE] = self.stats.get(CLS_IRREVERSIBLE, 0) + 1
            return YieldDecision(CLS_IRREVERSIBLE, True, "spec",
                                 f"目标「{node.name}」含不可逆语义「{w}」，须人确认")
        w = _hit(_CONSENT_WORDS, node.name)
        if w:
            self.stats[CLS_CONSENT] = self.stats.get(CLS_CONSENT, 0) + 1
            d = YieldDecision(CLS_CONSENT, False, "spec",
                              f"目标「{node.name}」是授权确认动作「{w}」")
            if CLS_CONSENT in self.policy.mandatory_stops:
                d = YieldDecision(d.cls, True, "policy", d.reason + "；产品策略强制停靠")
            return d
        return None

    # —— 刻度尺 ——
    def automatable_stats(self) -> dict[str, int]:
        """「待自动化」分母口径：9 类除实名、OTP 外的 7 类。"""
        return {k: v for k, v in self.stats.items() if k not in NON_AUTOMATABLE}
