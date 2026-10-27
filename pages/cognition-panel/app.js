/* 为你而在的认知 · 面板逻辑
   通过 AstrBotPluginPage bridge 调后端；接口前缀与插件名一致。 */

/* 注意：bridge.apiGet/apiPost 会自己补插件前缀，这里只能传相对路径。
   之前传了 "/astrbot_plugin_cognition/overview"，前缀重复 → 404 → Failed to fetch。 */
const PLUGIN = "astrbot_plugin_cognition";

const TABS = [
  { key: "overview", n: "01", label: "总览", title: "总览", desc: "运行时状态、权限、额度与工具盘点" },
  { key: "experts", n: "02", label: "专家", title: "子代理专家", desc: "多专家定义与今日调用额度" },
  { key: "goals", n: "03", label: "目标", title: "长期目标", desc: "跨会话保留，只有显式完成/放弃才结束" },
  { key: "skills", n: "04", label: "技能", title: "技能库", desc: "人写流程、模型只读，存盘即生效" },
  { key: "audit", n: "05", label: "记录", title: "动作记录", desc: "她最近动过什么：工具、成功与否" },
  { key: "about", n: "06", label: "关于", title: "关于", desc: "插件信息与设计取舍" },
];

const el = (id) => document.getElementById(id);
const state = { tab: "overview", data: {} };

/* ---------------- bridge 兼容层 ---------------- */
/* bridge 可能晚一点注入，等它一下（最多 3 秒） */
async function waitBridge() {
  for (let i = 0; i < 30; i += 1) {
    if (window.AstrBotPluginPage) return window.AstrBotPluginPage;
    await new Promise((r) => setTimeout(r, 100));
  }
  return null;
}

function unwrap(res) {
  if (res && typeof res === "object" && res.data !== undefined) return res.data;
  return res;
}

