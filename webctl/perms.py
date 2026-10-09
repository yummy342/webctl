"""所有者专属权限（R6 落点）：profile 目录与 vault 文件按「内含凭据」保管。

纪律：设完必须回读验证；设不上、验不过 → 抛 PermissionError，绝不静默降级
（「宣称了却没生效」比「没宣称」更坏）。

平台实现：
  - POSIX：chmod（目录 0700 / 文件 0600），回读 stat 验证。
  - Windows：icacls 去继承、只授当前用户，回读走 `icacls /save` 的 SDDL。

Windows 侧三轮复验的教训（都写进了下面的实现与 tests/test_perms.py 标定集）：
  1. 显示名不可解析：主体名带空格（NT AUTHORITY\\SYSTEM），按空格切必碎；
  2. SDDL 有自己的别名词汇表（SY/BA/OW/WD/BU…），与数字 SID 是同一主体的
     两种写法——比对前必须先归一化到数字 SID；
  3. icacls /save 写的是 UTF-16LE **无 BOM**，Python 的 "utf-16" codec
     强制要 BOM 会直接抛错，且抛在 errors= 的作用范围之外；
  4. OWNER RIGHTS（OW / S-1-3-4）不放行、要摘掉：它授的是「当前所有者」，
     所有权一变新所有者自动拿完全控制，与「只授当前用户」的纪律相冲。
最终不变量一句话：**这份 ACL 上只许有 {当前用户, Administrators, SYSTEM}。**
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# SDDL 别名 → 数字 SID（归一化词汇表；两种写法是同一主体）
_SDDL_ALIASES = {
    "SY": "S-1-5-18",        # Local System
    "BA": "S-1-5-32-544",    # BUILTIN\Administrators
    "BU": "S-1-5-32-545",    # BUILTIN\Users
    "WD": "S-1-1-0",         # Everyone
    "AU": "S-1-5-11",        # Authenticated Users
    "OW": "S-1-3-4",         # OWNER RIGHTS
}
_ALLOWED_SIDS = {"S-1-5-18", "S-1-5-32-544"}  # SYSTEM + Administrators，另加当前用户
# 注意：允许集是**上界不是清单**——真机收紧后实际只剩当前用户（继承摘除后
# SYSTEM/Administrators 并未被重新授予），比不变量更紧是正确结果，
# 不许后来者当缺陷把它们「补」回去（0.2.2 复验签核①）。
_OWNER_RIGHTS_SID = "S-1-3-4"


def normalize_sid(token: str) -> str:
    return _SDDL_ALIASES.get(token, token)


def restrict_to_owner(path: Path, *, is_dir: bool) -> None:
    path = Path(path)
    if sys.platform.startswith("win"):
        _restrict_windows(path)
    else:
        _restrict_posix(path, is_dir=is_dir)


def _restrict_posix(path: Path, *, is_dir: bool) -> None:
    want = 0o700 if is_dir else 0o600
    os.chmod(path, want)
    got = path.stat().st_mode & 0o777
    if got != want:
        raise PermissionError(f"权限回读不符：{path} 期望 {oct(want)} 实得 {oct(got)}")


# —— Windows：纯函数层（解码/解析/裁决可用真机样本在任何平台标定）——

_SID_RE = re.compile(r"S-1-\d+(?:-\d+)+")


def parse_whoami_sid(text: str) -> str:
    """从 `whoami /user` 输出里取当前用户 SID（唯一的 S-1-… 串）。"""
    m = _SID_RE.search(text)
    if not m:
        raise PermissionError("whoami /user 输出里找不到 SID")
    return m.group(0)


def decode_saved_acl(raw: bytes) -> str:
    """icacls /save 的文件是 UTF-16LE 无 BOM（有 BOM 时按 utf-16 解）。"""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-16-le", errors="replace")


def parse_saved_acl_sids(text: str) -> set[str]:
    """从 SDDL 文本里取全部 ACE 受托方，归一化为数字 SID 集合。
    ACE 形如 (A;;FA;;;SY) / (A;OICIID;FA;;;OW)，受托方在最后的 ;;; 之后。"""
    raw = re.findall(r";;;(S-1-\d+(?:-\d+)+|[A-Z]{2})\)", text)
    return {normalize_sid(t) for t in raw}


def assert_only_allowed_sids(found: set[str], user_sid: str, path) -> None:
    """不变量：ACL 上只许 {当前用户, SYSTEM, Administrators}。
    OW 已归一化为 S-1-3-4，不在允许集内——残留即抛错（摘除在调用方做）。"""
    allowed = _ALLOWED_SIDS | {user_sid}
    extra = found - allowed
    if extra:
        raise PermissionError(
            f"ACL 回读含非预期主体 {sorted(extra)}：{path}（完整集合 {sorted(found)}）")
    if user_sid not in found:
        raise PermissionError(f"ACL 回读里找不到当前用户 {user_sid}：{path}")


# —— Windows：命令层 ——

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    # errors="replace"：icacls 的本地化输出在个别 locale 下不可表示，
    # 不许让解码错误成为又一个「只在别人的机器上」的故障（复验单小注 b）
    return subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", timeout=30)


def _current_user_sid() -> str:
    r = _run(["whoami", "/user"])
    if r.returncode != 0:
        raise PermissionError(f"whoami /user 失败：{r.stderr.strip()}")
    return parse_whoami_sid(r.stdout)


def _current_user_name() -> str:
    return os.environ.get("USERNAME") or os.getlogin()


def _read_acl_sids(path: Path) -> set[str]:
    fd, tmpname = tempfile.mkstemp(prefix="webctl-acl-")
    os.close(fd)
    try:
        r = _run(["icacls", str(path), "/save", tmpname])
        if r.returncode != 0:
            raise PermissionError(f"icacls /save 回读失败：{path}")
        found = parse_saved_acl_sids(decode_saved_acl(Path(tmpname).read_bytes()))
        if not found:
            raise PermissionError(f"icacls /save 未解析出任何 SID：{path}")
        return found
    finally:
        try:
            os.unlink(tmpname)
        except OSError:
            pass


def _restrict_windows(path: Path) -> None:
    user = _current_user_name()
    r = _run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"])
    if r.returncode != 0:
        raise PermissionError(
            f"icacls 收紧失败：{path}: {r.stderr.strip() or r.stdout.strip()}")
    found = _read_acl_sids(path)
    if _OWNER_RIGHTS_SID in found:
        # OW 摘掉而非放行：两种主体写法各试一次，成败由回读裁决，不看返回码
        _run(["icacls", str(path), "/remove:g", "OWNER RIGHTS"])
        _run(["icacls", str(path), "/remove:g", _OWNER_RIGHTS_SID])
        found = _read_acl_sids(path)
    assert_only_allowed_sids(found, _current_user_sid(), path)
