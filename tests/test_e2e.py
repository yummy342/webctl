"""端到端（真 Chromium + 本地站点）：
§1 关掉重开仍登录；§2 AX 树定位 + 归一化坐标点中；§3/§7 占位符注入且
真值不进审计；§4 人控期间 AI 动作被拦；§5 不可逆目标被拦；
§8 playbook 跑通表单 + 预期不符即停。
"""
import functools
import http.server
import threading

import pytest

from webctl.actions import Action, ActionExecutor
from webctl.browser import BrowserManager
from webctl.credentials import FileVault
from webctl.observe import observe
from webctl.playbook import Playbook
from webctl.session import AgentSession
from webctl.takeover import State
from webctl.yields import YieldDecision, YieldEngine

PAGES = {
    "login.html": """<!doctype html><html><head><meta charset="utf-8"><title>登录</title></head><body>
      <h1>账号登录</h1>
      <input aria-label="用户名" id="u">
      <input aria-label="密码" id="p" type="password">
      <button id="loginBtn">登录</button>
      <div id="msg"></div>
      <script>
        document.getElementById('loginBtn').onclick = () => {
          localStorage.setItem('sess', '1');
          document.getElementById('msg').textContent = '欢迎回来';
        };
      </script></body></html>""",
    "home.html": """<!doctype html><html><head><meta charset="utf-8"><title>首页</title></head><body>
      <h1>控制台首页</h1><div id="state">未登录</div>
      <script>
        if (localStorage.getItem('sess') === '1')
          document.getElementById('state').textContent = '已登录状态';
      </script></body></html>""",
    "form.html": """<!doctype html><html><head><meta charset="utf-8"><title>资料表单</title></head><body>
      <h1>填写资料</h1>
      <input aria-label="姓名" id="name">
      <input aria-label="地址" id="addr">
      <button id="saveBtn">保存</button>
      <div id="msg"></div>
      <script>
        document.getElementById('saveBtn').onclick = () => {
          document.getElementById('msg').textContent = '已保存';
        };
      </script></body></html>""",
    "keys.html": """<!doctype html><html><head><meta charset="utf-8"><title>API Key 管理</title></head><body>
      <h1>API Key 管理</h1>
      <button id="createBtn">创建 Key</button>
      <button id="delBtn">删除此 Key</button>
      <button id="closeBtn">关闭</button>
      <div id="msg"></div>
      <script>
        document.getElementById('createBtn').onclick = () => {
          document.getElementById('msg').textContent =
            '您的 API Key 已生成：sk-test1234567890abcdef';
        };
        document.getElementById('delBtn').onclick = () => {
          document.getElementById('msg').textContent = '已删除';
        };
        document.getElementById('closeBtn').onclick = () => {
          document.getElementById('msg').textContent = '';
        };
      </script></body></html>""",
}

PAGES["pay.html"] = """<!doctype html><html><head><meta charset="utf-8"><title>收银台</title></head><body>
  <h1>收银台</h1><div>应付 ¥59.00</div>
  <button id="payBtn">确认付款</button>
  <div id="msg"></div>
  <script>
    document.getElementById('payBtn').onclick = () => {
      document.getElementById('msg').textContent = '已付款';
    };
  </script></body></html>"""

PAGES["dl.html"] = """<!doctype html><html><head><meta charset="utf-8"><title>下载</title></head><body>
  <h1>凭据下载</h1>
  <a href="/download.bin" download="cred.txt">下载凭据</a>
</body></html>"""

PAGES["tabs.html"] = """<!doctype html><html><head><meta charset="utf-8"><title>多标签</title></head><body>
  <h1>多标签页</h1>
  <a href="/form.html" target="_blank">打开表单</a>
</body></html>"""

