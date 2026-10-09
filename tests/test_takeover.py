"""§4 接管状态机：合法迁移、非法迁移拒绝、交接 diff。"""
import pytest

from webctl.takeover import State, TakeoverError, TakeoverMachine, diff_observations
from webctl.observe import AXNode

from conftest import make_obs


def test_human_takeover_release_handoff_cycle():
    m = TakeoverMachine()
    assert m.state is State.AI_RUNNING and m.ai_may_act
    m.human_takeover(make_obs(texts=["保存"]))
    assert m.state is State.HUMAN_CONTROL and not m.ai_may_act
    m.human_release()
    assert m.state is State.HANDOFF
    d = m.complete_handoff(make_obs(texts=["保存", "已保存"]))
    assert m.state is State.AI_RUNNING and m.ai_may_act
    assert any("已保存" in a for a in d.added)


def test_ai_yield_path():
    m = TakeoverMachine()
    m.ai_yield(make_obs(), "命中实名让位")
    assert m.state is State.WAITING_HUMAN
    assert m.yield_reason == "命中实名让位"
    m.human_release()
    m.complete_handoff(make_obs())
    assert m.yield_reason is None


def test_illegal_transitions_raise():
    m = TakeoverMachine()
    with pytest.raises(TakeoverError):
        m.human_release()  # AI_RUNNING 不能直接交回
    with pytest.raises(TakeoverError):
        m.complete_handoff(make_obs())  # 非 HANDOFF 不能完成交接
    m.human_takeover(make_obs())
    with pytest.raises(TakeoverError):
        m.ai_yield(make_obs(), "x")  # 人控期间 AI 不能再让位
    with pytest.raises(TakeoverError):
        m.human_takeover(make_obs())  # 不能重复接管


def test_diff_detects_url_change_and_removals():
    before = make_obs(url="https://a.com/1", texts=["登录", "取消"])
    after = make_obs(url="https://a.com/2", texts=["登录"])
    d = diff_observations(before, after)
    assert d.changed_url
    assert any("取消" in r for r in d.removed)
    assert "变了" in d.describe()


def test_action_history_kept():
    m = TakeoverMachine()
    m.record_action({"kind": "click"})
    m.record_action({"kind": "type"})
    assert len(m.action_history) == 2
