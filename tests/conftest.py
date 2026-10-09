import io

import pytest
from PIL import Image

from webctl.observe import AXNode, Observation


def make_png(w: int = 1280, h: int = 800) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


PNG = make_png()


def make_obs(url: str = "https://example.com/", title: str = "示例",
             texts: list[str] | None = None, png: bytes | None = None) -> Observation:
    nodes = [AXNode(role="text", name=t) for t in (texts or [])]
    return Observation(url=url, title=title, screenshot_png=png if png is not None else PNG,
                       ax_tree=nodes)
