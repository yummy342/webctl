"""§2 坐标归一化 + §8 playbook 预期检查/动作解析（纯逻辑部分）。"""
from webctl.observe import AXNode, normalize, denormalize
from webctl.playbook import Playbook, check_expectation, resolve_action

from conftest import make_obs


def test_normalize_roundtrip():
    assert normalize(0, 1280) == 0
    assert normalize(1280, 1280) == 1000
    assert normalize(640, 1280) == 500
    assert normalize(99999, 1280) == 1000  # 钳制
    assert denormalize(500, 1280) == 640.0


def test_expectation_checks():
    obs = make_obs(url="https://x.com/api-keys", title="Key 管理",
                   texts=["创建 Key"])
    assert check_expectation({"expect": {"url_contains": "/api-keys"}}, obs) is None
    assert check_expectation({"expect": {"ax_contains": "创建"}}, obs) is None
    assert check_expectation({"expect": {"url_contains": "/billing"}}, obs) is not None
    assert check_expectation({"expect": {"ax_contains": "不存在"}}, obs) is not None
    assert check_expectation({}, obs) is None


def test_resolve_action_by_target_name():
    obs = make_obs(texts=[])
    obs.ax_tree.append(AXNode(role="button", name="创建 Key", x=300, y=200))
    action, target, err = resolve_action(
        {"action": {"kind": "click", "target_name": "创建 Key"}}, obs)
    assert err is None and action.kind == "click"
    assert (action.x, action.y) == (300, 200)
    assert target.name == "创建 Key"


def test_resolve_action_missing_target_reports_error():
    obs = make_obs()
    action, target, err = resolve_action(
        {"action": {"kind": "click", "target_name": "不存在的按钮"}}, obs)
    assert action is None and err is not None


def test_resolve_action_target_without_geometry():
    obs = make_obs()
    obs.ax_tree.append(AXNode(role="button", name="无坐标按钮"))  # x/y 为 None
    action, target, err = resolve_action(
        {"action": {"kind": "click", "target_name": "无坐标按钮"}}, obs)
    assert action is None and "几何" in err


def test_playbook_from_dict():
    pb = Playbook.from_dict({"name": "demo", "steps": [{"name": "s1"}]})
    assert pb.name == "demo" and len(pb.steps) == 1


def test_playbook_rejects_unknown_step_field():
    """P0-2 加载期校验：声明了没人执行的字段必须报错，不许静默跳过。"""
    import pytest
    with pytest.raises(ValueError):
        Playbook.from_dict({"name": "x", "steps": [
            {"name": "s", "harvset": {"vault_name": "k"}}]})


def test_playbook_harvest_requires_vault_name():
    import pytest
    with pytest.raises(ValueError):
        Playbook.from_dict({"name": "x", "steps": [
            {"name": "s", "harvest": {"via": "credentials.harvest_key"}}]})


def test_playbook_rejects_unknown_action_kind():
    import pytest
    with pytest.raises(ValueError):
        Playbook.from_dict({"name": "x", "steps": [
            {"name": "s", "action": {"kind": "teleport"}}]})


def test_playbook_keeps_raw_for_probe_config():
    pb = Playbook.from_dict({"name": "x", "steps": [],
                             "probe": {"url": "https://e.com/models"}})
    assert pb.raw["probe"]["url"] == "https://e.com/models"
