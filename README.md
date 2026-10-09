# webctl — 半自动网页控制套件

**aiglade 的半自动网页代操作引擎**——aiglade 选型线 F4「代操作」的执行底座：客户在 aiglade 里发起代操作（如替客户在云厂商控制台创建 API Key），背后跑的就是它。本仓库是《半自动网页控制技术规格 v1.1》的参考实现，引擎本身零 aiglade 运行时依赖，可独立使用。
设计目标：AI 主跑、人随时接管、卡点自然落人手。

## 快速开始

```bash
pip install -e .          # playwright 版本 pin 在 pyproject（1.63.0 ↔ chromium 1243）
playwright install chromium
python scripts/check_env.py   # 版本与 pin 不一致会大声报（别静默用别的浏览器跑验收）
python -m pytest tests/ -q    # 全量测试：单元 + 真浏览器端到端
```

## 模块 ↔ 规格对照

| 文件 | 规格 | 要点 |
|---|---|---|
| `webctl/browser.py` | §1 | `launch_persistent_context`，profile 按用户隔离，viewport 1280×800 固定；目录权限走 `perms.py` 收紧 |
| `webctl/perms.py` | R6 | 所有者专属权限：POSIX chmod 0700/0600 + 回读验证；Windows 走 icacls 去继承只授当前用户 + **SID 级回读验证**（/save 导 SDDL，别名先归一化到数字 SID 再比对；不变量 = ACL 上只许 {当前用户, Administrators, SYSTEM}，OWNER RIGHTS 摘掉不放行；/save 文件按 UTF-16LE 无 BOM 解码）；**设不上就抛错，绝不静默降级**（0.1.0 的静默吞错是 P0-3） |
| `webctl/observe.py` | §2 | 截图 + CDP AX 树（role/name），坐标归一化 0–1000，隐藏节点过滤；全程不注入页面脚本 |
| `webctl/actions.py` | §3 | 只暴露 click/type/scroll/navigate/wait/key + 受控 upload（目录闸用 `is_relative_to`）/download（只落 download_dir、文件名清洗留痕）；navigate 只许 http/https；**没有 evaluate 通道**（fuzz 测试守住） |
| `webctl/takeover.py` | §4 | AI_RUNNING / HUMAN_CONTROL / WAITING_HUMAN / HANDOFF 状态机；交回时算观测 diff，不从旧计划硬续 |
| `webctl/yields.py` | §5 | **9 类让位不合并**（v1.1 的 8 类 + 审查轮新增第 9 类授权确认）；实名/OTP/不可逆 = 规格级硬让位；**策略层** `ProductPolicy` 承载产品级强制停靠（代操作产品把登录、授权确认升格，见 `ProductPolicy.daichaozuo()`）；`automatable_stats()` 是刻度尺，分母 = 除实名/OTP 外 7 类 |
| `webctl/audit.py` | §6 | JSONL 按 session 存，320px 缩略图内联；抑图主判据是**凭证通道宣告**（占位符注入当步、harvest 窗口期、playbook 敏感步），内容启发式（长令牌串/key 展示措辞/密码框）只是补充网且按宁抑勿漏收紧；每条带 `actor: ai\|human` |
| `webctl/credentials.py` | §7 | AI 上下文只有 `<<vault:NAME>>` 占位符，真值只在注入瞬间于执行器内部解析、绝不回传；`harvest_key` 一次性 key 读一次直入 vault + 探针验活（**强制直连、禁走系统代理**——探针带着真 key，代理是中间人，且会把失败形态伪装成 key 无效，复验单根因 B），失败**不删值**、标 `unverified`、复核提示带失败形态（HTTP 状态/错误类型）；删除须 `human_confirmed=True`；vault 写盘为**原子写**（临时文件 + fsync + os.replace），唯一副本不被写坏 |
| `webctl/playbook.py` | §8 | 声明式步骤 + 预期观测（URL/AX/标题）；预期不符 → `playbook_mismatch` 低置信让位；`from_dict` **加载期校验**：未知字段/未知动作/harvest 缺 vault_name 直接报错 |
| `webctl/session.py` | 胶水 | 单步回路：状态检查 → 观测 → 页面级让位 → 不可逆/授权目标检查 → 执行 → 审计；`human_stop` 声明步与 `harvest` 步由 runner 真执行；新标签自动收编（`tab_opened`/`tab_closed` 留痕，主标签被关转 WAITING_HUMAN）；被拒动作记 `action_rejected` 留痕 |

