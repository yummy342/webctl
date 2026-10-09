"""§3 动作空间收敛：只暴露下列动作，不给 AI 任意 JS 通道。

  click(x, y) / type(x, y, text) / scroll(dir, dist) / navigate(url) /
  wait(seconds|until) / key(name)
  受控原语（R8）：upload(x, y, path) / download —— 只许指定目录、文件名留痕。

任何动作都不经 page.evaluate；本模块刻意不提供 evaluate/run_js 之类的方法。
坐标一律为 0-1000 归一化，执行时按 viewport 换算像素。
type 的 text 可含 <<vault:NAME>> 占位符：由凭证层在注入瞬间解析为真值，
AI 侧与审计侧只见占位符（§7）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from .observe import denormalize

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_KEYS = {
    "Enter", "Tab", "Escape", "Backspace", "Delete", "ArrowUp", "ArrowDown",
    "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", " ",
}
_PLACEHOLDER_RE = re.compile(r"<<vault:([A-Za-z0-9_.-]+)>>")


def contains_placeholder(text: str | None) -> bool:
    """文本是否含凭证占位符——凭证通道据此宣告敏感期（审计抑图），
    不靠页面内容启发式猜（P0-1 修法）。"""
    return bool(text) and bool(_PLACEHOLDER_RE.search(text))


def _safe_filename(name: str) -> str:
    safe = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name or "download").strip() or "download"
    return safe.lstrip(".") or "download"


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    """一条待执行动作。repr 即审计可见形态：text 保持占位符原样。"""
    kind: str
    x: int | None = None
    y: int | None = None
    text: str | None = None
    direction: str | None = None
    distance: int | None = None
    url: str | None = None
    seconds: float | None = None
    key: str | None = None
    path: str | None = None

    # —— 构造器（AI 侧只用这些）——
    @classmethod
    def click(cls, x: int, y: int) -> "Action":
        return cls("click", x=x, y=y)

    @classmethod
    def type(cls, x: int, y: int, text: str) -> "Action":
        return cls("type", x=x, y=y, text=text)

    @classmethod
    def scroll(cls, direction: str, distance: int) -> "Action":
        return cls("scroll", direction=direction, distance=distance)

    @classmethod
    def navigate(cls, url: str) -> "Action":
        return cls("navigate", url=url)

    @classmethod
    def wait(cls, seconds: float) -> "Action":
        return cls("wait", seconds=seconds)

    @classmethod
    def press(cls, key: str) -> "Action":
        return cls("key", key=key)

    @classmethod
    def upload(cls, x: int, y: int, path: str) -> "Action":
        return cls("upload", x=x, y=y, path=path)

    @classmethod
    def download(cls, x: int, y: int) -> "Action":
        """点目标元素触发下载，文件只许落 download_dir（文件名清洗后留痕）。"""
        return cls("download", x=x, y=y)


class ActionExecutor:
    def __init__(self, page: Page, *, vault=None, upload_dir: Path | None = None,
                 download_dir: Path | None = None):
        self.page = page
        self.vault = vault  # §7 CredentialStore；None 时占位符原样拒绝执行（见 _resolve）
        self.upload_dir = Path(upload_dir) if upload_dir else None
        self.download_dir = Path(download_dir) if download_dir else None

    # —— 内部 ——
    def _px(self, nx: int | None, ny: int | None) -> tuple[float, float]:
        if nx is None or ny is None:
            raise ActionError("动作缺坐标")
        if not (0 <= nx <= 1000 and 0 <= ny <= 1000):
            raise ActionError(f"坐标越界（须 0-1000）: ({nx}, {ny})")
        vs = self.page.viewport_size or {"width": 1280, "height": 800}
        return denormalize(nx, vs["width"]), denormalize(ny, vs["height"])

    def _resolve(self, text: str) -> str:
        """占位符 → 真值。只在本方法内部短暂存在，绝不回传给调用方/审计。"""
        def rep(m: re.Match) -> str:
            if self.vault is None:
                raise ActionError("文本含凭证占位符但未配置 vault，拒绝执行")
            val = self.vault.get(m.group(1))
            if val is None:
                raise ActionError(f"vault 无此凭证: {m.group(1)}")
            return val
        return _PLACEHOLDER_RE.sub(rep, text)

    # —— 执行 ——
    def execute(self, action: Action) -> dict:
        """执行一条动作，返回结果摘要（不含任何凭证真值）。"""
        p = self.page
        k = action.kind
        if k == "click":
            x, y = self._px(action.x, action.y)
            p.mouse.click(x, y)
        elif k == "type":
            x, y = self._px(action.x, action.y)
            p.mouse.click(x, y)  # 先点后打，防焦点丢失（二合一，规格 §3）
            p.keyboard.type(self._resolve(action.text or ""))
        elif k == "scroll":
            if action.direction not in ("up", "down", "left", "right"):
                raise ActionError(f"非法滚动方向: {action.direction}")
            dist = (action.distance or 0) / 1000 * 800
            dx, dy = {
                "up": (0, -dist), "down": (0, dist),
                "left": (-dist, 0), "right": (dist, 0),
            }[action.direction]
            p.mouse.wheel(dx, dy)
        elif k == "navigate":
            scheme = urlparse(action.url or "").scheme.lower()
            if scheme not in ALLOWED_SCHEMES:
                raise ActionError(f"navigate 只允许 http/https，拒绝: {scheme or '(空)'}")
            p.goto(action.url)
        elif k == "wait":
            # P2-3：wait 上限 60 秒，AI 下 wait(999999) 不许把回路挂起
            # 下界也要钳：wait(-5) 会算出负毫秒，wait_for_timeout 行为未定义
            secs = max(0.0, min(action.seconds or 0, 60))
            p.wait_for_timeout(int(secs * 1000))
        elif k == "key":
            if action.key not in ALLOWED_KEYS:
                raise ActionError(f"按键不在允许集: {action.key}")
            p.keyboard.press(action.key)
        elif k == "upload":
            if self.upload_dir is None:
                raise ActionError("未配置 upload_dir，upload 原语禁用")
            base = self.upload_dir.resolve()
            fpath = (base / (action.path or "")).resolve()
            # is_relative_to 而非字符串 startswith：/data/up-evil 会骗过前缀判定（P2 实测）
            if not fpath.is_relative_to(base) or not fpath.is_file():
                raise ActionError(f"upload 只许 upload_dir 内已存在文件: {action.path}")
            x, y = self._px(action.x, action.y)
            # 点击没触发文件选择框时 Playwright 抛的是 TimeoutError，不是 ActionError；
            # 上层 session.act 只 catch ActionError → 既不记 action_rejected 留痕，
            # run_playbook 还会整段崩掉而不是"即停"。这里统一归一到 ActionError。
            try:
                with p.expect_file_chooser(timeout=15000) as fc:
                    p.mouse.click(x, y)
            except PWTimeout as e:
                raise ActionError("点击未触发文件选择框（15s 超时）") from e
            fc.value.set_files(str(fpath))
        elif k == "download":
            if self.download_dir is None:
                raise ActionError("未配置 download_dir，download 原语禁用")
            self.download_dir.mkdir(parents=True, exist_ok=True)
            x, y = self._px(action.x, action.y)
            # 同上：超时抛 TimeoutError 会被上层漏接，归一成 ActionError
            try:
                with p.expect_download(timeout=15000) as dl_info:
                    p.mouse.click(x, y)
            except PWTimeout as e:
                raise ActionError("点击未触发下载（15s 超时）") from e
            dl = dl_info.value
            safe = _safe_filename(dl.suggested_filename)
            dest = self.download_dir / safe
            dl.save_as(str(dest))
            return {"ok": True, "kind": k, "url": p.url, "downloaded": safe}
        else:
            raise ActionError(f"未知动作: {k}")
        return {"ok": True, "kind": k, "url": p.url}
