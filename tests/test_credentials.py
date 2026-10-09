"""§7 凭证面：占位符形态、vault 存取、删除须人确认、
一次性 key 回收的验活语义（失败不删值、打 unverified、复核提示带失败形态）。"""
import pytest

from webctl.credentials import FileVault, ProbeResult, harvest_key, placeholder


def test_placeholder_format():
    assert placeholder("aliyun_pw") == "<<vault:aliyun_pw>>"


def test_vault_roundtrip_and_entry_hides_value(tmp_path):
    v = FileVault(tmp_path / "vault.json")
    v.put("k1", "secret-value", meta={"site": "example"})
    assert v.get("k1") == "secret-value"
    e = v.entry("k1")
    assert e is not None and "value" not in e  # 元数据可见、真值不经 entry 出
    assert v.get("missing") is None


def test_delete_requires_human_confirmation(tmp_path):
    v = FileVault(tmp_path / "vault.json")
    v.put("k1", "v")
    with pytest.raises(PermissionError):
        v.delete("k1")
    assert v.get("k1") == "v"  # 未确认不删
    v.delete("k1", human_confirmed=True)
    assert v.get("k1") is None


def test_harvest_verified(tmp_path):
    v = FileVault(tmp_path / "vault.json")
    calls = {"extract": 0}

    def extract():
        calls["extract"] += 1
        return "sk-newkey123"

    out = harvest_key("site_key", extract, v, lambda val: ProbeResult(ok=True, http_status=200))
    assert out.status == "verified"
    assert v.get("site_key") == "sk-newkey123"
    assert v.entry("site_key")["status"] == "verified"
    assert calls["extract"] == 1  # 只读一次


def test_harvest_unverified_keeps_value_and_marks(tmp_path):
    """裁定语义：验活失败 → 值不删、标 unverified、复核提示带失败形态。"""
    v = FileVault(tmp_path / "vault.json")
    out = harvest_key("site_key", lambda: "sk-maybevalid", v,
                      lambda val: ProbeResult(ok=False, http_status=401,
                                              error_type="auth_rejected", detail="invalid api key"))
    assert out.status == "unverified"
    assert v.get("site_key") == "sk-maybevalid"  # 唯一副本还在
    e = v.entry("site_key")
    assert e["status"] == "unverified"
    assert e["status_detail"]["http_status"] == 401
    assert "401" in out.review_message and "auth_rejected" in out.review_message


def test_harvest_probe_exception_also_keeps_value(tmp_path):
    v = FileVault(tmp_path / "vault.json")

    def boom(_):
        raise TimeoutError("probe timed out")

    out = harvest_key("site_key", lambda: "sk-k", v, boom)
    assert out.status == "unverified"
    assert v.get("site_key") == "sk-k"
    assert "TimeoutError" in out.review_message


def test_harvest_empty_value_rejected(tmp_path):
    v = FileVault(tmp_path / "vault.json")
    with pytest.raises(ValueError):
        harvest_key("site_key", lambda: "", v, lambda val: ProbeResult(ok=True))


def test_vault_write_is_atomic_under_crash(tmp_path, monkeypatch):
    """P2：写一半崩了，旧值必须完好（唯一副本不许被写坏）。"""
    import os as _os
    v = FileVault(tmp_path / "vault.json")
    v.put("k1", "original")

    def boom(*a, **k):
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(_os, "replace", boom)
    with pytest.raises(OSError):
        v.put("k1", "corrupted-partial")
    monkeypatch.undo()
    assert v.get("k1") == "original"


import sys as _sys
import pytest as _pytest


@_pytest.mark.skipif(_sys.platform.startswith("win"),
                     reason="POSIX 模式位断言只在 POSIX 成立；Windows 侧由 perms 的 "
                            "SID 级回读校验 + test_perms.py 的标定集守（复验单根因 C）")
def test_vault_and_profile_perms_owner_only(tmp_path):
    """P0-3（POSIX 侧）：vault 文件 0600、目录 0700，回读 stat 验。"""
    import stat
    v = FileVault(tmp_path / "vault" / "vault.json")
    v.put("k", "v")
    fmode = stat.S_IMODE((tmp_path / "vault" / "vault.json").stat().st_mode)
    dmode = stat.S_IMODE((tmp_path / "vault").stat().st_mode)
    assert fmode == 0o600, oct(fmode)
    assert dmode == 0o700, oct(dmode)
