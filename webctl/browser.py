"""§1 真浏览器 + 持久 profile。

- Playwright 驱动 Chromium，launch_persistent_context，登录态跨次保留。
- profile 按用户隔离：profiles/<user_id>/，目录权限 0700（R6：profile 即凭据库，
  活会话 cookie 就是账号通行证，按 vault 同级保管——磁盘加密与访问审计由部署层补，
  本层负责最小权限与不外泄到日志）。
- 浏览器与 viewport 固定：viewport 1280x800（规格 §2）；playwright 版本在
  pyproject 里 pin 死，避免指纹漂移。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from .perms import restrict_to_owner

VIEWPORT = {"width": 1280, "height": 800}

# P1-5：user_id 白名单字符集。Windows 下 "C:\\evil" 经 pathlib join 会直接
# 丢弃 base 变成绝对路径，UNC 路径同理——只拦 "/" 和 ".." 不够。
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Windows 保留设备名：CON/PRN/AUX/NUL/COM1-9/LPT1-9 在文件系统层是设备不是文件，
# 拿它当目录名行为异常（不是越权，是健壮性坑）。
_WIN_DEV_RE = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)


def safe_name(name: str, what: str = "name") -> str:
    """路径片段白名单 + Windows 保留名。**抛错而非改名**：调用方给错名字
    必须立刻知道，静默改名会让审计/凭据落到意料之外的地方。

    规则：非空、字符集 [A-Za-z0-9_.-]、不是 . / ..、不是保留设备名、
    不以 . 或空格结尾（NTFS 会吃掉尾点与尾空格，与原名别名冲突）。"""
    s = str(name)
    if not s or not _USER_ID_RE.match(s) or s in (".", ".."):
        raise ValueError(f"非法 {what}: {name!r}")
    if _WIN_DEV_RE.match(s):
        raise ValueError(f"{what} 是 Windows 保留设备名: {name!r}")
    if s != s.rstrip(". "):
        raise ValueError(f"{what} 不能以 . 或空格结尾: {name!r}")
    return s


# 最近一次主框架导航响应的状态码。挂一份可变 dict 在 page 上；挂不上去
# （宿主对象不允许设属性）就静默跳过，http_status 保持 None，不影响其它路径。
# 为什么要它：在那之前 Observation.http_status 恒为 None，§5 的 429 风控
# 判据在真实回路里永不触发——测试却用手工构造的 Signals(429) 断言它能触发。
_STATUS_FLAG = "_webctl_nav_status"


def track_nav_status(page: Page) -> None:
    """登记导航响应监听；同一页面重复调用只登记一次。"""
    if getattr(page, _STATUS_FLAG, None) is not None:
        return
    box: dict = {"status": None}
    try:
        setattr(page, _STATUS_FLAG, box)
    except Exception:
        return

    def on_response(resp):
        try:
            if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                box["status"] = resp.status
        except Exception:
            pass

    try:
        page.on("response", on_response)
    except Exception:
        pass


def last_nav_status(page: Page) -> int | None:
    box = getattr(page, _STATUS_FLAG, None)
    return box.get("status") if isinstance(box, dict) else None


@dataclass
class BrowserManager:
    base_dir: Path
    headless: bool = True
    _pw: Playwright | None = field(default=None, repr=False)
    _contexts: dict[str, BrowserContext] = field(default_factory=dict, repr=False)

    @property
    def profiles_dir(self) -> Path:
        return self.base_dir / "profiles"

    def profile_path(self, user_id: str) -> Path:
        # 白名单 + ".." + Windows 保留名，统一走 safe_name
        return self.profiles_dir / safe_name(user_id, "user_id")

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        if self._pw is None:
            self.profiles_dir.mkdir(parents=True, exist_ok=True)
            restrict_to_owner(self.profiles_dir, is_dir=True)
            self._pw = sync_playwright().start()

    def context(self, user_id: str) -> BrowserContext:
        """同一 user_id 复用同一持久 context；不同用户绝不混用 profile。"""
        self.start()
        if user_id not in self._contexts:
            pdir = self.profile_path(user_id)
            pdir.mkdir(parents=True, exist_ok=True)
            restrict_to_owner(pdir, is_dir=True)
            assert self._pw is not None
            self._contexts[user_id] = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(pdir),
                headless=self.headless,
                viewport=VIEWPORT,
                accept_downloads=True,
            )
        return self._contexts[user_id]

    def page(self, user_id: str) -> Page:
        ctx = self.context(user_id)
        p = ctx.pages[0] if ctx.pages else ctx.new_page()
        track_nav_status(p)
        return p

    def close(self) -> None:
        for ctx in self._contexts.values():
            try:
                ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._pw is not None:
            self._pw.stop()
            self._pw = None
