"""§5 让位引擎：8 类齐全、硬/条件分层、策略层（登录产品级强制停靠）、
动作级不可逆检测、刻度尺分母口径（除实名/OTP 外 6 类）。"""
from webctl.observe import AXNode
from webctl.yields import (
    ALL_CLASSES, SPEC_HARD, ProductPolicy, Signals, YieldEngine,
    CLS_CAPTCHA, CLS_CONSENT, CLS_IRREVERSIBLE, CLS_LOGIN, CLS_LOW_CONF, CLS_OTP,
    CLS_PAYMENT, CLS_REALNAME, CLS_RISK,
)

from conftest import make_obs


def eng(policy=None):
    return YieldEngine(policy)


def test_nine_classes_defined():
    # v1.1 §5 的 8 类 + 2026-10-08 审查轮新增第 9 类授权确认（不合并原则不变）
    assert len(ALL_CLASSES) == 9
    assert SPEC_HARD == {CLS_REALNAME, CLS_OTP, CLS_IRREVERSIBLE}


def test_realname_is_hard_and_suppresses_screenshot():
    d = eng().check_page(make_obs(texts=["请完成实名认证，上传身份证"]), Signals())
    assert d.cls == CLS_REALNAME and d.hard and d.source == "spec"
    assert d.suppress_screenshot


def test_otp_is_hard():
    d = eng().check_page(make_obs(texts=["短信验证码已发送"]), Signals())
    assert d.cls == CLS_OTP and d.hard
    d2 = eng().check_page(make_obs(), Signals(has_otp_input=True))
    assert d2.cls == CLS_OTP


def test_captcha_conditional():
    d = eng().check_page(make_obs(texts=["请拖动滑块完成人机验证"]), Signals())
    assert d.cls == CLS_CAPTCHA and not d.hard


def test_payment_needs_keyword_and_amount():
    d = eng().check_page(make_obs(texts=["收银台 应付 ¥199.00"]), Signals())
    assert d.cls == CLS_PAYMENT and not d.hard
    # 只有关键词没有金额 → 不误报支付
    assert eng().check_page(make_obs(texts=["结算说明"]), Signals()) is None


def test_risk_control_by_status_and_words():
    d = eng().check_page(make_obs(), Signals(http_status=429))
    assert d.cls == CLS_RISK
    d2 = eng().check_page(make_obs(texts=["系统检测到异常流量"]), Signals())
    assert d2.cls == CLS_RISK


def test_login_wall_spec_conditional_without_credential():
    d = eng().check_page(make_obs(url="https://x.com/login"), Signals())
    assert d.cls == CLS_LOGIN and not d.hard and d.source == "spec"


def test_login_wall_no_yield_with_saved_credential_spec_only():
    e = eng()
    assert e.check_page(make_obs(url="https://x.com/login"),
                        Signals(has_saved_credential=True)) is None
    assert e.check_policy_stops(make_obs(url="https://x.com/login"),
                                Signals(has_saved_credential=True)) is None


def test_login_becomes_hard_policy_stop_for_daichaozuo():
    """裁定的核心：代操作产品里，登录即使有保存凭据也强制停靠（产品层）。"""
    e = eng(ProductPolicy.daichaozuo())
    obs = make_obs(url="https://x.com/login")
    sig = Signals(has_saved_credential=True)
    assert e.check_page(obs, sig) is None  # 规格层不让
    d = e.check_policy_stops(obs, sig)     # 产品层让，且为硬
    assert d.cls == CLS_LOGIN and d.hard and d.source == "policy"


def test_login_policy_upgrades_spec_decision():
    e = eng(ProductPolicy.daichaozuo())
    d = e.check_page(make_obs(url="https://x.com/login"), Signals())
    assert d.hard and d.source == "policy"


def test_irreversible_target_is_hard_regardless_of_confidence():
    e = eng()
    node = AXNode(role="button", name="删除此 API Key", x=10, y=10)
    d = e.check_action_target(node)
    assert d.cls == CLS_IRREVERSIBLE and d.hard
    ok = AXNode(role="button", name="创建 API Key", x=10, y=10)
    assert e.check_action_target(ok) is None
    assert e.check_action_target(None) is None


def test_low_confidence_after_repeated_locate_failures():
    e = eng()
    for _ in range(3):
        e.confidence.note_locate(False)
    d = e.check_page(make_obs(), Signals())
    assert d.cls == CLS_LOW_CONF


def test_priority_realname_beats_captcha():
    d = eng().check_page(make_obs(texts=["实名认证", "拖动滑块"]), Signals())
    assert d.cls == CLS_REALNAME


def test_automatable_denominator_excludes_realname_and_otp():
    e = eng()
    e.check_page(make_obs(texts=["实名认证"]), Signals())
    e.check_page(make_obs(texts=["短信验证码已发送"]), Signals())
    auto = e.automatable_stats()
    assert CLS_REALNAME not in auto and CLS_OTP not in auto
    assert len(auto) == 7


def test_consent_target_conditional_at_spec_level():
    e = eng()
    node = AXNode(role="button", name="同意授权", x=10, y=10)
    d = e.check_action_target(node)
    assert d.cls == CLS_CONSENT and not d.hard and d.source == "spec"


def test_consent_becomes_hard_under_daichaozuo_policy():
    """F4 三停靠之三：授权确认在代操作产品里强制停靠。"""
    e = eng(ProductPolicy.daichaozuo())
    node = AXNode(role="button", name="同意授权", x=10, y=10)
    d = e.check_action_target(node)
    assert d.cls == CLS_CONSENT and d.hard and d.source == "policy"


def test_consent_page_needs_scope_words():
    d = eng().check_page(make_obs(texts=["授权确认", "该应用将访问你的通讯录"]), Signals())
    assert d.cls == CLS_CONSENT
    # 只有授权词、没有范围词 → 不误报（普通页面常有「授权」字样）
    assert eng().check_page(make_obs(texts=["授权经销商名单"]), Signals()) is None


def test_irreversible_beats_consent():
    e = eng()
    node = AXNode(role="button", name="撤销授权", x=10, y=10)
    d = e.check_action_target(node)
    assert d.cls == CLS_IRREVERSIBLE
