"""§2 AI 可观测：每步给 AI 两样东西——截图 + 可访问性树（AX tree）。

- AX 树经 CDP Accessibility.getFullAXTree 取得（role + name），几何经
  DOM.getBoxModel 取后端节点包围盒；全程不向页面注入任何脚本。
- 坐标归一化 0-1000（相对 viewport），viewport 变了不崩。
- 过滤隐藏/被忽略节点；只保留带 name 或可交互的节点，控制体积。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from playwright.sync_api import Page

from .browser import VIEWPORT, last_nav_status

INTERACTIVE_ROLES = {
    "button", "link", "textbox", "checkbox", "radio", "combobox", "listbox",
    "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch",
    "slider", "searchbox", "spinbutton", "option", "treeitem",
}


def normalize(px: float, total: int) -> int:
    """像素 → 0-1000 归一化坐标，钳制在范围内。"""
    return max(0, min(1000, round(px / total * 1000)))


def denormalize(n: float, total: int) -> float:
    return n / 1000 * total


@dataclass
class AXNode:
    role: str
    name: str
    x: int | None = None  # 归一化中心坐标；取不到几何时为 None
    y: int | None = None
    backend_id: int | None = None

    def to_dict(self) -> dict:
        return {"role": self.role, "name": self.name, "x": self.x, "y": self.y}


@dataclass
class Observation:
    url: str
    title: str
    screenshot_png: bytes
    ax_tree: list[AXNode] = field(default_factory=list)
    http_status: int | None = None

    def ax_texts(self) -> list[str]:
        return [n.name for n in self.ax_tree if n.name]

    def find(self, name_part: str, role: str | None = None) -> AXNode | None:
        for n in self.ax_tree:
            if name_part in n.name and (role is None or n.role == role):
                return n
        return None

    def summary_hash(self) -> int:
        """粗粒度页面指纹：供 §5 低置信（动作后页面无变化）与 §4 交接 diff 用。"""
        items = tuple(sorted((n.role, n.name, n.x, n.y) for n in self.ax_tree))
        return hash((self.url, items))


def _prop(node: dict, key: str):
    for p in node.get("properties", []):
        if p.get("name") == key:
            return p.get("value", {}).get("value")
    return None


def observe(page: Page, *, max_nodes: int = 200) -> Observation:
    """对当前页面做一次完整观测。失败时降级为只有截图与 URL，不抛错给上层。"""
    shot = page.screenshot(type="png")
    width = page.viewport_size["width"] if page.viewport_size else VIEWPORT["width"]
    height = page.viewport_size["height"] if page.viewport_size else VIEWPORT["height"]
    nodes: list[AXNode] = []
    cdp = None
    try:
        cdp = page.context.new_cdp_session(page)
        tree = cdp.send("Accessibility.getFullAXTree")
        raw = tree.get("nodes", [])
        kept: list[dict] = []
        for nd in raw:
            if nd.get("ignored"):
                continue
            if _prop(nd, "hidden"):
                continue
            role = (nd.get("role") or {}).get("value", "")
            name = (nd.get("name") or {}).get("value", "") or ""
            if not name and role not in INTERACTIVE_ROLES:
                continue
            kept.append(nd)
        for nd in kept[:max_nodes]:
            role = (nd.get("role") or {}).get("value", "")
            name = (nd.get("name") or {}).get("value", "") or ""
            x = y = None
            bid = nd.get("backendDOMNodeId")
            if bid is not None:
                try:
                    box = cdp.send("DOM.getBoxModel", {"backendNodeId": bid})
                    quad = box["model"]["content"]  # [x1,y1,x2,y2,x3,y3,x4,y4]
                    cx = (quad[0] + quad[4]) / 2
                    cy = (quad[1] + quad[5]) / 2
                    x, y = normalize(cx, width), normalize(cy, height)
                except Exception:
                    pass
            nodes.append(AXNode(role=role, name=name, x=x, y=y, backend_id=bid))
    except Exception:
        nodes = []
    finally:
        # CDP session 不 detach 会一路累积：act() 每步至少观测 2 次、每个
        # playbook 步 1 次，长会话必漏。detach 失败也不能影响观测结果。
        if cdp is not None:
            try:
                cdp.detach()
            except Exception:
                pass
    return Observation(url=page.url, title=page.title(), screenshot_png=shot,
                       ax_tree=nodes, http_status=last_nav_status(page))
