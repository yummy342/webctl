"""perms 标定集（样本纪律：逐字取真机形态，不许按代码的假设编样本）。

0.2.1 的正腿之死：夹具里写的是 (A;;FA;;;S-1-3-4)，真机吐的是
(A;OICIID;FA;;;OW) —— 夹具编码了代码的同一个假设，标定自然无牙。
本文件的 REAL_MACHINE_SDDL 是复验单里 Windows 真机的逐字形态
（用户 SID 用同形态的完整值），此后 perms 的任何改动都先过它。
"""
import pytest

from webctl.perms import (
    assert_only_allowed_sids, decode_saved_acl, normalize_sid,
    parse_saved_acl_sids, parse_whoami_sid,
)

USER_SID = "S-1-5-21-1000000000-2000000000-3000000000-1001"  # 合成值：形态取真机，标识不带真机

WHOAMI_SAMPLE = f"""User Name          SID
================== ============================================
desktop-example\\demo-user {USER_SID}
"""

# 真机 /save 的两行：第一行是显式授予（含当前用户），第二行带继承形态的
# 系统项与 OWNER RIGHTS（渲染成别名 OW，不是数字 S-1-3-4）。
# 出处注（0.2.2 签核小注 a）：两行是**两个不同路径**的真机抓取合成——
# 第一行取自用户目录下、第二行取自 %TEMP% 下；真机上两种形态互斥，
# 不会同时出现在同一个路径的同一份文件里。逐行皆真，合成忠实，
# 但别把这份夹具当成某个真实存在的文件。
REAL_MACHINE_SDDL = (
    f"D:PAI(A;;FA;;;{USER_SID})(A;;FA;;;BA)(A;;FA;;;SY)\n"
    "D:(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;FA;;;OW)\n"
)
# 摘掉 OW 之后应有的形态
STRIPPED_SDDL = (
    f"D:PAI(A;;FA;;;{USER_SID})(A;;FA;;;BA)(A;;FA;;;SY)\n"
    "D:(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)\n"
)
BAD_SDDL_EVERYONE = f"D:PAI(A;;FA;;;SY)(A;;FA;;;WD)(A;;FA;;;{USER_SID})\n"
BAD_SDDL_USERS = f"D:PAI(A;;FA;;;SY)(A;;FA;;;S-1-5-32-545)(A;;FA;;;{USER_SID})\n"


def test_parse_whoami_sid():
    assert parse_whoami_sid(WHOAMI_SAMPLE) == USER_SID


def test_alias_normalization():
    assert normalize_sid("OW") == "S-1-3-4"
    assert normalize_sid("SY") == "S-1-5-18"
    assert normalize_sid("BA") == "S-1-5-32-544"
    assert normalize_sid(USER_SID) == USER_SID


def test_decode_utf16le_without_bom():
    """复验单根因 1：icacls /save 是 UTF-16LE 无 BOM，
    utf-16 codec 会抛在 errors= 作用范围之外——必须按字节解。"""
    raw = REAL_MACHINE_SDDL.encode("utf-16-le")
    assert not raw.startswith(b"\xff\xfe")
    text = decode_saved_acl(raw)
    assert parse_saved_acl_sids(text) == parse_saved_acl_sids(REAL_MACHINE_SDDL)
    # 带 BOM 的变体也要能解
    raw_bom = b"\xff\xfe" + REAL_MACHINE_SDDL.encode("utf-16-le")
    assert parse_saved_acl_sids(decode_saved_acl(raw_bom)) == \
        parse_saved_acl_sids(REAL_MACHINE_SDDL)


def test_real_machine_shape_parses_with_ow_normalized():
    found = parse_saved_acl_sids(REAL_MACHINE_SDDL)
    assert found == {USER_SID, "S-1-5-32-544", "S-1-5-18", "S-1-3-4"}


def test_real_machine_shape_fails_invariant_until_ow_stripped():
    """不变量 {用户, Administrators, SYSTEM}：真机原样（含 OW）必须被判不合格，
    摘掉 OW 后必须放行——正负两腿用的都是真机形态。"""
    with pytest.raises(PermissionError):
        assert_only_allowed_sids(parse_saved_acl_sids(REAL_MACHINE_SDDL),
                                 USER_SID, path="vault")
    assert_only_allowed_sids(parse_saved_acl_sids(STRIPPED_SDDL),
                             USER_SID, path="vault")  # 不抛错即过


def test_everyone_in_acl_raises():
    with pytest.raises(PermissionError):
        assert_only_allowed_sids(parse_saved_acl_sids(BAD_SDDL_EVERYONE),
                                 USER_SID, path="vault.json")


def test_builtin_users_in_acl_raises():
    with pytest.raises(PermissionError):
        assert_only_allowed_sids(parse_saved_acl_sids(BAD_SDDL_USERS),
                                 USER_SID, path="vault.json")


def test_missing_user_in_acl_raises():
    with pytest.raises(PermissionError):
        assert_only_allowed_sids({"S-1-5-18", "S-1-5-32-544"}, USER_SID,
                                 path="vault.json")