PAGES["grid.html"] = """<!doctype html><html><head><meta charset="utf-8"><title>网格</title></head><body>
  <h1>元素网格</h1><div id="count">已点：0</div>
  <div style="display:grid;grid-template-columns:repeat(6,120px);gap:8px">
      GRID_BUTTONS
  </div>
  <script>
    const hit = new Set();
    document.querySelectorAll('.cell').forEach(b => b.onclick = () => {
      hit.add(b.id);
      document.getElementById('count').textContent = '已点：' + hit.size;
    });
  </script></body></html>""".replace("GRID_BUTTONS", '<button class="cell" id="b01">按钮01</button>\n      <button class="cell" id="b02">按钮02</button>\n      <button class="cell" id="b03">按钮03</button>\n      <button class="cell" id="b04">按钮04</button>\n      <button class="cell" id="b05">按钮05</button>\n      <button class="cell" id="b06">按钮06</button>\n      <button class="cell" id="b07">按钮07</button>\n      <button class="cell" id="b08">按钮08</button>\n      <button class="cell" id="b09">按钮09</button>\n      <button class="cell" id="b10">按钮10</button>\n      <button class="cell" id="b11">按钮11</button>\n      <button class="cell" id="b12">按钮12</button>\n      <button class="cell" id="b13">按钮13</button>\n      <button class="cell" id="b14">按钮14</button>\n      <button class="cell" id="b15">按钮15</button>\n      <button class="cell" id="b16">按钮16</button>\n      <button class="cell" id="b17">按钮17</button>\n      <button class="cell" id="b18">按钮18</button>\n      <button class="cell" id="b19">按钮19</button>\n      <button class="cell" id="b20">按钮20</button>\n      <button class="cell" id="b21">按钮21</button>\n      <button class="cell" id="b22">按钮22</button>\n      <button class="cell" id="b23">按钮23</button>\n      <button class="cell" id="b24">按钮24</button>')


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    d = tmp_path_factory.mktemp("site")
    for name, html in PAGES.items():
        (d / name).write_text(html, encoding="utf-8")
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/probe":
                ok = self.headers.get("Authorization") == \
                    "Bearer sk-test1234567890abcdef"
                body = b'{"ok":true}' if ok else b'{"error":"bad key"}'
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/download.bin":
                body = b"file-body-123"
                self.send_response(200)
                self.send_header("Content-Disposition", 'attachment; filename="cred.txt"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def log_message(self, *args):
            pass

    handler = functools.partial(Handler, directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def base_dir(tmp_path):
    return tmp_path / "webctl-data"


def _login_via_kit(mgr: BrowserManager, user: str, site: str):
    page = mgr.page(user)
    page.goto(f"{site}/login.html")
    obs = observe(page)
    btn = obs.find("登录", role="button")
    assert btn is not None and btn.x is not None
    ActionExecutor(page).execute(Action.click(btn.x, btn.y))
    page.wait_for_timeout(150)
    return page


def test_profile_persistence_across_restarts(site, base_dir):
    """§1 验收：关掉重开，之前登录过的站仍保持登录；不同用户隔离。"""
    with BrowserManager(base_dir) as m1:
        _login_via_kit(m1, "alice", site)
    with BrowserManager(base_dir) as m2:  # 全新进程级重开，同一 profile 目录
        page = m2.page("alice")
        page.goto(f"{site}/home.html")
        assert "已登录状态" in observe(page).ax_texts() or \
            "已登录状态" in page.inner_text("body")
        page_bob = m2.page("bob")
        page_bob.goto(f"{site}/home.html")
        assert "未登录" in page_bob.inner_text("body")


def test_observe_locates_and_click_hits(site, base_dir):
    """§2 验收：截图 + AX 树能定位登录按钮，归一化坐标点中。"""
    with BrowserManager(base_dir) as m:
        page = m.page("carol")
        page.goto(f"{site}/login.html")
        obs = observe(page)
        assert obs.screenshot_png[:4] == b"\x89PNG"
        btn = obs.find("登录", role="button")
        assert btn is not None and 0 <= btn.x <= 1000 and 0 <= btn.y <= 1000
        ActionExecutor(page).execute(Action.click(btn.x, btn.y))
        page.wait_for_timeout(150)
        assert "欢迎回来" in page.inner_text("body")


def test_playbook_runs_form(site, base_dir):
    with BrowserManager(base_dir) as m:
        page = m.page("dave")
        page.goto(f"{site}/form.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="pb1",
                                   engine=YieldEngine())
        pb = Playbook.from_dict({"name": "填表", "steps": [
            {"name": "填姓名", "expect": {"url_contains": "/form"},
             "action": {"kind": "type", "target_name": "姓名", "text": "张三"}},
            {"name": "填地址",
             "action": {"kind": "type", "target_name": "地址", "text": "北京市"}},
            {"name": "保存", "action": {"kind": "click", "target_name": "保存"}},
        ]})
        results = sess.run_playbook(pb)
        assert all(r["ok"] for r in results), results
        assert page.locator("#name").input_value() == "张三"
        assert "已保存" in page.inner_text("body")


def test_playbook_mismatch_stops_and_yields(site, base_dir):
    with BrowserManager(base_dir) as m:
        page = m.page("erin")
        page.goto(f"{site}/form.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="pb2",
                                   engine=YieldEngine())
        pb = Playbook.from_dict({"name": "错位", "steps": [
            {"name": "第一步", "expect": {"url_contains": "/billing"},
             "action": {"kind": "click", "target_name": "保存"}},
        ]})
        results = sess.run_playbook(pb)
        assert not results[0]["ok"] and "playbook_mismatch" in results[0]["stopped"]
        assert sess.takeover.state is State.WAITING_HUMAN


def test_irreversible_target_blocked(site, base_dir):
    with BrowserManager(base_dir) as m:
        page = m.page("frank")
        page.goto(f"{site}/keys.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="irr1",
                                   engine=YieldEngine())
        obs = observe(page)
        node = obs.find("删除此 Key", role="button")
        assert node is not None
        r = sess.act(Action.click(node.x, node.y), target_node=node)
        assert isinstance(r, YieldDecision) and r.cls == "irreversible" and r.hard
        assert "已删除" not in page.inner_text("body")  # 没真点下去


def test_placeholder_secret_never_reaches_audit(site, base_dir):
    secret = "p@ssw0rd-unique-7788"
    vault = FileVault(base_dir / "vault.json")
    vault.put("login_pw", secret)
    with BrowserManager(base_dir) as m:
        page = m.page("grace")
        page.goto(f"{site}/login.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="sec1",
                                   engine=YieldEngine(), vault=vault)
        obs = observe(page)
        pw = obs.find("密码")
        assert pw is not None and pw.x is not None
        r = sess.act(Action.type(pw.x, pw.y, "<<vault:login_pw>>"),
                     has_saved_credential=True)
        assert not isinstance(r, YieldDecision), r
        assert page.locator("#p").input_value() == secret  # 真值进了页面
    audit_text = sess.audit.path.read_text(encoding="utf-8")
    assert secret not in audit_text                        # 审计里没有真值
    assert "<<vault:login_pw>>" in audit_text              # 只有占位符


def test_takeover_blocks_ai_actions(site, base_dir):
    with BrowserManager(base_dir) as m:
        page = m.page("heidi")
        page.goto(f"{site}/form.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="tk1",
                                   engine=YieldEngine())
        sess.takeover.human_takeover(observe(page))
        with pytest.raises(RuntimeError):
            sess.act(Action.click(500, 500))


# —— 0.2.0 审查轮新增验收 ——

def test_harvest_playbook_end_to_end(site, base_dir):
    """P0-2：harvest 步真执行——key 入 vault、探针验活、弹窗可关、全程抑图。"""
    from webctl.audit import AuditLog
    vault = FileVault(base_dir / "vault.json")
    with BrowserManager(base_dir) as m:
        page = m.page("ivan")
        page.goto(f"{site}/keys.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="hv1",
                                   engine=YieldEngine(), vault=vault)
        pb = Playbook.from_dict({
            "name": "建 key",
            "probe": {"url": f"{site}/probe", "method": "GET",
                      "auth": "Bearer <<vault:test_key>>", "expect_status": 200},
            "steps": [
                {"name": "点创建", "action": {"kind": "click", "target_name": "创建 Key"}},
                {"name": "回收", "harvest": {"vault_name": "test_key"}},
                {"name": "关弹窗", "action": {"kind": "click", "target_name": "关闭"}},
            ]})
        results = sess.run_playbook(pb)
        assert all(r["ok"] for r in results), results
        assert results[1]["detail"] == "verified"
        assert vault.get("test_key") == "sk-test1234567890abcdef"
        assert vault.entry("test_key")["status"] == "verified"
    entries = AuditLog.replay(sess.audit.path)
    harvest_entries = [e for e in entries if e["kind"] == "harvest"]
    assert harvest_entries and harvest_entries[0]["screenshot_thumb"] is None
    # 揭示 key 的那一步（点创建）审计不许留图——令牌串在页面上即抑；
    # 关弹窗之后页面已无 key，缩略图恢复留存是正当的
    actions = [e for e in entries if e["kind"] == "action"]
    assert actions[0]["screenshot_thumb"] is None
    assert "token_visible" in (actions[0]["screenshot_suppressed"] or "")


def test_harvest_failure_stops_before_close(site, base_dir):
    """harvest 取不到值 → 停，后续步（关弹窗）绝不执行。"""
    vault = FileVault(base_dir / "vault.json")
    with BrowserManager(base_dir) as m:
        page = m.page("judy")
        page.goto(f"{site}/form.html")  # 这页根本没有 key
        sess = AgentSession.create(page, base_dir=base_dir, session_id="hv2",
                                   engine=YieldEngine(), vault=vault)
        pb = Playbook.from_dict({"name": "无 key", "steps": [
            {"name": "回收", "harvest": {"vault_name": "nope"}},
            {"name": "保存", "action": {"kind": "click", "target_name": "保存"}},
        ]})
        results = sess.run_playbook(pb)
        assert len(results) == 1 and not results[0]["ok"]
        assert "harvest" in results[0]["stopped"]
        assert vault.get("nope") is None


def test_payment_page_never_hard_charged(site, base_dir):
    """§5 端到端：支付页命中让位，绝不硬闯付款。"""
    with BrowserManager(base_dir) as m:
        page = m.page("karl")
        page.goto(f"{site}/pay.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="pay1",
                                   engine=YieldEngine())
        obs = observe(page)
        node = obs.find("确认付款", role="button")
        r = sess.act(Action.click(node.x, node.y), target_node=node)
        assert isinstance(r, YieldDecision) and r.cls == "payment"
        assert "已付款" not in page.inner_text("body")


def test_consent_stop_in_daichaozuo_session(site, base_dir):
    """F4 授权确认：代操作策略下点「同意授权」被硬停靠。"""
    from webctl.yields import ProductPolicy
    with BrowserManager(base_dir) as m:
        page = m.page("lena")
        page.goto(f"{site}/form.html")
        page.evaluate("""() => {
          const b = document.createElement('button');
          b.textContent = '同意授权'; b.id = 'consentBtn';
          document.body.appendChild(b);
        }""")
        page.wait_for_timeout(100)
        sess = AgentSession.create(page, base_dir=base_dir, session_id="cs1",
                                   engine=YieldEngine(ProductPolicy.daichaozuo()))
        obs = observe(page)
        node = obs.find("同意授权", role="button")
        assert node is not None
        r = sess.act(Action.click(node.x, node.y), target_node=node)
        assert isinstance(r, YieldDecision) and r.cls == "authorization" and r.hard


def test_download_primitive_lands_in_dir(site, base_dir):
    dl_dir = base_dir / "downloads"
    with BrowserManager(base_dir) as m:
        page = m.page("mike")
        page.goto(f"{site}/dl.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="dl1",
                                   engine=YieldEngine(), download_dir=dl_dir)
        obs = observe(page)
        node = obs.find("下载凭据")
        assert node is not None and node.x is not None
        r = sess.act(Action.download(node.x, node.y), target_node=node)
        assert not isinstance(r, YieldDecision), r
        assert r["downloaded"] == "cred.txt"
        assert (dl_dir / "cred.txt").read_bytes() == b"file-body-123"


def test_new_tab_adopted_and_audited(site, base_dir):
    """§4 R4：新标签收编进 session 并留痕，可切换活动页。"""
    from webctl.audit import AuditLog
    with BrowserManager(base_dir) as m:
        page = m.page("nina")
        page.goto(f"{site}/tabs.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="tab1",
                                   engine=YieldEngine())
        obs = observe(page)
        node = obs.find("打开表单")
        sess.act(Action.click(node.x, node.y), target_node=node)
        page.wait_for_timeout(400)
        assert len(sess.pages) >= 2
        entries = AuditLog.replay(sess.audit.path)
        assert any(e["kind"] == "tab_opened" for e in entries)
        new_page = sess.pages[-1]
        sess.switch_page(new_page)
        o2, _ = sess.observe_now()
        assert "/form.html" in o2.url


def test_human_act_uses_fresh_observation(site, base_dir):
    """P2-3：human_act 记当场页面，不许用接管前的旧观测。"""
    from webctl.audit import AuditLog
    with BrowserManager(base_dir) as m:
        page = m.page("olga")
        page.goto(f"{site}/form.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="ha1",
                                   engine=YieldEngine())
        sess.observe_now()  # 留下 form 页的旧观测
        page.goto(f"{site}/keys.html")  # 人在接管期自己导航走了
        sess.human_act("人工处理完毕")
        entries = AuditLog.replay(sess.audit.path)
        human = [e for e in entries if e["actor"] == "human"]
        assert human and "/keys.html" in human[-1]["url"]


def test_rejected_action_leaves_audit_trace(site, base_dir):
    """§3 越界留痕：被拒动作也要进审计。"""
    from webctl.actions import ActionError
    from webctl.audit import AuditLog
    with BrowserManager(base_dir) as m:
        page = m.page("pete")
        page.goto(f"{site}/form.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="rj1",
                                   engine=YieldEngine())
        with pytest.raises(ActionError):
            sess.act(Action.navigate("javascript:alert(1)"))
        entries = AuditLog.replay(sess.audit.path)
        assert any(e["kind"] == "action_rejected" for e in entries)


def test_first_hit_rate_on_grid(site, base_dir):
    """§2 R5：24 元素网格，定位+点击首击命中率 ≥95%。"""
    with BrowserManager(base_dir) as m:
        page = m.page("quinn")
        page.goto(f"{site}/grid.html")
        ex = ActionExecutor(page)
        hits = 0
        for i in range(1, 25):
            obs = observe(page)
            node = obs.find(f"按钮{i:02d}", role="button")
            if node is None or node.x is None:
                continue
            before = page.inner_text("#count")
            ex.execute(Action.click(node.x, node.y))
            page.wait_for_timeout(30)
            if page.inner_text("#count") != before:
                hits += 1
        rate = hits / 24
        assert rate >= 0.95, f"首击命中率 {rate:.2f}（{hits}/24）"


def test_probe_ignores_system_proxy(site, base_dir, monkeypatch):
    """复验单根因 B 的标定：系统代理被毒化（指向必死地址）时，
    探针仍须直连——它带着刚回收的真 key，不许经任何中间人。
    对照腿：显式走系统代理的 opener 同环境下必须失败，证明标定有牙。"""
    import urllib.request
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    with BrowserManager(base_dir) as m:
        page = m.page("ruth")
        page.goto(f"{site}/form.html")
        sess = AgentSession.create(page, base_dir=base_dir, session_id="px1",
                                   engine=YieldEngine())
        sess.probe_config = {"url": f"{site}/probe", "method": "GET",
                             "auth": "Bearer <<vault:test_key>>",
                             "expect_status": 200}
        probe = sess._build_probe()
        r = probe("sk-test1234567890abcdef")
        assert r.ok and r.http_status == 200, r  # 直连拿到真答案
    # 对照腿：走系统代理（读环境变量）在同环境下到不了本地服务器
    poisoned = urllib.request.build_opener(urllib.request.ProxyHandler())
    try:
        with poisoned.open(f"{site}/probe", timeout=5) as resp:
            raise AssertionError(f"对照腿不该成功，却拿到 {resp.status}")
    except AssertionError:
        raise
    except Exception:
        pass  # 连接被拒/超时 = 对照腿如期失败
