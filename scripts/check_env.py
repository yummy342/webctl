"""环境一致性检查：playwright 版本必须与 pyproject 的 pin 一致，
且 pin 对应的 chromium 构建已安装。不一致就大声报——pin 的意义是
防指纹漂移，静默用另一个浏览器跑过测试等于没 pin（审查单 §0）。"""
import importlib.metadata as md
import re
import sys
from pathlib import Path

pin = re.search(r'"playwright==([\d.]+)"',
                Path(__file__).with_name("pyproject.toml").read_text() if False else
                (Path(__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")).group(1)
installed = md.version("playwright")
cache = Path.home() / ".cache" / "ms-playwright"
browsers = sorted(p.name for p in cache.glob("chromium*")) if cache.exists() else []
print(f"pin: playwright=={pin} | installed: {installed} | chromium builds: {browsers}")
if installed != pin:
    print(f"⚠️ 版本不一致：装的是 {installed}，pin 的是 {pin}——测试结果不代表 pin 环境")
    sys.exit(1)
print("✅ 环境与 pin 一致")
