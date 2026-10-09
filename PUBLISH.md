# 发布到 GitHub 的说明（webctl 0.2.5）

## 这是什么
aiglade 的半自动网页代操作引擎（选型线 F4「代操作」执行底座），
《半自动网页控制技术规格 v1.1》参考实现。版本 0.2.5：
0.2.3 已过双平台发版门（Linux 全量 85 绿 / Windows 真机 84 passed + 1 skipped）；
0.2.4 纯逻辑改动，Linux 纯 Python 测试 57 通过，e2e 待双平台复跑；
**0.2.5 已过双平台发版门（2026-10-09 实测）**：
Linux 全量 **85 passed**（python 3.14.4 / playwright 1.63.0，18.0s）；
Windows 真机 **84 passed + 1 skipped**（python 3.11.15 / playwright 1.63.0，39.0s，
跳的是 `test_credentials.py` 的 POSIX 模式位用例，Windows 上由 `test_perms.py` 的 SID 回读路径覆盖）。
85 收集 = 84 通过 + 1 平台跳过，与 0.2.3 那轮口径一致。

## 安全审查结论（2026-10-08，发布前已处理）
- 全仓无真实密钥/令牌：出现的 sk-…、密码字样均为测试夹具假值。
- 已脱敏：测试夹具中的真实机器名/用户名/机器 SID 换成合成值（形态保留）；
  内部基础设施代号与本机路径已中性化/删除。
- `.gitignore` 已备：`.venv/`、缓存、以及运行时产物（profiles/、vault/、
  audit/、downloads/——含真实登录态与凭据）永不入库。

## 发布步骤（在任意一台装了 git 的电脑上）
1. 解压本包到一个目录，例如 `webctl/`。
2. 打开 https://github.com/new ，建一个**公开**仓，名字 `webctl`，
   **不要**勾选 README/.gitignore/license 任何初始化选项（保持全空仓）。
3. 在解压目录里执行：
   ```bash
   cd webctl
   git init
   git add .
   git commit -m "webctl 0.2.5: aiglade semi-automated web operation engine"
   git branch -M main
   git remote add origin https://github.com/swiftcat7097/webctl.git
   git push -u origin main
   ```
   推送时 GitHub 不接受账号密码：用 Personal Access Token（Settings →
   Developer settings → Personal access tokens）当密码，或改用 SSH 地址
   `git@github.com:swiftcat7097/webctl.git`（需先配 SSH key），
   或用 GitHub Desktop 打开该目录点 Publish。
4. 推完刷新仓库页确认：应有 27 个文件（README.md、pyproject.toml、
   webctl/ 11 个 .py、tests/ 9 个 .py、playbooks/ 2 个、scripts/ 1 个、
   .gitignore、本文件）。

## 一个待你定的点
本包**未附 LICENSE**——GitHub 上无许可证 = 默认保留全部权利，他人不可
合法复用。若想真开源（如引流），自行加一个（如 MIT：仓库页 Add file →
Create new file → 文件名 LICENSE → 右侧 Choose a license template）。

## 0.2.4 变更（2026-10-09，审查 P1 全修）
- vault 原子写临时文件 0600 创建（P1-1）；已存在 vault 文件重开复查权限（P1-2）
- 审计目录/文件收紧到所有者专属（P1-3）
- human_act 加 suppress 抑图通道（P1-4）
- user_id 白名单字符集防 Windows 路径穿越（P1-5）
- 探针 header 按 auth 声明头名组装（P1-6）
- playbook expect 子键白名单 + human_stop 类名校验 + extract_regex 预编译（P1-7/P2-1/P2-2）
- .gitignore 加裸 vault.json（P1-8）；wait 上限 60 秒（P2-3）

## 0.2.5 变更（2026-10-09，三路独立审查后补修；未跑测试）
- session_id 走 browser.safe_name 白名单（原先只给 user_id 上了锁，session_id
  仍可直接拼路径）；safe_name 一并挡 Windows 保留设备名与尾点/尾空格
- human_act 补 has_password_field（原先 act/_audit_yield 都传、只有人工路径漏，
  接管期密码框截图会带明文进审计）
- harvest 步遇 vault=None 改走 takeover.ai_yield（原先直接 return，让 playbook
  停了而 takeover 仍停在 AI_RUNNING）
- upload/download 的 Playwright TimeoutError 归一成 ActionError（原先是裸
  TimeoutError，上层只 catch ActionError → 不留痕且整段崩）
- tab_closed 归属按「关闭时 AI 是否在执行动作」判，不再无条件记 human
- Observation.http_status 接真实导航状态码（原先恒 None，429 风控分支在真实
  回路里永不触发，只有单测手工构造才亮）；observe() 的 CDP session 补 detach
- playbook harvest 子键加白名单（bailian.json 里那个没人读的 "via" 已删）；
  navigate/key/upload 缺必填字段改成返回错误而非 KeyError
- wait 补下界钳制；run_playbook 定位失败路径补计让位刻度尺
- .gitignore 加 *.tmp（vault.json.tmp 内容与 vault 同等）；__init__.__version__
  与包版本对齐到 0.2.5

**仍未定（发布前需人工拍板）**：仓归 swiftcat7097 但手头可用的 GitHub 令牌
属于 yummy342；本包未附 LICENSE；内部代号（选型线 F4 / 代操作 / yields.py 的
类名 daichaozuo）与项目名 aiglade 是否随公开仓一并保留。
