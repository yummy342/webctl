"""§6 审计：actor 必填且只能 ai|human、敏感时刻截图抑制、回放一一对应。"""
import pytest

from webctl.audit import AuditLog, sensitive_reason

from conftest import make_obs

SECRET_TOKEN = "sk-abcdefgh12345678ijklmnop"


def test_actor_must_be_ai_or_human(tmp_path):
    log = AuditLog.open(tmp_path, "s1")
    with pytest.raises(ValueError):
        log.record(actor="robot", kind="action")
    log.record(actor="ai", kind="action")
    log.record(actor="human", kind="human_action")
    entries = AuditLog.replay(log.path)
    assert [e["actor"] for e in entries] == ["ai", "human"]


def test_normal_page_keeps_thumbnail(tmp_path):
    log = AuditLog.open(tmp_path, "s2")
    e = log.record(actor="ai", kind="action", obs=make_obs(texts=["普通页面"]))
    assert e["screenshot_thumb"]  # 有缩略图
    assert e["screenshot_suppressed"] is None


def test_password_field_suppresses_screenshot(tmp_path):
    log = AuditLog.open(tmp_path, "s3")
    e = log.record(actor="ai", kind="action", obs=make_obs(texts=["密码"]),
                   has_password_field=True)
    assert e["screenshot_thumb"] is None
    assert "password" in e["screenshot_suppressed"]


def test_yield_suppress_flag_suppresses_screenshot(tmp_path):
    log = AuditLog.open(tmp_path, "s4")
    e = log.record(actor="ai", kind="yield", obs=make_obs(texts=["实名认证"]),
                   yield_info={"cls": "real_name", "suppress_screenshot": True})
    assert e["screenshot_thumb"] is None
    assert e["yield"]["cls"] == "real_name"


def test_key_display_page_suppressed_by_content():
    obs = make_obs(title="API Key", texts=[f"您的 API Key 已生成：{SECRET_TOKEN}"])
    assert sensitive_reason(obs) is not None


def test_sensitive_net_calibrated_on_real_wordings():
    """P0-1 标定集（审查实测的 5 种真实措辞）：0.1.0 只有第 1 种会响，
    0.2.0 必须全响——仪器先用已知答案标定，再上岗。"""
    cases = [
        ("控制台", [f"您的 API Key 已生成：{SECRET_TOKEN}"]),
        ("API-KEY", ["请妥善保存，仅显示一次", SECRET_TOKEN]),
        ("密钥", ["密钥：", SECRET_TOKEN]),
        ("AccessKey", ["AccessKey Secret", SECRET_TOKEN]),
        ("凭据", ["凭据新建成功", SECRET_TOKEN]),
        ("密钥", ["密钥如下，请保存"]),  # 无令牌串也要靠措辞网兜住
    ]
    for title, texts in cases:
        assert sensitive_reason(make_obs(title=title, texts=texts)) is not None, (title, texts)
    # 反例：普通页面不许误抑
    assert sensitive_reason(make_obs(title="首页", texts=["欢迎使用控制台"])) is None


def test_replay_sequence_matches_records(tmp_path):
    log = AuditLog.open(tmp_path, "s5")
    for i in range(3):
        log.record(actor="ai", kind="action", action={"kind": "click", "i": i},
                   obs=make_obs())
    entries = AuditLog.replay(log.path)
    assert [e["seq"] for e in entries] == [1, 2, 3]
    assert [e["action"]["i"] for e in entries] == [0, 1, 2]
