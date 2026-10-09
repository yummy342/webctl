# F4 代操作 playbook · 首家：阿里云百炼（草案 v0.1，2026-10-08）

状态：**结构与口径已按定版写齐；页面元素文案未经登录态实测**，首次真跑前须逐个校准（见末节）。机器可读版同目录 `bailian.json`，格式对齐 `webctl/playbook.py` 的 `Playbook.from_dict`（已实测可加载）。

依据：产品规格 F4 的 playbook 结构（入口 / 停靠点 / 建 key 步骤 / 最小权限 / 占位符回填点 / vault 字段 / 探针 / 失败与风控 / 撤销路径）；引擎口径为技术规格 v1.1（§5 八类让位、§7 凭证面、§8 playbook 层）与 webctl 参考实现。

## 1. 入口

- 控制台：`https://bailian.console.aliyun.com/`
- API-Key 管理页（公开教程给出的深链形态）：`https://bailian.console.aliyun.com/?tab=model#/api-key`
- 地域先行：API Key 严格绑单地域 + 单业务空间，创建前先确认地域（默认华北 2 北京），跨地域不可迁移、不可共享。

## 2. 停靠点（三停靠，按 2026-10-08 分层裁定）

| 停靠 | 层级 | 行为 |
|---|---|---|
| 登录 | 产品级强制停靠（`ProductPolicy.daichaozuo()`） | 检测到登录页即停手交还本人；停靠期不采集、不截图 |
| 实名认证 | 规格级硬让位（§5 第 6 类） | 停手交还；让位期暂停截图 |
| 授权确认（服务协议 / RAM 授权 / 开通确认弹窗） | §5 第 9 类：规格层条件让位、代操作策略层强制停靠（2026-10-08 裁定）；playbook 以 `human_stop: "authorization"` 显式标注为主判据 | 停手交还，不自动点「确定」 |
| OTP / 短信验证码 | 规格级硬让位（§5 第 7 类） | 停手交还 |

## 3. 建 key 步骤（对应 bailian.json 的 steps）

1. 导航到 API-Key 管理页 → 让位引擎先行拦截登录/实名/授权页。
2. 点「创建 API Key」→ 弹窗内选**归属账号**（主账号或 RAM 子账号）与**归属业务空间**。
3. 配最小权限（见 §4）→ 人确认后点「确定」。
4. **一次性 harvest**：明文只显示一次，执行器内部读一次直入 vault（`credentials.harvest_key`），agent 上下文只有占位符 `<<vault:bailian_api_key>>`；本步审计抑图（§6 敏感时刻）。
5. 关弹窗 → 跑探针（见 §6）。关闭后明文不可再看，丢失只能重置（旧 key 失效——重置属破坏性动作，人确认才动）。

页面特征（预期观测，写进每步 expect）：URL 含 `api-key`；AX 含「业务空间」「IP」等弹窗字段。按钮与字段的**确切文案待实测校准**，playbook 定位不到即按 §8 触发 `playbook_mismatch` 低置信让位，绝不即兴发挥。

## 4. 最小权限设置项

- **业务空间**：用显式创建的业务空间，不用默认空间承载生产——公开文档口径：默认业务空间无法设置模型调用/调优/部署限制，精细化权限必须在自建空间启用；模型可用性与限流由业务空间策略决定，Key 自动继承。
- **IP 白名单**：创建时可配；有自有固定出口 IP 就填，无明确需求留空前须向人确认，不默认敞开也不擅自锁死。
- **可访问模型范围**：只勾客户实际要用的模型；Token Plan / Coding Plan 的专属 key（`sk-sp-` 开头）与普通通用 key 是两类、抵扣口径不同，本 playbook 只建**普通通用 key**，专属 key 不在本流程内。

## 5. vault 落存字段

| 字段 | 值 |
|---|---|
| name | `bailian_api_key` |
| provider | `aliyun-bailian` |
| region | 创建时地域（如 `cn-beijing`） |
| workspace | 归属业务空间名 |
| base_url（北京，OpenAI 兼容） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| status | 探针结果：verified / unverified（失败不删值，见 §6） |

## 6. 探针验活样例

```bash
curl -s -o /dev/null -w '%{http_code}' \
  https://dashscope.aliyuncs.com/compatible-mode/v1/models \
  -H "Authorization: Bearer <<vault:bailian_api_key>>"
# 期望 200。失败处置（§7 定版）：vault 值不删、标 unverified、
# 复核提示带 HTTP 状态/错误类型，人确认才删。
```

验收口径照 F4：日志 grep key 前缀 0 命中（标定）；撤销后探针须转失败才算撤销真生效。

## 7. 常见失败与风控提示

- **地域不匹配**：北京生成的 key 调其他地域接口直接报错——落存时 region 必填，调用方按 region 选 base_url。
- **免费额度误扣**：普通 key 优先抵扣新人免费额度；未实名账号强制「用完即停」，实名账号注意该开关状态，避免探针/试用之外产生意外按量账单（本 playbook 不动计费开关，只提示）。
- **控制台改版**：百炼控制台迭代快，expect 不符即停（§8），把不符截图位与实际 AX 文本带回校准本文件，不硬续。
- **RAM 子账号权限不足**：建 key / 配空间权限需相应 RAM 策略，命中权限不足页按低置信让位交人，不代改 RAM 策略。

## 8. 撤销与删 key 路径

同一 API-Key 管理页 → 目标 key 行 → 删除。删除属 §5 不可逆类：**人确认后才执行**；执行后重跑 §6 探针，预期非 200，验过才关单。

## 9. 待校准清单（首次真跑时一次清掉）

1. 「创建 API Key」按钮确切文案与创建弹窗字段名（业务空间 / IP 白名单 / 模型范围的 AX 名）。
2. API-Key 页深链在登录态下的实际落点（是否仍为 `#/api-key`）。
3. 明文弹窗的 DOM/AX 形态——harvest 的读取点是否可经 AX 树拿到（拿不到则该步改人贴入 vault、agent 仍只见占位符）。
4. 探针端点以北京地域实测一次 200 后，把 region/base_url 写死进落存默认值。
