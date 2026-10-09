"""webctl — 半自动网页控制套件（《半自动网页控制技术规格 v1.1》实现）。

模块与规格的对应：
  browser.py      §1 真浏览器 + 持久 profile
  observe.py      §2 AI 可观测（截图 + AX 树 + 归一化坐标）
  actions.py      §3 动作空间收敛（6 动作 + 受控原语，无任意 JS 通道）
  takeover.py     §4 接管状态机
  yields.py       §5 让位触发器（9 类 + 策略层：规格硬让位 / 产品强制停靠）
  audit.py        §6 审计回放（敏感时刻截图抑制、actor 归属）
  credentials.py  §7 凭证面（占位符注入 / vault 托管 / 一次性 key 最后一公里）
  playbook.py     §8 playbook 层（声明式步骤 + 预期观测，不符即让位）
  session.py      会话胶水：观测 → 让位检查 → 动作 → 审计
"""

__version__ = "0.2.5"