## 已裁定的口径（写进了代码，别再翻）

- **让位不合并**：支付测「走到钱的决策点」、不可逆测「撞上破坏性按钮」，统计口径不同。**授权确认是第 9 类**（2026-10-08 审查轮裁定）：F4 三停靠之三原先无家；它不是并进不可逆类——授权是身份/法律行为，与登录同层：规格层条件检测，代操作策略层强制停靠，playbook 用 `human_stop` 标注作主判据。
- **登录分层**：规格层登录墙是条件让位（有保存凭据可过）；代操作产品层是强制停靠（身份行为必须交还本人）。两层都在 `yields.py`，别把产品策略揉进规格分类。
- **敏感期不靠猜**：抑图由凭证通道宣告（harvest 窗口/占位符注入步），验收判据是「敏感窗口内每条审计 `screenshot_thumb is None`」——grep 审计文本找 key 前缀**测不到** base64 图片里的明文，该判据已作废。
- **验活失败语义**：vault 值不删、标 unverified、人确认才删（删除属不可逆类）。
- 低置信「动作后无变化」阈值实现为连续 2 次（规格未定数，此处定版）。

## 测试即验收（规格各节验收口径的落地）

- §1：真浏览器关掉重开仍登录、双用户隔离 — `tests/test_e2e.py::test_profile_persistence_across_restarts`
- §2：AX 树定位按钮、归一化坐标点中；**R5 首击命中率**：24 元素网格实测 ≥95% — `test_first_hit_rate_on_grid`
- §3：100 个随机动作序列只许以 `ActionError` 失败、无 JS 逃逸口；upload 兄弟目录绕过回归；越界被拒留痕 — `tests/test_actions.py`、`test_rejected_action_leaves_audit_trace`
- §4：人控期间 AI 动作被拦；新标签收编/切换 — `test_takeover_blocks_ai_actions`、`test_new_tab_adopted_and_audited`
- §5：9 类逐个命中、优先级、分母口径；支付页端到端不硬闯；授权确认代操作策略下硬停靠 — `tests/test_yields.py`、`test_payment_page_never_hard_charged`、`test_consent_stop_in_daichaozuo_session`
- §6/§7：占位符真值进了页面但不进审计；harvest 全流程（入 vault+验活+抑图判据）；harvest 取不到值即停不许关弹窗；审计敏感网用审查实测的 5 种真实措辞标定 — `test_harvest_playbook_end_to_end`、`test_harvest_failure_stops_before_close`、`tests/test_audit.py::test_sensitive_net_calibrated_on_real_wordings`

## 发版门（2026-10-08 定规矩）

三轮复验的规律：每一轮溜过去的缺陷都只存在于作者跑不到的平台上。
**发版 = Linux 全量绿 + Windows 全量绿（真机），缺一不算发版**；
任何「全绿」表述必须带平台名。标定样本逐字取真机形态，不许按代码的假设编。

## 边界与已知缺口（明说，不装有）

- 接管 UI（右上常驻按钮等）是产品层的事，本套件只提供状态机与拦截点。
- **FileVault 是明文 JSON 落盘**，只靠所有者专属权限保护——规格说的「信封加密」本地实现**没有**，生产换托管凭据库（实现 `CredentialStore` 接口）才有；别以为 local 也加密。
- §6「审计库加密 + 访问留痕」未实现：审计文件按内含准凭据等级靠目录权限保管，加密与访问审计是部署层的事。
- `perms.py` 的 Windows icacls 分支已过一轮真机复验：收紧动作本身正确，0.2.0 坏在回读解析（主体名切碎误报）；0.2.1 改 SID 级验证，解析器由 `tests/test_perms.py` 用真机输出形态标定（正腿放行 + Everyone 负腿必响）。icacls 的实际执行仍建议每次 Windows 部署时跑一遍完整测试确认。
- 「撤销 key 后探针转失败」的撤销闭环（bailian.json 的 `revoke` 块）runner 尚未实现，属下一轮。
- 各家控制台的真实 playbook 首次真跑前须逐个校准页面文案（bailian.json 已标 UNVERIFIED_PAGE_LABELS）：定位不到即低置信让位，不许硬续。
- 测试页须带 `<meta charset>`，否则 Chromium 按默认编码解析、AX 名会乱码（已踩过）。