async function apiGet(path) {
  const api = window.AstrBotPluginPage;
  if (api && typeof api.apiGet === "function") {
    return unwrap(await api.apiGet(path));
  }
  const res = await fetch(`/${PLUGIN}/${path}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const api = window.AstrBotPluginPage;
  if (api && typeof api.apiPost === "function") {
    return unwrap(await api.apiPost(path, body || {}));
  }
  const res = await fetch(`/${PLUGIN}/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body || {}),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

/* ---------------- 小工具 ---------------- */
function esc(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toast(msg, bad) {
  const box = document.createElement("div");
  box.className = "toast" + (bad ? " bad" : "");
  box.textContent = msg;
  document.body.appendChild(box);
  setTimeout(() => box.remove(), 2200);
}

function cell(k, v, cls) {
  return `<div class="cell"><div class="k">${esc(k)}</div>
    <div class="v ${cls || ""}">${v}</div></div>`;
}

/* ---------------- 渲染：总览 ---------------- */
function renderOverview(d) {
  const ws = d.workspace || {};
  const auth = d.auth || {};
  const del = d.delegate || {};
  const tools = d.tools || {};
  const counts = d.counts || {};

  const accessTag = ws.access === "full"
    ? '<span class="tag warn">FULL</span>'
    : '<span class="tag ok">WORKSPACE</span>';
  const modeTag = `<span class="tag">${esc(ws.mode || "?")}</span>`;

  let html = '<div class="grid">';
  html += cell("版本", esc(d.version || "—"), "accent");
  html += cell("工具数", `<span class="tag done">${tools.total || 0}</span>`, "big");
  html += cell("工作区", `${modeTag} ${accessTag}<div class="v path" style="margin-top:8px">${esc(ws.root || "—")}</div>`);
  html += cell("子代理额度", `${del.left == null ? "—" : del.left} / ${del.limit == null ? "—" : del.limit}`,
    del.left === 0 ? "accent" : "");
  html += cell("进行中目标", counts.goals_open == null ? "—" : counts.goals_open);
  html += cell("技能", counts.skills == null ? "—" : counts.skills);
  html += cell("动作记录", counts.audit == null ? "—" : counts.audit);
  html += cell("数据目录", `<span class="path">${esc(d.data_dir || "—")}</span>`);
  html += "</div>";

  html += '<div class="sect"><h3>权限</h3><div class="rule"></div></div>';
  html += '<div class="grid">';
  html += cell("管理员", esc(auth.admins || "（空）"), "small");
  html += cell("成员", esc(auth.members || "（空）"), "small");
  html += cell("未登记的人", auth.members_open
    ? '<span class="tag warn">算成员</span>'
    : '<span class="tag ok">仅访客</span>');
  html += cell("回退名单", esc(auth.fallback_owner || "（空）"), "small");
  html += "</div>";

  if (tools.names && tools.names.length) {
    html += '<div class="sect"><h3>工具清单</h3><div class="rule"></div></div>';
    html += '<div class="list collapsed" id="tool-list">';
    for (const name of tools.names) {
      html += `<div class="row"><div class="id">TOOL</div>
        <div class="main"><div class="t">toolbox_${esc(name)}</div></div>
        <div class="r"><span class="tag ok">READY</span></div></div>`;
    }
    if (tools.names.length > 8) {
      html += `<button class="more" id="tool-more">展开全部 ${tools.names.length} 个 ↓</button>`;
    }
    html += "</div>";
  }
  return html;
}

/* ---------------- 渲染：专家 ---------------- */
function renderExperts(d) {
  const items = d.experts || [];
  const budget = d.budget || {};
  let html = '<div class="grid">';
  html += cell("今日已用", budget.used == null ? "—" : budget.used);
  html += cell("每日上限", budget.limit == null ? "—" : budget.limit);
  html += cell("剩余", budget.left == null ? "—" : budget.left, "accent");
  html += cell("专家数", items.length);
  html += "</div>";

  if (!items.length) {
    html += `<div class="sect"><h3>说明</h3><div class="rule"></div></div>
      <div class="empty">还没有配置专家。<br /><br />
      在插件配置的 <b>delegate.agents</b> 里写 JSON 数组，例如：<br /><br />
      [{"name":"审稿","description":"挑文档里的问题","system":"你是严格的审稿人…"}]</div>`;
    return html;
  }

  html += '<div class="sect"><h3>专家列表</h3><div class="rule"></div></div><div class="list">';
  for (const e of items) {
    const provider = e.provider_id
      ? `<span class="tag">${esc(e.provider_id)}</span>`
      : '<span class="tag">同默认模型</span>';
    html += `<div class="row"><div class="id">${esc(e.name)}</div>
      <div class="main"><div class="t">${esc(e.description || "（无说明）")}</div>
        <div class="d">${esc(e.system || "（继承默认系统提示）")}</div></div>
      <div class="r">${provider}<br />≤ ${e.max_chars} 字</div></div>`;
  }
  html += "</div>";
  return html;
}

/* ---------------- 渲染：目标 ---------------- */
function renderGoals(d) {
  const items = d.goals || [];
  let html = `<div class="form">
      <input id="goal-title" placeholder="新目标（要做成什么）" />
      <input id="goal-note" placeholder="备注（可选）" />
      <button class="btn primary" id="goal-add">立目标</button>
    </div>`;

  if (!items.length) {
    html += '<div class="empty">现在没有进行中的目标。</div>';
    return html;
  }
  html += '<div class="list">';
  for (const g of items) {
    const tag = g.state === "active"
      ? '<span class="tag done">进行中</span>'
      : g.state === "done"
        ? '<span class="tag ok">已完成</span>'
        : '<span class="tag bad">已放弃</span>';
    const acts = g.state === "active"
      ? `<button class="btn ghost" data-done="${esc(g.id)}">完成</button>
         <button class="btn ghost danger" data-drop="${esc(g.id)}">放弃</button>`
      : "";
    html += `<div class="row"><div class="id">${esc(g.id)}</div>
      <div class="main"><div class="t">${esc(g.title)}</div>
        ${g.note ? `<div class="d">${esc(g.note)}</div>` : ""}</div>
      <div class="r">${tag}<br />${acts}</div></div>`;
  }
  html += "</div>";
  return html;
}

/* ---------------- 渲染：技能 ---------------- */
function renderSkills(d) {
  const items = d.skills || [];
  if (!items.length) {
    return '<div class="empty">技能目录是空的。把 .md 放进数据目录的 skills/ 下即可，存盘即生效。</div>';
  }
  let html = '<div class="list">';
  for (const s of items) {
    html += `<div class="row"><div class="id">SKILL</div>
      <div class="main"><div class="t">${esc(s.name)}</div>
        <div class="d">${esc(s.description || "（无说明）")}</div></div>
      <div class="r">${s.chars} 字<br /><button class="btn ghost" data-skill="${esc(s.name)}">查看</button></div></div>`;
  }
  html += "</div>";
  html += '<div id="skill-body"></div>';
  return html;
}

/* ---------------- 渲染：记录 ---------------- */
function renderAudit(d) {
  const items = d.records || [];
  let html = `<div class="form">
      <span class="dim" style="flex:1">共 ${items.length} 条（最近 120 条）</span>
      <button class="btn ghost danger" id="audit-clear">清空记录</button>
    </div>`;
  if (!items.length) {
    html += '<div class="empty">还没有记录。她用工具时会自动写入。</div>';
    return html;
  }
  html += '<div class="list">';
  for (const r of items) {
    const ok = r.ok !== false;
    const tag = ok ? '<span class="tag ok">OK</span>' : '<span class="tag bad">FAIL</span>';
    const when = String(r.time || "").replace("T", " ").slice(5, 19);
    html += `<div class="row"><div class="id">${esc(r.tool || "?")}</div>
      <div class="main"><div class="t">${esc(r.detail || "")}</div>
        <div class="d">${esc(r.user || "")}</div></div>
      <div class="r">${tag}<br />${esc(when)}</div></div>`;
  }
  html += "</div>";
  return html;
}

/* ---------------- 渲染：关于 ---------------- */
function renderAbout() {
  return `<div class="grid">
    ${cell("插件", "为你而在的认知")}
    ${cell("职责", "她「能动手」的部分：文件、网页、文档、脚本、平台动作")}
    ${cell("设计原则", "路径监狱 · 权限分级 · 审计落盘 · 失败如实说")}
    ${cell("工具数", "32")}
    ${cell("子代理", "多专家 · 可按专家指定模型 · 每日额度")}
    ${cell("技能库", "人写流程、模型只读、存盘即生效")}
  </div>
  <div class="sect"><h3>取舍</h3><div class="rule"></div></div>
  <div class="list">
    <div class="row"><div class="id">不做</div><div class="main"><div class="t">工作流编排 / 中途提问 / 用量台账</div>
      <div class="d">成本高或风险高于收益，写在 CHANGELOG 的「已知边界」里</div></div></div>
    <div class="row"><div class="id">不做</div><div class="main"><div class="t">子代理带工具</div>
      <div class="d">分工是「主代理取数、子代理思考」，避免它乱动工作区</div></div></div>
    <div class="row"><div class="id">注意</div><div class="main"><div class="t">access=full 且 full_roots 留空 = 真全盘</div>
      <div class="d">只有管理员能用，但请想清楚再开</div></div></div>
  </div>`;
}

/* ---------------- 载入与切换 ---------------- */
async function loadOverview() {
  const d = await apiGet("overview");
  state.data.overview = d;
  el("ver").textContent = d.version || "—";
  const chip = el("chip-status");
  chip.className = "chip on";
  el("foot-mid").textContent = `${(d.tools && d.tools.total) || 0} TOOLS`;
  return renderOverview(d);
}

async function loadTab(key) {
  if (key === "overview") return loadOverview();
  if (key === "experts") return renderExperts(await apiGet("experts"));
  if (key === "goals") return renderGoals(await apiGet("goals"));
  if (key === "skills") return renderSkills(await apiGet("skills"));
  if (key === "audit") return renderAudit(await apiGet("audit"));
  if (key === "about") return renderAbout();
  return "<div class=\"empty\">未知页签</div>";
}

function bindActions() {
  const add = el("goal-add");
  if (add) {
    add.onclick = async () => {
      const title = el("goal-title").value.trim();
      const note = el("goal-note").value.trim();
      if (!title) return toast("目标不能为空", true);
      const res = await apiPost("goals/add", { title, note });
      if (res && res.status === "error") return toast(res.message || "失败", true);
      toast("已立目标");
      render();
    };
  }
  document.querySelectorAll("[data-done]").forEach((btn) => {
    btn.onclick = async () => {
      await apiPost("goals/close", { goal_id: btn.dataset.done, state: "done" });
      toast("已完成");
      render();
    };
  });
  document.querySelectorAll("[data-drop]").forEach((btn) => {
    btn.onclick = async () => {
      await apiPost("goals/close", { goal_id: btn.dataset.drop, state: "dropped" });
      toast("已放弃");
      render();
    };
  });
  document.querySelectorAll("[data-skill]").forEach((btn) => {
    btn.onclick = async () => {
      const res = await apiGet("skills/read?name=" + encodeURIComponent(btn.dataset.skill));
      const box = el("skill-body");
      if (!box) return;
      box.innerHTML = `<div class="sect"><h3>${esc(res.name || "")}</h3><div class="rule"></div></div>
        <div class="list"><div class="row"><div class="id">BODY</div>
        <div class="main"><div class="d" style="white-space:pre-wrap">${esc(res.body || "")}</div></div></div></div>`;
    };
  });
  const more = el("tool-more");
  if (more) {
    more.onclick = () => {
      const list = el("tool-list");
      if (!list) return;
      const collapsed = list.classList.toggle("collapsed");
      more.textContent = collapsed ? `展开全部 ${list.querySelectorAll(".row").length} 个 ↓` : "收起 ↑";
    };
  }
  const clear = el("audit-clear");
  if (clear) {
    clear.onclick = async () => {
      await apiPost("audit/clear", {});
      toast("已清空");
      render();
    };
  }
}

function renderRail() {
  el("rail").innerHTML = TABS.map((t) =>
    `<button data-key="${t.key}" class="${t.key === state.tab ? "active" : ""}">
      <span class="n">${t.n}</span><span>${t.label}</span></button>`
  ).join("");
  document.querySelectorAll("#rail button").forEach((btn) => {
    btn.onclick = () => {
      state.tab = btn.dataset.key;
      render();
    };
  });
}

async function render() {
  const meta = TABS.find((t) => t.key === state.tab) || TABS[0];
  el("idx").textContent = meta.n;
  el("title").textContent = meta.title;
  el("desc").textContent = meta.desc;
  renderRail();
  el("panel").innerHTML = '<div class="loading">载入中…</div>';
  try {
    const html = await loadTab(state.tab);
    el("panel").innerHTML = html;
    bindActions();
    el("foot-right").textContent = "READY";
  } catch (err) {
    el("panel").innerHTML = `<div class="empty">读取失败：${esc(err && err.message ? err.message : err)}</div>`;
    el("foot-right").textContent = "ERROR";
  }
}

/* ---------- 坐标跟随（终末地的重力跟随，改成本地坐标读数） ---------- */
document.addEventListener("mousemove", (ev) => {
  const x = String(ev.clientX).padStart(4, "0");
  const y = String(ev.clientY).padStart(4, "0");
  el("coord").textContent = `X ${x} · Y ${y}`;
});

el("refresh").onclick = () => {
  el("chip-status").className = "chip";
  render();
};

/* hash 路由：/#experts 这类可以直接打开某页 */
function applyHash() {
  const key = String(location.hash || "").replace("#", "").trim();
  if (key && TABS.some((t) => t.key === key)) {
    state.tab = key;
  }
}

window.addEventListener("hashchange", () => {
  applyHash();
  render();
});

applyHash();
waitBridge().then(render);
