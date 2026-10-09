"""§7 凭证面（第 7 件）：占位符进上下文 / vault 托管 / 一次性 key 的最后一公里。

- AI 上下文里只出现占位符 <<vault:NAME>>；真值只在浏览器层注入的瞬间
  存在于执行器内部（见 actions.ActionExecutor._resolve），不进日志、不进审计。
- vault 是接口：本文件给本地实现 FileVault（所有者专属权限 + 原子写、
  按「内含凭据」等级保管）；生产可换托管凭据库实现，接口不变。
- 一次性 key 最后一公里：建 key 成功后页面只显示一次 —— 受控通道读一次、
  直入 vault、探针验活。验活失败语义（2026-10-08 裁定入正式条文）：
  **不删值**（vault 里那份是唯一副本）、打 unverified 标、提示人复核，
  且复核提示必须带失败形态（HTTP 状态/错误类型），让人分清是 key 真无效
  还是探针/网络问题；删除归不可逆类，人确认后才动。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .perms import restrict_to_owner


class CredentialStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def put(self, name: str, value: str, meta: dict | None = None) -> None: ...
    def entry(self, name: str) -> dict | None: ...


class FileVault:
    """本地 vault 实现：profiles 同级目录 vault/，权限收紧到所有者专属
    （POSIX 0600 / Windows ACL，见 perms.py；设不上就抛错，不静默降级）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        parent = self.path.parent
        # P2-11：裸文件名时 parent 是 "."，不对进程当前目录 chmod
        if str(parent) not in (".", ""):
            parent.mkdir(parents=True, exist_ok=True)
            restrict_to_owner(parent, is_dir=True)
        if self.path.exists():
            # P1-2：已存在的文件也要复查权限，不只收紧父目录
            restrict_to_owner(self.path, is_dir=False)
        else:
            self._write({})

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        """原子写：同目录临时文件写满 + fsync 后 os.replace 换名。
        进程崩在半路也只会留下临时文件，「唯一副本」不被写坏（P2）。
        P1-1：临时文件用 os.open(O_CREAT|O_EXCL, 0o600) 建，
        杜绝 umask 022 下 0644 全局可读窗口（里面是整个 vault 明文）。"""
        payload = json.dumps(data, ensure_ascii=False, indent=1)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # 上次崩溃可能留下 tmp：先删，避免 O_EXCL 误杀
        try:
            os.unlink(tmp)
        except OSError:
            pass
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        restrict_to_owner(tmp, is_dir=False)
        os.replace(tmp, self.path)

    def get(self, name: str) -> str | None:
        e = self._read().get(name)
        return e["value"] if e else None

    def entry(self, name: str) -> dict | None:
        e = self._read().get(name)
        if e is None:
            return None
        return {k: v for k, v in e.items() if k != "value"}  # 元数据可示，值不可示

    def put(self, name: str, value: str, meta: dict | None = None) -> None:
        data = self._read()
        data[name] = {"value": value, "meta": meta or {},
                      "status": "stored", "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        self._write(data)

    def set_status(self, name: str, status: str, detail: dict | None = None) -> None:
        data = self._read()
        if name not in data:
            raise KeyError(name)
        data[name]["status"] = status
        if detail:
            data[name]["status_detail"] = detail
        self._write(data)

    def delete(self, name: str, *, human_confirmed: bool = False) -> None:
        """删除是不可逆类动作：必须显式的人确认标记，否则拒绝。"""
        if not human_confirmed:
            raise PermissionError("删除 vault 值属不可逆类，须人确认（human_confirmed=True）")
        data = self._read()
        data.pop(name, None)
        self._write(data)


@dataclass
class ProbeResult:
    ok: bool
    http_status: int | None = None
    error_type: str | None = None   # 如 timeout / conn_error / auth_rejected
    detail: str = ""


@dataclass
class HarvestOutcome:
    name: str
    status: str                     # verified | unverified
    review_message: str | None = None  # unverified 时给人的复核提示（带失败形态）


def harvest_key(name: str, extract_once: Callable[[], str],
                vault: FileVault, probe: Callable[[str], ProbeResult]) -> HarvestOutcome:
    """一次性 key 回收：读一次 → 直入 vault → 探针验活。

    extract_once 只调用一次（页面只显示一次）；失败处置按裁定：
    值不删、打 unverified、给带失败形态的复核提示。
    """
    value = extract_once()
    if not value:
        raise ValueError("extract_once 未取到 key 值")
    vault.put(name, value, meta={"source": "harvest", "harvested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    try:
        r = probe(value)
    except Exception as e:  # 探针自身异常也按验活失败处理，不删值
        r = ProbeResult(ok=False, error_type=type(e).__name__, detail=str(e))
    if r.ok:
        vault.set_status(name, "verified")
        return HarvestOutcome(name=name, status="verified")
    detail = {"http_status": r.http_status, "error_type": r.error_type, "detail": r.detail}
    vault.set_status(name, "unverified", detail)
    msg = (f"key「{name}」已存入 vault 但验活未过：HTTP={r.http_status} "
           f"错误类型={r.error_type}（{r.detail}）。值未删除——请人工复核是 key 无效"
           f"还是探针/网络问题；删除属不可逆操作，需你确认后才动。")
    return HarvestOutcome(name=name, status="unverified", review_message=msg)


def placeholder(name: str) -> str:
    """AI 侧构造动作用的占位符形态。"""
    return f"<<vault:{name}>>"
