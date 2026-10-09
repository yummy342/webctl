"""§3 动作空间：校验先于执行、scheme 白名单、按键白名单、坐标边界、
占位符只在执行瞬间解析且结果不回传；fuzz：随机动作序列不跳出沙盒。"""
import random

import pytest

from webctl.actions import Action, ActionError, ActionExecutor


class StubMouse:
    def __init__(self, rec): self.rec = rec
    def click(self, x, y): self.rec.append(("click", x, y))
    def wheel(self, dx, dy): self.rec.append(("wheel", dx, dy))


class StubKeyboard:
    def __init__(self, rec): self.rec = rec
    def type(self, text): self.rec.append(("type", text))
    def press(self, key): self.rec.append(("press", key))


class StubPage:
    def __init__(self):
        self.rec = []
        self.mouse = StubMouse(self.rec)
        self.keyboard = StubKeyboard(self.rec)
        self.viewport_size = {"width": 1280, "height": 800}
        self.url = "https://example.com/"

    def wait_for_timeout(self, ms): self.rec.append(("wait", ms))
    def goto(self, url): self.url = url


class DictVault:
    def __init__(self, d): self.d = d
    def get(self, name): return self.d.get(name)


def test_navigate_scheme_whitelist():
    ex = ActionExecutor(StubPage())
    for bad in ["javascript:alert(1)", "file:///etc/passwd", "data:text/html,x",
                "ftp://x", ""]:
        with pytest.raises(ActionError):
            ex.execute(Action.navigate(bad))


def test_key_whitelist():
    ex = ActionExecutor(StubPage())
    with pytest.raises(ActionError):
        ex.execute(Action.press("F13"))
    assert ex.execute(Action.press("Enter"))["ok"]


def test_coordinate_bounds():
    ex = ActionExecutor(StubPage())
    with pytest.raises(ActionError):
        ex.execute(Action.click(1001, 10))
    with pytest.raises(ActionError):
        ex.execute(Action.click(-1, 10))
    r = ex.execute(Action.click(500, 500))
    assert r["ok"]
    # 归一化 500/1000 → 像素中心
    assert ("click", 640.0, 400.0) in ex.page.rec


def test_scroll_direction_validated():
    ex = ActionExecutor(StubPage())
    with pytest.raises(ActionError):
        ex.execute(Action.scroll("sideways", 100))
    assert ex.execute(Action.scroll("down", 100))["ok"]


def test_placeholder_requires_vault():
    ex = ActionExecutor(StubPage())
    with pytest.raises(ActionError):
        ex.execute(Action.type(10, 10, "<<vault:pw>>"))


def test_placeholder_resolved_at_injection_and_never_returned():
    page = StubPage()
    ex = ActionExecutor(page, vault=DictVault({"pw": "s3cr3t!"}))
    r = ex.execute(Action.type(10, 10, "<<vault:pw>>"))
    assert ("type", "s3cr3t!") in page.rec       # 真值进了页面
    assert "s3cr3t!" not in str(r)               # 结果摘要不带真值
    assert "s3cr3t!" not in str(Action.type(10, 10, "<<vault:pw>>"))  # 动作本身只有占位符


def test_upload_confined_to_upload_dir(tmp_path):
    up = tmp_path / "up"
    up.mkdir()
    (up / "ok.txt").write_text("x")
    ex = ActionExecutor(StubPage(), upload_dir=up)
    with pytest.raises(ActionError):
        ex.execute(Action.upload(10, 10, "../escape.txt"))
    with pytest.raises(ActionError):
        ex.execute(Action.upload(10, 10, "missing.txt"))
    ex2 = ActionExecutor(StubPage())  # 未配置 upload_dir → 禁用
    with pytest.raises(ActionError):
        ex2.execute(Action.upload(10, 10, "ok.txt"))


def test_no_js_escape_hatch():
    for name in ["evaluate", "run_js", "eval", "exec_js", "add_script"]:
        assert not hasattr(ActionExecutor, name), f"执行器不得暴露 {name}"


def test_fuzz_random_actions_stay_in_sandbox():
    rng = random.Random(42)
    page = StubPage()
    ex = ActionExecutor(page, vault=DictVault({"a": "A"}))
    makers = [
        lambda: Action.click(rng.randint(-50, 1050), rng.randint(-50, 1050)),
        lambda: Action.type(10, 10, rng.choice(["hi", "<<vault:a>>", "<<vault:zz>>"])),
        lambda: Action.scroll(rng.choice(["up", "down", "left", "right", "bad"]), 100),
        lambda: Action.navigate(rng.choice(["https://e.com", "javascript:x", "file:///x"])),
        lambda: Action.press(rng.choice(["Enter", "Tab", "F13", "Alt+F4"])),
        lambda: Action.wait(0),
    ]
    for _ in range(100):
        try:
            ex.execute(rng.choice(makers)())
        except ActionError:
            pass  # 只允许 ActionError 这一种失败形态


def test_upload_sibling_prefix_escape_blocked(tmp_path):
    """P2-1 回归：../up-evil 是兄弟目录，字符串前缀会放行，is_relative_to 不会。"""
    up = tmp_path / "up"
    up.mkdir()
    evil = tmp_path / "up-evil"
    evil.mkdir()
    (evil / "payload.csv").write_text("x")
    ex = ActionExecutor(StubPage(), upload_dir=up)
    with pytest.raises(ActionError):
        ex.execute(Action.upload(10, 10, "../up-evil/payload.csv"))


def test_download_action_exists():
    assert hasattr(Action, "download")
