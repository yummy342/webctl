"""§6 审计回放：每步留痕，让位点打标，可完整回放。

条目：{ts, session, actor: ai|human, kind, action, url, obs_diff,
      screenshot_thumb（320px 宽、base64）, yield}
actor 字段（R7）：登录/实名/授权等法律意义动作要能区分是账号持有人
本人做的还是 AI 做的——这是代办合规的证据链，事后补不了。

截图纪律（R3）：审计库按「内含准凭据」等级保管。敏感时刻截图必须抑制
（screenshot_thumb=null 且记 suppressed_reason）：
  - 实名让位期（R2：证件画面不进审计库）
  - 页面含密码框
  - 一次性 key 显示页（启发式：页面出现 key 已生成类文案 + 长令牌串）
  - 支付页
抑制只针对截图，动作与 diff 照常记——合规靠归属与动作链，不靠偷拍画面。
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .browser import safe_name
from .observe import Observation
from .perms import restrict_to_owner

_KEY_DISPLAY_RE = re.compile(r"(api[- ]?key|密钥).{0,12}(已生成|创建成功|如下|generated|created)", re.I)
_LONG_TOKEN_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9_-]{32,})\b")


def sensitive_reason(obs: Observation, *, yield_suppress: bool = False,
                     has_password_field: bool = False) -> str | None:
    """内容启发式只是**补充网**（P0-1 修法）：主判据是凭证通道的显式宣告
    （record 的 suppress 参数）。网本身按「宁抑勿漏」收紧：页面任何位置
    出现长令牌串、或出现 key 展示类措辞，都抑——误抑只损失一张截图，
    漏抑损失的是客户凭据。"""
    if yield_suppress:
        return "yield_suppress（实名等硬让位期）"
    if has_password_field:
        return "password_field（页面含密码框）"
    text = obs.title + "\n" + "\n".join(obs.ax_texts())
    if _LONG_TOKEN_RE.search(text):
        return "token_visible（页面出现长令牌串）"
    if _KEY_DISPLAY_RE.search(text):
        return "key_display（key 展示类措辞）"
    return None


def _thumb(png: bytes, width: int = 320) -> str:
    img = Image.open(io.BytesIO(png))
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@dataclass
class AuditLog:
    path: Path
    session_id: str
    _seq: int = field(default=0, repr=False)

    @classmethod
    def open(cls, base_dir: Path, session_id: str) -> "AuditLog":
        # P1-3：审计库按「内含准凭据」等级保管——目录与文件都要收紧到所有者专属，
        # 不只 mkdir（默认 0755 全局可读）。
        d = Path(base_dir) / "audit"
        d.mkdir(parents=True, exist_ok=True)
        restrict_to_owner(d, is_dir=True)
        # session_id 也进路径，白名单必须与 browser.safe_name 同一档：
        # Windows 下 "C:\evil" 经 pathlib join 会丢弃 base 变成绝对路径，
        # 只拦 "/" 和 ".." 不够。挡在 open() 入口——审计写错地方比写不了更糟。
        sid = safe_name(session_id, "session_id")
        p = d / f"{sid}.jsonl"
        if not p.exists():
            p.touch()
        restrict_to_owner(p, is_dir=False)
        return cls(path=p, session_id=sid)

    def record(self, *, actor: str, kind: str, action: dict | None = None,
               obs: Observation | None = None, obs_diff: str = "",
               yield_info: dict | None = None, has_password_field: bool = False,
               suppress: str | None = None) -> dict:
        if actor not in ("ai", "human"):
            raise ValueError(f"actor 必须是 ai|human: {actor}")
        self._seq += 1
        thumb = None
        suppressed = None
        if obs is not None:
            # 通道宣告（suppress）优先于一切内容启发式
            suppressed = suppress or sensitive_reason(
                obs,
                yield_suppress=bool(yield_info and yield_info.get("suppress_screenshot")),
                has_password_field=has_password_field,
            )
            if suppressed is None:
                thumb = _thumb(obs.screenshot_png)
        entry = {
            "seq": self._seq,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "session": self.session_id,
            "actor": actor,
            "kind": kind,
            "action": action,
            "url": obs.url if obs else None,
            "obs_diff": obs_diff,
            "screenshot_thumb": thumb,
            "screenshot_suppressed": suppressed,
            "yield": yield_info,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    @staticmethod
    def replay(path: Path) -> list[dict]:
        """完整回放：动作与截图一一对应（被抑制的条目带原因）。"""
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
