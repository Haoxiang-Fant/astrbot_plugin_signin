const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);

function setStatus(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// ================= 图标（自绘扁平线条图标，SVG） =================
// 每个图标是一个 stroke 线条 SVG；尺寸由 CSS 控制（默认 100%）。
const ICONS = {
  // 商店（扁平店招：遮阳棚 + 门）
  shop:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M4 9.5 2 13h20l-2-3.5z"/><path d="M3 13v7h18v-7"/><path d="M9 20v-5h6v5"/><path d="M2 5h9l1 2h4l1-2h5"/></svg>',
  // 齿轮（设置）
  gear:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.86l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.86-.34 1.7 1.7 0 0 0-1.03 1.56V21a2 2 0 1 1-4 0v-.09A1.7 1.7 0 0 0 8.94 19.4a1.7 1.7 0 0 0-1.86.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.86 1.7 1.7 0 0 0-1.56-1.03H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 8.94a1.7 1.7 0 0 0-.34-1.86l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.86.34H9a1.7 1.7 0 0 0 1-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.86-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.86V9a1.7 1.7 0 0 0 1.56 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1z"/></svg>',
  food:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M4 11h16a8 8 0 0 1-16 0z"/><path d="M9 7c-1 1-1 2 0 3M13 7c-1 1-1 2 0 3M17 7c-1 1-1 2 0 3"/></svg>',
  drink:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M9 3h6l-1 5H10L9 3z"/><path d="M10 8h4l1 13H9l1-13z"/><path d="M8.5 13h7"/></svg>',
  medicine:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/></svg>',
  toy:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M12 4a7 7 0 0 0-7 7v6h14v-6a7 7 0 0 0-7-7z"/><path d="M5 11a7 7 0 0 1 14 0"/><circle cx="9" cy="14" r="1"/><circle cx="15" cy="14" r="1"/><path d="M10 18.5v1M14 18.5v1"/></svg>',
  seed:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M12 21V9"/><path d="M12 12c-3 0-5-1-5-4 3 0 5 1 5 4zM12 12c3 0 5-1 5-4-3 0-5 1-5 4z"/><path d="M12 9V5"/></svg>',
  fert:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M9 3h6M10 3v9l-5 8a1.5 1.5 0 0 0 1.3 2h11.4A1.5 1.5 0 0 0 19 20l-5-8V3"/></svg>',
  pet:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="6" cy="9" r="2"/><circle cx="10" cy="6" r="2"/><circle cx="14" cy="6" r="2"/><circle cx="18" cy="9" r="2"/><path d="M12 11c-4 0-6 3-6 6 0 2 1.3 3 3 3 1 0 1.5-.5 3-.5s2 .5 3 .5c1.7 0 3-1 3-3 0-3-2-6-6-6z"/></svg>',
  coin:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M9.5 9.5h5M9.5 14.5h5"/></svg>',
  bank:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M3 10 12 4l9 6z"/><path d="M5 10v8M9 10v8M15 10v8M19 10v8M3 20h18"/></svg>',
  sign:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="m4 13 5 5L20 7"/><path d="M6 21h12"/></svg>',
  activity:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="3.5" y="5" width="17" height="16" rx="1"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/><path d="M8 13h3M13 13h3M8 17h3"/></svg>',
  rank:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M6 4h12v4a6 6 0 0 1-12 0z"/><path d="M6 6H3v2a3 3 0 0 0 3 3M18 6h3v2a3 3 0 0 1-3 3M12 14v4"/><path d="M8 21h8M9 18h6"/></svg>',
  backpack:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="5" y="7" width="14" height="14" rx="1.5"/><path d="M9 7V5a3 3 0 0 1 6 0v2"/><path d="M5 13h14M9 13v2h6v-2"/></svg>',
  ledger:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="5" y="3" width="14" height="18" rx="1"/><path d="M9 7h6M9 11h6M9 15h3"/></svg>',
  undo:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M4 9h9a5 5 0 0 1 0 10H9"/><path d="M8 5 4 9l4 4"/></svg>',
  debug:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="12" cy="13" r="4"/><path d="M12 9V6M8 10 6 8M16 10l2-2M8 16l-2 2M16 16l2 2"/><path d="M9 3.5 12 5l3-1.5"/></svg>',
  switch:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="3" y="7" width="18" height="10" rx="5"/><circle cx="16" cy="12" r="3"/></svg>',
  data:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3"/></svg>',
  system:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="3" y="4" width="18" height="7" rx="1"/><rect x="3" y="13" width="18" height="7" rx="1"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>',
  link:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M9 15 15 9M10.5 6.5 12 5a4 4 0 0 1 6 6l-1.5 1.5M13.5 17.5 12 19a4 4 0 0 1-6-6l1.5-1.5"/></svg>',
  work:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="3" y="7.5" width="18" height="12"/><path d="M9 7.5V6a3 3 0 0 1 6 0v1.5"/><path d="M3 12.5h18"/></svg>',
  // 左轮手枪：骰子（六面骰，前/顶/右三面 + 三点）
  revolver:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M5 5h14v14H5z"/><path d="M5 5 8 2h14l-3 3"/><path d="M19 5l3-3v14l-3 3"/><rect x="8" y="8" width="1.8" height="1.8" fill="currentColor" stroke="none"/><rect x="11.6" y="11.6" width="1.8" height="1.8" fill="currentColor" stroke="none"/><rect x="15.2" y="15.2" width="1.8" height="1.8" fill="currentColor" stroke="none"/></svg>',
  // 偷菜：小偷面罩（蒙面眼罩 + 双眼洞 + 系带，加高版）
  steal:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><path d="M2 8c4-2 16-2 20 0"/><path d="M2 8c0 4.2 4.2 7 10 7s10-2.8 10-7"/><rect x="6.4" y="8.8" width="4.2" height="3.4"/><rect x="13.4" y="8.8" width="4.2" height="3.4"/><path d="M2 8v5M22 8v5"/></svg>',
  // 用户（2.1.0 运行记录·用户信息）：人形（头 + 肩 + 身体）
  user:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="12" cy="7.5" r="3.5"/><path d="M4 20c0-4 3.6-6 8-6s8 2 8 6"/><path d="M12 21v-4M12 17l-1.6-1.1M12 17l1.6-1.1"/></svg>',
  // 固定结算（2.1.0）：时钟（表盘 + 时针分针）
  clock:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2.5"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2"/></svg>',
  default:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="square" stroke-linejoin="miter"><rect x="4" y="4" width="16" height="16" rx="1"/><path d="M4 9h16M9 4v16"/></svg>',
};

function icon(name) {
  return ICONS[name] || ICONS.default;
}

// ================= 数值表格编辑器（1.7.7：商店/打工/玩耍） =================
const JOB_FIELDS = [
  ["min_level", "最低等级"], ["min_health", "最低健康"], ["min_mood", "最低心情"],
  ["cost_stamina", "消耗体力"], ["cost_satiety", "消耗饱食"], ["cost_thirst", "消耗口渴"],
  ["cost_health", "消耗健康"], ["cost_mood", "消耗心情"],
  ["time", "时间(分钟)"], ["coins", "金币"], ["exp", "经验"],
];
const PLAY_FIELDS = [
  ["min_level", "最低等级"], ["min_health", "最低健康"], ["min_mood", "最低心情"],
  ["cost_stamina", "消耗体力"], ["cost_satiety", "消耗饱食"], ["cost_thirst", "消耗口渴"],
  ["cost_health", "消耗健康"], ["cost_mood", "消耗心情"],
  ["time", "时间(分钟)"], ["exp", "经验"], ["mood", "心情"], ["stamina", "体力"], ["health", "健康"],
];
const SHOP_EFFECT_FIELDS = [
  ["satiety", "饱食度"], ["thirst", "口渴值"], ["stamina", "体力"],
  ["mood", "心情值"], ["health", "健康度"], ["price", "价格(金币)"],
];
const PETSHOP_TYPES = ["食物", "饮料", "药物", "玩具"];
const CROP_FIELDS = [
  ["seed_price", "种子价格"], ["seed_sell_price", "种子卖价"], ["yield", "产量"],
  ["crop_price", "成熟售价"], ["exp", "收获经验"], ["min_level", "需要等级"],
  ["grow_minutes", "成熟(分钟)"],
];
const FERT_FIELDS = [
  ["price", "价格/时"], ["yield_add", "增产%/次"],
  ["max_accel", "可加速次数(-1不限)"],
];
const LOAN_FIELDS = [
  ["max_amount", "最大金额"], ["fav_req", "好感等级"], ["pet_req", "宠物等级"],
  ["farm_req", "农场等级"], ["rate", "日利率%"],
];

// ================= 「先加载后编辑」保护 =================
const loadedTabs = {};
const LOCKED_SECTIONS = {
  petshop: { tables: ["petshop-table"], adds: ["btn-petshop-add"], status: "status-petshop" },
  jobs: { tables: ["jobs-table"], adds: ["btn-jobs-add"], status: "status-jobs" },
  plays: { tables: ["plays-table"], adds: ["btn-plays-add"], status: "status-plays" },
  crops: { tables: ["crops-table"], adds: ["btn-crops-add"], status: "status-crops" },
  ferts: { tables: ["ferts-table"], adds: ["btn-ferts-add"], status: "status-ferts" },
  loanpkgs: { tables: ["loans-table"], adds: ["btn-loans-add"], status: "status-loanpkgs" },
};

function syncLockUI() {
  for (const [key, s] of Object.entries(LOCKED_SECTIONS)) {
    const on = !!loadedTabs[key];
    s.tables.forEach((id) => { const el = $(id); if (el) el.classList.toggle("locked", !on); });
    s.adds.forEach((id) => { const el = $(id); if (el) el.disabled = !on; });
  }
}

function setLoaded(key, ok) {
  loadedTabs[key] = ok;
  syncLockUI();
}

function guardLoaded(key) {
  if (loadedTabs[key]) return true;
  setStatus(LOCKED_SECTIONS[key].status, "⚠️ 请先点击「加载」读取当前数据，加载后才能编辑/保存");
  return false;
}

function numCell(f, v) {
  return `<td><input class="i-num" data-f="${f}" type="number" step="any" value="${esc(v ?? 0)}" /></td>`;
}

function itemRowHtml(fields, item, opts) {
  const nameDesc = opts?.codeMode
    ? `<td class="c-name"><input class="i-num i-code" data-f="code" type="number" min="3" max="10" step="1" value="${esc(item?.code ?? "")}" placeholder="代码" /></td>`
    : `<td class="c-name">
      <input class="i-name" data-f="name" value="${esc(item?.name ?? "")}" placeholder="名称" />
      <input class="i-desc" data-f="desc" value="${esc(item?.desc ?? "")}" placeholder="描述" />
    </td>`;
  const typeCell = opts?.withType
    ? `<td><select class="i-type" data-f="type">${PETSHOP_TYPES.map(
        (t) => `<option value="${t}"${(item?.type || "食物") === t ? " selected" : ""}>${t}</option>`,
      ).join("")}</select></td>`
    : "";
  const nums = fields.map(([f]) => numCell(f, item?.[f])).join("");
  return `<tr>${nameDesc}${typeCell}${nums}<td><button class="row-del danger" title="删除该行">删除</button></td></tr>`;
}

function itemTableHtml(fields, items, opts) {
  const headCells =
    (opts?.codeMode ? `<th class="c-name">代码</th>` : `<th class="c-name">名称 / 描述</th>`) +
    (opts?.withType ? `<th>类型</th>` : "") +
    fields.map(([, label]) => `<th>${label}</th>`).join("") +
    `<th>操作</th>`;
  const colCount = 1 + (opts?.withType ? 1 : 0) + fields.length + 1;
  const bodyRows = (items || []).map((it) => itemRowHtml(fields, it, opts));
  return `<table class="item-table"><thead><tr>${headCells}</tr></thead><tbody>${bodyRows.join("")}</tbody></table>`;
}

function bindRowDelete(container) {
  container.querySelectorAll(".row-del").forEach((b) =>
    b.addEventListener("click", () => b.closest("tr").remove()),
  );
}

function renderItemTable(containerId, fields, items, opts) {
  const box = $(containerId);
  box.innerHTML = itemTableHtml(fields, items, opts);
  bindRowDelete(box);
}

function addItemRow(containerId, fields, opts) {
  // 2.0.2：新增行插入到表格「最上方」方便编辑（而不是追加到末尾）
  let box = $(containerId);
  let tbody = box.querySelector("tbody");
  if (!tbody) {
    renderItemTable(containerId, fields, [], opts);
    box = $(containerId);
    tbody = box.querySelector("tbody");
  }
  const tr = document.createElement("template");
  tr.innerHTML = itemRowHtml(fields, null, opts).trim();
  const row = tr.content.firstElementChild;
  row.querySelector(".row-del").addEventListener("click", () => row.remove());
  tbody.insertBefore(row, tbody.firstChild);
  row.querySelector(".i-name, .i-code")?.focus();
}

function collectItemTable(containerId, fields, opts) {
  // 返回 { items, errors }；客户端校验
  const items = [];
  const errors = [];
  const seen = new Set();
  const rows = document.querySelectorAll(`#${containerId} tbody tr`);
  rows.forEach((tr, idx) => {
    tr.querySelectorAll("input,select").forEach((el) => el.classList.remove("invalid"));
    let item, label;
    if (opts?.codeMode) {
      const codeEl = tr.querySelector('[data-f="code"]');
      if (!codeEl) return;
      const rawCode = codeEl.value.trim();
      const code = rawCode === "" ? NaN : Number(rawCode);
      if (!Number.isInteger(code) || code < 3 || code > 10) {
        errors.push(`第 ${idx + 1} 行：代码必须是 3~10 的整数`);
        codeEl.classList.add("invalid");
        return;
      }
      if (seen.has(code)) {
        errors.push(`代码 ${code} 重复`);
        codeEl.classList.add("invalid");
        return;
      }
      seen.add(code);
      item = { code };
      label = `套餐${code}`;
    } else {
      const nameEl = tr.querySelector('[data-f="name"]');
      if (!nameEl) return;
      const name = nameEl.value.trim();
      const desc = tr.querySelector('[data-f="desc"]').value;
      if (!name) {
        errors.push(`第 ${idx + 1} 行：名称不能为空`);
        nameEl.classList.add("invalid");
        return;
      }
      if (seen.has(name)) {
        errors.push(`「${name}」名称重复`);
        nameEl.classList.add("invalid");
        return;
      }
      seen.add(name);
      item = { name, desc };
      label = `「${name}」`;
    }
    // 2.0.2：宠物商店分类型编辑时，类型由当前编辑器固定传入 opts.forceType
    if (opts?.withType) item.type = tr.querySelector('[data-f="type"]')?.value || opts.forceType;
    else if (opts?.forceType) item.type = opts.forceType;
    let bad = false;
    for (const [f] of fields) {
      const el = tr.querySelector(`[data-f="${f}"]`);
      const raw = el.value.trim();
      const v = raw === "" ? 0 : Number(raw);
      if (raw !== "" && isNaN(v)) {
        errors.push(`${label} 的 ${f} 必须是数字`);
        el.classList.add("invalid");
        bad = true;
        break;
      }
      item[f] = v;
    }
    if (!bad) items.push(item);
  });
  return { items, errors };
}

async function saveItemTables(statusId, endpoint, payload) {
  setStatus(statusId, "保存中...");
  try {
    const resp = await bridge.apiPost(endpoint, payload);
    if (resp && resp.saved === false && resp.errors) {
      setStatus(statusId, "⚠️ 未保存：" + Object.values(resp.errors).join("；"));
      return;
    }
    setStatus(statusId, "✅ 已保存并立即生效");
  } catch (e) {
    setStatus(statusId, "❌ 保存失败：" + e.message);
  }
}

// ================= 2.2.2 后台数据「待保存」状态 =================
// 商店编辑 / 打工玩耍 / 银行贷款 / 功能开关 / 运行参数 / 活动 / 同义口令 编辑页：
// 修改后进入待保存状态，手动保存退出；未保存离开弹窗询问；离开时暂存容灾草稿（config/draft），
// 下次访问弹窗询问是否保存。
const DIRTY_PANELS = {
  "shoptype": () => buildShopPayload(),
  "farm-crops": () => buildCropsPayload(),
  "farm-ferts": () => buildFertsPayload(),
  "config-jobs": () => buildConfigPayload("jobs"),
  "config-plays": () => buildConfigPayload("plays"),
  "loanpkgs": () => buildLoanPkgsPayload(),
  "features": () => buildFeaturesPayload(),
  "params": () => buildParamsPayload(),
  "activities": () => buildActivitiesPayload(),
  "aliases": () => buildAliasesPayload(),
};
const dirtyPanels = new Set();
let _draftTimer = null;
let _bootDraft = null;

function markDirty(panelId) {
  if (!DIRTY_PANELS[panelId] || dirtyPanels.has(panelId)) return;
  dirtyPanels.add(panelId);
  scheduleDraftSave();
}

function buildDirtyPayloads() {
  const out = [];
  dirtyPanels.forEach((panel) => {
    const build = DIRTY_PANELS[panel];
    if (!build) { dirtyPanels.delete(panel); return; }
    const b = build();
    if (b) out.push({ panel, endpoint: b.endpoint, payload: b.payload });
  });
  return out;
}

// 容灾：待保存状态下把当前编辑内容暂存到服务端临时文档（防页面直接关闭丢失）
function scheduleDraftSave() {
  if (!dirtyPanels.size) return;
  clearTimeout(_draftTimer);
  _draftTimer = setTimeout(async () => {
    if (!dirtyPanels.size) return;
    const payloads = {};
    buildDirtyPayloads().forEach((it) => { payloads[it.panel] = { endpoint: it.endpoint, payload: it.payload }; });
    if (!Object.keys(payloads).length) return;
    try { await bridge.apiPost("config/draft", { payloads }); } catch (e) { /* 容灾暂存失败不影响编辑 */ }
  }, 1000);
}

function clearDirtyAll() {
  dirtyPanels.clear();
  bridge.apiPost("config/draft", { clear: true }).catch(() => {});
}

async function saveDirtyAll() {
  const errs = [];
  for (const it of buildDirtyPayloads()) {
    try {
      await bridge.apiPost(it.endpoint, it.payload);
      dirtyPanels.delete(it.panel);
    } catch (e) {
      errs.push(it.panel + "：" + e.message);
    }
  }
  if (!dirtyPanels.size) {
    try { await bridge.apiPost("config/draft", { clear: true }); } catch (e) { /* 忽略 */ }
  }
  return errs;
}

// 未保存修改弹窗（页面内弹窗，非浏览器 confirm）：保存 / 不保存 / 取消（✕）
let _unsavedConfirm = null;
let _unsavedDiscard = null;
function showUnsavedModal(title, text, onConfirm, onDiscard) {
  $("unsaved-title").textContent = title;
  $("unsaved-text").textContent = text;
  _unsavedConfirm = onConfirm || null;
  _unsavedDiscard = onDiscard || null;
  $("unsaved-modal").classList.remove("hidden");
}
function closeUnsavedModal() {
  $("unsaved-modal").classList.add("hidden");
  _unsavedConfirm = null;
  _unsavedDiscard = null;
}

// 离开当前面板前的守卫：有未保存修改 → 弹窗询问
function guardNavAway(action) {
  if (!dirtyPanels.size) { action(); return; }
  showUnsavedModal("⚠️ 有未保存的修改", "当前页面有未保存的修改，离开前是否保存？",
    async () => {
      const errs = await saveDirtyAll();
      closeUnsavedModal();
      if (errs.length) alert("部分修改保存失败：" + errs.join("；"));
      action();
    },
    () => { clearDirtyAll(); closeUnsavedModal(); action(); });
}

// 启动时检查容灾草稿（上次待保存状态下离开时暂存的修改）
async function checkDraftOnBoot() {
  try {
    const r = await bridge.apiGet("config/draft");
    const d = r && r.draft;
    if (!d || !d.payloads || !Object.keys(d.payloads).length) return;
    _bootDraft = d.payloads;
    showUnsavedModal("💾 发现上次未保存的修改",
      "上次编辑于 " + (d.time || "未知时间") + "，离开时未保存已自动暂存。是否保存这些修改？",
      async () => {
        const errs = [];
        for (const it of Object.values(_bootDraft || {})) {
          try { await bridge.apiPost(it.endpoint, it.payload); } catch (e) { errs.push(it.endpoint + "：" + e.message); }
        }
        _bootDraft = null;
        try { await bridge.apiPost("config/draft", { clear: true }); } catch (e) { /* 忽略 */ }
        closeUnsavedModal();
        if (errs.length) alert("部分修改保存失败：" + errs.join("；"));
      },
      () => { _bootDraft = null; try { bridge.apiPost("config/draft", { clear: true }).catch(() => {}); } catch (e) {} closeUnsavedModal(); });
  } catch (e) { /* 局域网未解锁等场景忽略 */ }
}

// ---------- 打工 / 玩耍（2.0.2：拆分为独立编辑页，保存时合并回全量配置） ----------
let configStore = { jobs: [], plays: [] };   // 全量打工/玩耍配置（两类编辑器共用）

async function loadConfig(kind) {
  // kind: "jobs" | "plays"
  const statusId = kind === "jobs" ? "status-jobs" : "status-plays";
  setStatus(statusId, "加载中...");
  try {
    const data = await bridge.apiGet("backend/config");
    configStore = { jobs: (data && data.jobs) || [], plays: (data && data.plays) || [] };
    if (kind === "jobs") renderItemTable("jobs-table", JOB_FIELDS, configStore.jobs);
    else renderItemTable("plays-table", PLAY_FIELDS, configStore.plays);
    setLoaded(kind, true);
    setStatus(statusId, "✅ 已加载");
  } catch (e) {
    setLoaded(kind, false);
    setStatus(statusId, "❌ 加载失败：" + e.message);
  }
}

// 2.2.2：构建待保存数据（校验失败返回 null 并已提示）；保存 = 构建 + 发送
function buildConfigPayload(kind) {
  if (!guardLoaded(kind)) return null;
  if (kind === "jobs") {
    const jobs = collectItemTable("jobs-table", JOB_FIELDS);
    if (jobs.errors.length) {
      setStatus("status-jobs", "❌ " + jobs.errors.map((m) => "打工·" + m).join("；"));
      return null;
    }
    return { endpoint: "backend/config", payload: { jobs: jobs.items, plays: configStore.plays } };
  }
  const plays = collectItemTable("plays-table", PLAY_FIELDS);
  if (plays.errors.length) {
    setStatus("status-plays", "❌ " + plays.errors.map((m) => "玩耍·" + m).join("；"));
    return null;
  }
  return { endpoint: "backend/config", payload: { jobs: configStore.jobs, plays: plays.items } };
}

async function saveConfig(kind) {
  const statusId = kind === "jobs" ? "status-jobs" : "status-plays";
  const b = buildConfigPayload(kind);
  if (!b) return;
  await saveItemTables(statusId, b.endpoint, b.payload);
}

// ---------- 宠物商店（2.0.2：按类型分编辑器，保存时合并回全量列表） ----------
let petShopStore = [];   // 全量商店商品（各类型编辑器共用）
let currentShopType = "食物";

async function loadPetShop(shopType) {
  if (shopType) currentShopType = shopType;
  setStatus("status-petshop", "加载中...");
  try {
    const data = await bridge.apiGet("petshop");
    petShopStore = (data && data.items) || [];
    const filtered = petShopStore.filter((it) => (it.type || "食物") === currentShopType);
    renderItemTable("petshop-table", SHOP_EFFECT_FIELDS, filtered, { forceType: currentShopType });
    const tlabel = $("petshop-type-label");
    if (tlabel) tlabel.textContent = currentShopType;
    setLoaded("petshop", true);
    setStatus("status-petshop", "✅ 已加载");
  } catch (e) {
    setLoaded("petshop", false);
    setStatus("status-petshop", "❌ 加载失败：" + e.message);
  }
}

// 2.2.2：构建待保存数据（校验失败返回 null 并已提示）
function buildShopPayload() {
  if (!guardLoaded("petshop")) return null;
  const shop = collectItemTable("petshop-table", SHOP_EFFECT_FIELDS, { forceType: currentShopType });
  if (shop.errors.length) {
    setStatus("status-petshop", "❌ " + shop.errors.join("；"));
    return null;
  }
  // 用编辑后的该类型条目替换全量列表中的同类型条目
  const others = petShopStore.filter((it) => (it.type || "食物") !== currentShopType);
  return { endpoint: "petshop", payload: { items: [...others, ...shop.items] } };
}

async function savePetShopAll() {
  const b = buildShopPayload();
  if (!b) return;
  await saveItemTables("status-petshop", b.endpoint, b.payload);
}

// ---------- 农场商店：种子 / 化肥 ----------
async function loadCrops() {
  setStatus("status-crops", "加载中...");
  try {
    const data = await bridge.apiGet("farm/crops");
    renderItemTable("crops-table", CROP_FIELDS, (data && data.items) || []);
    setLoaded("crops", true);
    setStatus("status-crops", "✅ 已加载");
  } catch (e) {
    setLoaded("crops", false);
    setStatus("status-crops", "❌ 加载失败：" + e.message);
  }
}

function buildCropsPayload() {
  if (!guardLoaded("crops")) return null;
  const crops = collectItemTable("crops-table", CROP_FIELDS);
  if (crops.errors.length) {
    setStatus("status-crops", "❌ " + crops.errors.join("；"));
    return null;
  }
  return { endpoint: "farm/crops", payload: { items: crops.items } };
}

async function saveCrops() {
  const b = buildCropsPayload();
  if (!b) return;
  await saveItemTables("status-crops", b.endpoint, b.payload);
}

async function loadFerts() {
  setStatus("status-ferts", "加载中...");
  try {
    const data = await bridge.apiGet("farm/ferts");
    renderItemTable("ferts-table", FERT_FIELDS, (data && data.items) || []);
    setLoaded("ferts", true);
    setStatus("status-ferts", "✅ 已加载");
  } catch (e) {
    setLoaded("ferts", false);
    setStatus("status-ferts", "❌ 加载失败：" + e.message);
  }
}

function buildFertsPayload() {
  if (!guardLoaded("ferts")) return null;
  const ferts = collectItemTable("ferts-table", FERT_FIELDS);
  if (ferts.errors.length) {
    setStatus("status-ferts", "❌ " + ferts.errors.join("；"));
    return null;
  }
  return { endpoint: "farm/ferts", payload: { items: ferts.items } };
}

async function saveFerts() {
  const b = buildFertsPayload();
  if (!b) return;
  await saveItemTables("status-ferts", b.endpoint, b.payload);
}

// ---------- 恢复默认道具数据（需验证码确认） ----------
let _captchaCode = "";

function openCaptchaModal() {
  _captchaCode = String(Math.floor(100000 + Math.random() * 900000));
  $("captcha-code").textContent = _captchaCode;
  const input = $("captcha-input");
  input.value = "";
  input.classList.remove("invalid");
  $("captcha-error").classList.add("hidden");
  $("captcha-modal").classList.remove("hidden");
  setTimeout(() => input.focus(), 0);
}

function closeCaptchaModal() {
  $("captcha-modal").classList.add("hidden");
  _captchaCode = "";
}

function applyBenchmark() {
  // 打开验证码弹窗（确认后才真正调用 items/apply_benchmark）
  openCaptchaModal();
}

async function submitCaptcha() {
  const input = $("captcha-input");
  if (input.value.trim() !== _captchaCode) {
    input.classList.add("invalid");
    $("captcha-error").classList.remove("hidden");
    input.select();
    return;
  }
  closeCaptchaModal();
  setStatus("status-benchmark", "应用中...");
  try {
    const r = await bridge.apiPost("items/apply_benchmark", {});
    if (r && r.saved === false && r.errors) {
      setStatus("status-benchmark", "⚠️ 默认数据校验失败：" + Object.values(r.errors).join("；"));
      return;
    }
    setStatus("status-benchmark",
      `✅ 已应用默认道具数据：商品 ${r.shop} / 作物 ${r.crops} / 肥料 ${r.ferts} 条`);
  } catch (e) {
    setStatus("status-benchmark", "❌ 应用失败：" + e.message);
  }
}

// ---------- 贷款套餐 ----------
async function loadLoanPkgs() {
  setStatus("status-loanpkgs", "加载中...");
  try {
    const data = await bridge.apiGet("loan/packages");
    renderItemTable("loans-table", LOAN_FIELDS, (data && data.items) || [], { codeMode: true });
    setLoaded("loanpkgs", true);
    setStatus("status-loanpkgs", "✅ 已加载");
  } catch (e) {
    setLoaded("loanpkgs", false);
    setStatus("status-loanpkgs", "❌ 加载失败：" + e.message);
  }
}

function buildLoanPkgsPayload() {
  if (!guardLoaded("loanpkgs")) return null;
  const loans = collectItemTable("loans-table", LOAN_FIELDS, { codeMode: true });
  if (loans.errors.length) {
    setStatus("status-loanpkgs", "❌ " + loans.errors.join("；"));
    return null;
  }
  return { endpoint: "loan/packages", payload: { items: loans.items } };
}

async function saveLoanPkgs() {
  const b = buildLoanPkgsPayload();
  if (!b) return;
  await saveItemTables("status-loanpkgs", b.endpoint, b.payload);
}

// ---------- 数据导入导出 ----------
async function exportData() {
  setStatus("status-data", "导出中...");
  try {
    const r = await bridge.apiGet("data/export");
    const text =
      r && typeof r.content === "string"
        ? r.content
        : JSON.stringify(r ?? {}, null, 2);
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "signin_backup.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus("status-data", "✅ 已导出全部数据（存档 + 自定义配置）");
  } catch (e) {
    setStatus("status-data", "❌ 导出失败：" + e.message);
  }
}

async function importData() {
  const file = $("import-file").files[0];
  if (!file) {
    setStatus("status-data", "请先选择要导入的 JSON 文件");
    return;
  }
  setStatus("status-data", "导入中...");
  try {
    const content = await file.text();
    const parsed = JSON.parse(content);
    const payload = parsed && parsed.files ? { files: parsed.files } : { content };
    await bridge.apiPost("data/import", payload);
    setStatus("status-data", "✅ 导入成功（存档 + 自定义配置已还原）");
  } catch (e) {
    setStatus("status-data", "❌ 导入失败：" + e.message);
  }
}

// ---------- 功能开关 ----------
async function loadFeatures() {
  setStatus("status-features", "加载中...");
  try {
    const data = await bridge.apiGet("feature/status");
    const modules = (data && data.modules) || [];
    const box = $("features-list");
    if (!modules.length) {
      box.innerHTML = '<p class="hint">没有可配置的功能模块。</p>';
    } else {
      box.innerHTML = modules
        .map(
          (m) => `<label class="param-item feature-item">
            <span class="param-label">${m.label}</span>
            <span class="param-input">
              <input type="checkbox" data-feature="${m.key}" data-label="${m.label}" ${m.enabled ? "checked" : ""} />
            </span>
            <small>关闭后对应指令提示「功能已被管理员关闭」</small>
          </label>`,
        )
        .join("");
    }
    setStatus("status-features", "✅ 已加载");
  } catch (e) {
    setStatus("status-features", "❌ 加载失败：" + e.message);
  }
}

function buildFeaturesPayload() {
  const cbs = document.querySelectorAll("#features-list [data-feature]");
  if (!cbs.length) {
    setStatus("status-features", "❌ 请先点击「加载」读取功能开关");
    return null;
  }
  const switches = {};
  cbs.forEach((cb) => { switches[cb.dataset.feature] = cb.checked; });
  return { endpoint: "feature/status", payload: { switches } };
}

async function saveFeatures() {
  const b = buildFeaturesPayload();
  if (!b) return;
  setStatus("status-features", "保存中...");
  try {
    await bridge.apiPost(b.endpoint, b.payload);
    setStatus("status-features", "✅ 已保存并立即生效");
  } catch (e) {
    setStatus("status-features", "❌ 保存失败：" + e.message);
  }
}

// ---------- 活动中心 ----------
async function loadActivities() {
  setStatus("status-activities", "加载中...");
  try {
    const data = await bridge.apiGet("activities");
    const list = (data && data.activities) || [];
    const box = $("activities-list");
    if (!list.length) {
      box.innerHTML = '<p class="hint">没有已注册的活动模块（请查看 ACTIVITY.md 编写）。</p>';
    } else {
      box.innerHTML = list
        .map((a) => {
          const fields = (a.schema || [])
            .map((f) => {
              const v = a.values[f.field] ?? "";
              const minAttr = f.min != null ? `data-min="${f.min}" ` : "";
              const maxAttr = f.max != null ? `data-max="${f.max}" ` : "";
              const input =
                f.type === "bool"
                  ? `<input type="checkbox" data-aid="${a.id}" data-field="${f.field}" data-type="bool" data-label="${f.label}" ${v ? "checked" : ""} />`
                  : `<input type="text" data-aid="${a.id}" data-field="${f.field}" data-type="${f.type}" data-label="${f.label}" ${minAttr}${maxAttr}value="${v}" />`;
              return `<label class="act-field">
                <span class="act-field-label">${f.label}</span>
                ${input}
                ${f.desc ? `<small>${f.desc}</small>` : ""}
              </label>`;
            })
            .join("");
          return `<div class="activity-card ${a.expired ? "expired" : ""}">
            <label class="activity-head">
              <input type="checkbox" data-aid="${a.id}" data-enable ${a.enabled ? "checked" : ""} />
              <strong>${a.name}</strong>
              ${a.expired ? `<span class="expired-badge">已过期</span>` : ""}
              <em>${a.time_str}</em>
              <small>要求：${a.req_text || ""}</small>
              ${a.commands.length ? `<small>指令：${a.commands.join(" / ")}</small>` : ""}
            </label>
            <div class="act-fields">${fields}</div>
          </div>`;
        })
        .join("");
    }
    setStatus("status-activities", "✅ 已加载");
  } catch (e) {
    setStatus("status-activities", "❌ 加载失败：" + e.message);
  }
}

function buildActivitiesPayload() {
  const enabled = {};
  const configs = {};
  const clientErrors = [];
  document
    .querySelectorAll('#activities-list input[data-enable]')
    .forEach((cb) => (enabled[cb.dataset.aid] = cb.checked));
  document.querySelectorAll("#activities-list [data-field]").forEach((el) => {
    const aid = el.dataset.aid;
    const field = el.dataset.field;
    const type = el.dataset.type;
    const label = el.dataset.label || field;
    el.classList.remove("invalid");
    let v;
    if (type === "bool") {
      v = el.checked;
    } else if (type === "int" || type === "float") {
      const raw = el.value.trim();
      if (raw === "") {
        v = 0;
      } else {
        v = Number(raw);
        if (isNaN(v)) {
          clientErrors.push(`${label} 必须是数字`);
          el.classList.add("invalid");
          return;
        }
        const min = el.dataset.min !== undefined ? Number(el.dataset.min) : null;
        const max = el.dataset.max !== undefined ? Number(el.dataset.max) : null;
        if ((min !== null && v < min) || (max !== null && v > max)) {
          clientErrors.push(`${label} 需在 ${min}~${max} 之间`);
          el.classList.add("invalid");
          return;
        }
      }
    } else if (field === "start" || field === "end") {
      const raw = el.value.trim();
      if (raw !== "" && !/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(raw)) {
        clientErrors.push(`${label} 格式应为 YYYY-MM-DD HH:MM`);
        el.classList.add("invalid");
        return;
      }
      v = raw;
    } else {
      v = el.value;
    }
    (configs[aid] = configs[aid] || {})[field] = v;
  });
  if (clientErrors.length) {
    setStatus("status-activities", "❌ " + clientErrors.join("；"));
    return null;
  }
  if (!Object.keys(enabled).length) {
    setStatus("status-activities", "❌ 请先点击「加载」读取活动配置");
    return null;
  }
  return { endpoint: "activities", payload: { enabled, configs } };
}

async function saveActivities() {
  const b = buildActivitiesPayload();
  if (!b) return;
  setStatus("status-activities", "保存中...");
  try {
    const resp = await bridge.apiPost(b.endpoint, b.payload);
    const errs = (resp && resp.errors) || {};
    const aidKeys = Object.keys(errs);
    if (aidKeys.length) {
      const msgs = [];
      aidKeys.forEach((aid) => {
        Object.entries(errs[aid] || {}).forEach(([field, msg]) => {
          msgs.push(msg);
          const el = document.querySelector(`#activities-list [data-aid="${aid}"][data-field="${field}"]`);
          if (el) el.classList.add("invalid");
        });
      });
      setStatus("status-activities", "⚠️ 部分参数未生效：" + msgs.join("；"));
    } else {
      setStatus("status-activities", "✅ 已保存（参数立即生效）");
    }
  } catch (e) {
    setStatus("status-activities", "❌ 保存失败：" + e.message);
  }
}

// ================= 运行参数（2.0.2：设置子页卡片 → 按子系统过滤的编辑页，选项卡默认展开） =================
let currentParamsMap = {}; // key → 参数对象（表格式编辑需跨参数取当前值，如 FARM_UPGRADE_COSTS）

async function loadParams(group) {
  setStatus("status-params", "加载中...");
  try {
    const data = await bridge.apiGet("params");
    let items = (data && data.params) || [];
    if (group && group !== "__all__") items = items.filter((p) => (p.group || "其他") === group);
    currentParamsMap = {};
    items.forEach((p) => (currentParamsMap[p.key] = p));
    const box = $("params-list");
    if (!items.length) {
      box.innerHTML = '<p class="hint">没有可配置的设置项。</p>';
      setStatus("status-params", "✅ 已加载");
      return;
    }
    // 两级分组：组 → 子组 → 参数（2.0.2：编辑页选项卡默认展开）
    const groups = {};
    items.forEach((p) => {
      const g = p.group || "其他";
      const sg = p.subgroup || "通用";
      (groups[g] = groups[g] || {})[sg] = groups[g][sg] || [];
      groups[g][sg].push(p);
    });
    const renderSubs = (subs) =>
      Object.entries(subs)
        .map(
          ([sgname, arr]) => `<details class="param-subgroup" open>
            <summary>${sgname}（${arr.length}）</summary>
            <div class="param-items">${arr.map(renderParamItem).join("")}</div>
          </details>`,
        )
        .join("");
    const groupEntries = Object.entries(groups);
    const groupFiltered = !!(group && group !== "__all__");
    // 设置编辑页（已按子系统过滤）：解散最外层「组」选项卡，直接展示子选项卡
    box.innerHTML = groupFiltered
      ? groupEntries.map(([, subs]) => `<div class="param-subgroups">${renderSubs(subs)}</div>`).join("")
      : groupEntries
          .map(
            ([gname, subs]) => `<details class="param-group" open>
              <summary>${gname}（${Object.values(subs).flat().length}）</summary>
              <div class="param-subgroups">${renderSubs(subs)}</div>
            </details>`,
          )
          .join("");
    setStatus("status-params", "✅ 已加载");
    // 属性上限表「添加健康值档位」按钮 + 行删除
    const addAttrBtn = $("btn-attr-add-row");
    if (addAttrBtn) addAttrBtn.addEventListener("click", addAttrMaxRow);
    document.querySelectorAll("#params-list .attr-del").forEach((b) =>
      b.addEventListener("click", () => b.closest("tr").remove()),
    );
  } catch (e) {
    setStatus("status-params", "❌ 加载失败：" + e.message);
  }
}

function addAttrMaxRow() {
  // 在属性上限表末尾追加一行（健康值档位可增删）
  const row = document.createElement("template");
  row.innerHTML = `<tr class="attr-row">
    <td><input class="attr-floor" type="number" step="any" value="" placeholder="健康下限" /></td>
    <td><input class="attr-cell" type="number" step="any" data-a="sat" value="0" /></td>
    <td><input class="attr-cell" type="number" step="any" data-a="thr" value="0" /></td>
    <td><input class="attr-cell" type="number" step="any" data-a="sta" value="0" /></td>
    <td><input class="attr-cell" type="number" step="any" data-a="mood" value="0" /></td>
    <td><button type="button" class="attr-del row-del danger" title="删除该档位">删除</button></td>
  </tr>`.trim();
  const tr = row.content.firstElementChild;
  const tbody = document.querySelector("#params-list .attr-row")?.closest("tbody");
  if (tbody) {
    tbody.appendChild(tr);
    tr.querySelector(".attr-del").addEventListener("click", () => tr.remove());
    tr.querySelector(".attr-floor").focus();
  }
}

// ================= 宠物结算范围（2.0.1：固定四档表格编辑） =================
const SETTLE_TIERS = [
  { t: "T1", label: "T1 · 状态最佳档" },
  { t: "T2", label: "T2 · 状态良好档" },
  { t: "T3", label: "T3 · 状态偏低档" },
  { t: "T4", label: "T4 · 状态最差档" },
];
const SETTLE_ATTRS = ["饱食", "口渴", "体力", "心情", "健康"];
const SETTLE_TIER_ATTRS = ["饱食", "口渴", "心情"];
const SETTLE_TIER_COLS = ["一档下限", "二档下限", "三档下限"];

function tierCell(str, attr, idx) {
  const seg = (str || "").split("|").find((s) => {
    const eq = s.indexOf("=");
    return eq >= 0 && s.slice(0, eq).trim() === attr;
  });
  if (!seg) return "";
  const nums = seg
    .slice(seg.indexOf("=") + 1)
    .replace(/，/g, ",")
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
  return nums[idx] != null ? nums[idx] : "";
}

function renderTierTable(p) {
  const rows = SETTLE_TIER_ATTRS.map(
    (a) => `<tr>
      <td class="settle-tier">${a}</td>
      ${SETTLE_TIER_COLS.map(
        (c, i) => `<td><input class="tier-cell" type="number" step="any" data-a="${a}" data-i="${i}"
          value="${esc(tierCell(p.value, a, i))}" placeholder="0" /></td>`,
      ).join("")}
    </tr>`,
  ).join("");
  return `<div class="settle-wrap">
    <table class="item-table settle-table">
      <thead><tr><th>属性</th>${SETTLE_TIER_COLS.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="hint">属性值 ≥ 一档下限 → 1档；≥ 二档下限 → 2档；≥ 三档下限 → 3档；否则 4档。留空则使用默认。</p>
  </div>`;
}

function settleCell(str, tier, attr) {
  const seg = (str || "").split("|").find((s) => {
    const eq = s.indexOf("=");
    return eq >= 0 && s.slice(0, eq).trim().toUpperCase() === tier;
  });
  if (!seg) return "";
  const body = seg.slice(seg.indexOf("=") + 1);
  for (const item of body.split(",")) {
    const tilde = item.indexOf("~");
    if (tilde < 0) continue;
    const left = item.slice(0, tilde);
    const m = left.match(/^(.*?)(-?\d+(?:\.\d+)?)$/);
    if (m && m[1].trim() === attr) {
      return left.slice(m[1].length) + "~" + item.slice(tilde + 1);
    }
  }
  return "";
}

function renderSettleTable(p) {
  const rows = SETTLE_TIERS.map(
    (t) => `<tr data-tier="${t.t}">
      <td class="settle-tier">${t.label}</td>
      ${SETTLE_ATTRS.map(
        (a) => `<td><input class="settle-cell" type="text" data-t="${t.t}" data-a="${a}"
          value="${esc(settleCell(p.value, t.t, a))}" placeholder="${a}" /></td>`,
      ).join("")}
    </tr>`,
  ).join("");
  return `<div class="settle-wrap">
    <table class="item-table settle-table">
      <thead><tr><th>档位</th>${SETTLE_ATTRS.map((a) => `<th>${a}</th>`).join("")}</tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="hint">每格填写该档该属性的「最低~最高」变化值（如 -15~-10）；留空表示该档不变化该属性。</p>
  </div>`;
}

// ================= 土地等级加成（表格编辑，2.0.2） =================
function parseFarmGrades(str) {
  // "贫瘠土地=0,0|红土地=100,0|..." → [{name, yield, time}]（yield/time 为百分比）
  return String(str ?? "")
    .split("|")
    .map((seg) => {
      const eq = seg.indexOf("=");
      if (eq < 0) return null;
      const name = seg.slice(0, eq).trim();
      const nums = seg
        .slice(eq + 1)
        .replace(/，/g, ",")
        .split(",")
        .map((x) => x.trim());
      if (!name || nums.length < 2) return null;
      return { name, yield: nums[0], time: nums[1] };
    })
    .filter(Boolean);
}

function renderFarmGradesTable(p) {
  const rows = parseFarmGrades(p.value);
  const costsP = currentParamsMap["FARM_UPGRADE_COSTS"];
  let costs = Array.isArray(costsP?.value)
    ? costsP.value
    : String(costsP?.value ?? "1000,1500,2000,3000").replace(/，/g, ",").split(",");
  const body = rows
    .map(
      (r, i) => `<tr class="grade-row">
      <td><input class="grade-name" value="${esc(r.name)}" placeholder="等级名" /></td>
      <td><input class="grade-price" type="number" step="any" min="0" value="${i === 0 ? 0 : esc(costs[i - 1] ?? 0)}" ${i === 0 ? "disabled" : ""} /></td>
      <td><input class="grade-yield" type="number" step="any" value="${esc(r.yield)}" /></td>
      <td><input class="grade-time" type="number" step="any" value="${esc(r.time)}" /></td>
    </tr>`,
    )
    .join("");
  return `<div class="settle-wrap">
    <table class="item-table settle-table">
      <thead><tr><th>土地等级</th><th>升级价格</th><th>产量加成(%)</th><th>时间减免(%)</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
    <p class="hint">每一行 = 一个土地等级：升级价格 = 从上一级升到该级的金币（贫瘠为基础，不可改）；产量加成 / 时间减免为百分比（如 100 = +100%）。保存后同步写入 FARM_UPGRADE_COSTS。</p>
  </div>`;
}

// ================= 属性上限·健康值范围（表格编辑，2.0.2） =================
const ATTR_MAX_ATTRS = ["饱食", "口渴", "体力", "心情"];

function parseAttrMax(str) {
  // "140=200,200,200,120|80=..." → [{floor, sat, thr, sta, mood}]
  return String(str ?? "")
    .split("|")
    .map((seg) => {
      const eq = seg.indexOf("=");
      if (eq < 0) return null;
      const floor = seg.slice(0, eq).trim();
      const nums = seg
        .slice(eq + 1)
        .replace(/，/g, ",")
        .split(",")
        .map((x) => x.trim());
      if (floor === "" || nums.length < 4) return null;
      return { floor, sat: nums[0], thr: nums[1], sta: nums[2], mood: nums[3] };
    })
    .filter(Boolean);
}

function renderAttrMaxTable(p) {
  const rows = parseAttrMax(p.value);
  const body = rows
    .map(
      (r) => `<tr class="attr-row">
      <td><input class="attr-floor" type="number" step="any" value="${esc(r.floor)}" /></td>
      <td><input class="attr-cell" type="number" step="any" data-a="sat" value="${esc(r.sat)}" /></td>
      <td><input class="attr-cell" type="number" step="any" data-a="thr" value="${esc(r.thr)}" /></td>
      <td><input class="attr-cell" type="number" step="any" data-a="sta" value="${esc(r.sta)}" /></td>
      <td><input class="attr-cell" type="number" step="any" data-a="mood" value="${esc(r.mood)}" /></td>
      <td><button type="button" class="attr-del row-del danger" title="删除该档位">删除</button></td>
    </tr>`,
    )
    .join("");
  return `<div class="settle-wrap">
    <table class="item-table settle-table">
      <thead><tr><th>健康值下限</th>${ATTR_MAX_ATTRS.map((a) => `<th>${a}上限</th>`).join("")}<th>操作</th></tr></thead>
      <tbody>${body}</tbody>
    </table>
    <p class="hint">健康值 ≥ 某行下限 即按该行上限生效（从高到低匹配）；每格为该属性在对应健康范围内的上限。可增删行调整健康值档位。</p>
    <div class="toolbar tbl-actions"><button type="button" class="add-row" id="btn-attr-add-row">＋ 添加健康值档位</button></div>
  </div>`;
}

function renderParamItem(p) {
  const min = p.min != null ? `min="${p.min}" ` : "";
  const max = p.max != null ? `max="${p.max}" ` : "";
  let displayVal = p.value;
  if (p.type === "list" && Array.isArray(p.value)) {
    displayVal = p.value.join(",");
  }
  if (p.key === "PET_SETTLE_RANGES") {
    return `<div class="param-item param-item-block">
      <span class="param-label">${p.label}</span>
      ${renderSettleTable(p)}
      <small>${p.desc || ""}</small>
    </div>`;
  }
  if (p.key === "PET_SETTLE_TIERS") {
    return `<div class="param-item param-item-block">
      <span class="param-label">${p.label}</span>
      ${renderTierTable(p)}
      <small>${p.desc || ""}</small>
    </div>`;
  }
  // 2.0.2：土地等级加成 → 表格编辑（含升级价格，随保存写入 FARM_UPGRADE_COSTS）
  if (p.key === "FARM_GRADE_BONUSES") {
    return `<div class="param-item param-item-block">
      <span class="param-label">${p.label}</span>
      ${renderFarmGradesTable(p)}
      <small>${p.desc || ""}</small>
    </div>`;
  }
  // 2.0.2：属性上限·健康值范围 → 表格编辑
  if (p.key === "PET_ATTR_MAX_RANGES") {
    return `<div class="param-item param-item-block">
      <span class="param-label">${p.label}</span>
      ${renderAttrMaxTable(p)}
      <small>${p.desc || ""}</small>
    </div>`;
  }
  // 升级费用已并入「土地等级加成」表格，不再单独渲染
  if (p.key === "FARM_UPGRADE_COSTS") return "";
  // 2.2.0：密码类参数不回显当前值，留空 = 保持不变（哈希校验只在插件本地进行）
  if (p.masked) {
    return `<label class="param-item">
      <span class="param-label">${p.label}</span>
      <span class="param-input"><input type="password" data-key="${p.key}" data-type="str" data-masked="1" data-label="${p.label}" value="" placeholder="已设置（留空保持不变）" autocomplete="off" /></span>
      <small>${p.desc || ""}（已设置，不向访问终端回传；输入新值并保存即可修改）</small>
    </label>`;
  }
  const input =
    p.type === "int" || p.type === "float"
      ? `<input type="number" data-key="${p.key}" data-type="${p.type}" data-label="${p.label}" ${min}${max}value="${p.value}" />`
      : p.type === "bool"
        ? `<input type="checkbox" data-key="${p.key}" data-type="bool" data-label="${p.label}" ${p.value ? "checked" : ""} />`
        : `<input type="text" data-key="${p.key}" data-type="str" data-label="${p.label}" value="${displayVal}" />`;
  return `<label class="param-item">
    <span class="param-label">${p.label}</span>
    <span class="param-input">${input}</span>
    <small>${p.desc || ""}${p.min != null ? `（范围 ${p.min}~${p.max ?? "∞"}）` : ""}${p.type === "list" ? "（逗号分隔多个数值）" : ""}</small>
  </label>`;
}

// 2.2.2：构建待保存的运行参数（校验失败返回 null 并已提示）
function buildParamsPayload() {
  const params = {};
  const clientErrors = [];
  document.querySelectorAll("#params-list [data-key]").forEach((el) => {
    const key = el.dataset.key;
    const type = el.dataset.type;
    const label = el.dataset.label || key;
    el.classList.remove("invalid");
    if (type === "bool") {
      params[key] = el.checked;
      return;
    }
    // 2.2.0：密码类参数留空 = 保持不变（不提交，后端保留现有值）
    if (el.dataset.masked === "1" && el.value === "") {
      return;
    }
    if (el.value === "") {
      clientErrors.push(`「${label}」不能为空`);
      el.classList.add("invalid");
      return;
    }
    if (type === "int" || type === "float") {
      const v = Number(el.value);
      if (isNaN(v)) {
        clientErrors.push(`「${label}」必须是数字`);
        el.classList.add("invalid");
        return;
      }
      const min = el.min !== "" ? Number(el.min) : null;
      const max = el.max !== "" ? Number(el.max) : null;
      if ((min !== null && v < min) || (max !== null && v > max)) {
        clientErrors.push(`「${label}」需在 ${min}~${max} 之间`);
        el.classList.add("invalid");
        return;
      }
      params[key] = v;
    } else {
      params[key] = el.value;
    }
  });
  const settleCells = document.querySelectorAll("#params-list .settle-cell");
  if (settleCells.length) {
    const map = {};
    settleCells.forEach((el) => {
      const t = el.dataset.t;
      const a = el.dataset.a;
      const v = el.value.trim();
      if (!v) return;
      if (v.indexOf("~") < 0) {
        clientErrors.push(`「${t}·${a}」需为 最低~最高（如 -15~-10）`);
        el.classList.add("invalid");
        return;
      }
      (map[t] = map[t] || {})[a] = v;
    });
    const parts = [];
    SETTLE_TIERS.forEach(({ t }) => {
      const attrs = map[t] || {};
      const body = SETTLE_ATTRS.map((a) => (attrs[a] ? `${a}${attrs[a]}` : "")).filter(Boolean);
      if (body.length) parts.push(`${t}=${body.join(",")}`);
    });
    if (parts.length || settleCells.length) params["PET_SETTLE_RANGES"] = parts.join("|");
  }
  const tierCells = document.querySelectorAll("#params-list .tier-cell");
  if (tierCells.length) {
    const tmap = {};
    tierCells.forEach((el) => {
      const a = el.dataset.a;
      const v = el.value.trim();
      if (v === "") return;
      if (isNaN(Number(v))) {
        clientErrors.push(`「${a}·${SETTLE_TIER_COLS[Number(el.dataset.i)]}」必须是数字`);
        el.classList.add("invalid");
        return;
      }
      (tmap[a] = tmap[a] || [])[Number(el.dataset.i)] = v;
    });
    const tparts = [];
    SETTLE_TIER_ATTRS.forEach((a) => {
      const nums = tmap[a] || [];
      if (!nums.length) return;
      const body = SETTLE_TIER_COLS.map((_, i) => nums[i] || "").join(",");
      tparts.push(`${a}=${body}`);
    });
    if (tparts.length) params["PET_SETTLE_TIERS"] = tparts.join("|");
  }
  // 2.0.2：土地等级加成表格 → 序列化 FARM_GRADE_BONUSES（百分比）+ FARM_UPGRADE_COSTS（升级价格）
  const gradeRows = document.querySelectorAll("#params-list .grade-row");
  if (gradeRows.length) {
    const gparts = [];
    const gprices = [];
    let gerr = false;
    gradeRows.forEach((tr, idx) => {
      const nameEl = tr.querySelector(".grade-name");
      const name = nameEl.value.trim();
      const priceEl = tr.querySelector(".grade-price");
      const yieldV = Number(tr.querySelector(".grade-yield").value);
      const timeV = Number(tr.querySelector(".grade-time").value);
      if (!name) {
        clientErrors.push(`土地等级表第 ${idx + 1} 行：等级名不能为空`);
        nameEl.classList.add("invalid");
        gerr = true;
        return;
      }
      if (isNaN(yieldV) || isNaN(timeV) || (priceEl.disabled ? false : isNaN(Number(priceEl.value)))) {
        clientErrors.push(`土地等级表第 ${idx + 1} 行：请输入数字`);
        gerr = true;
        return;
      }
      gparts.push(`${name}=${yieldV},${timeV}`);
      if (!priceEl.disabled) gprices.push(Number(priceEl.value));
    });
    if (!gerr) {
      params["FARM_GRADE_BONUSES"] = gparts.join("|");
      params["FARM_UPGRADE_COSTS"] = gprices;
    }
  }
  // 2.0.2：属性上限·健康值范围表格 → 序列化
  const attrRows = document.querySelectorAll("#params-list .attr-row");
  if (attrRows.length) {
    const aparts = [];
    let aerr = false;
    attrRows.forEach((tr, idx) => {
      const floorEl = tr.querySelector(".attr-floor");
      const floor = floorEl.value.trim();
      const vals = ["sat", "thr", "sta", "mood"].map((a) => {
        const el = tr.querySelector(`.attr-cell[data-a="${a}"]`);
        const v = el.value.trim();
        if (v === "" || isNaN(Number(v))) {
          clientErrors.push(`属性上限表第 ${idx + 1} 行：请输入数字`);
          el.classList.add("invalid");
          aerr = true;
        }
        return v;
      });
      if (floor === "" || isNaN(Number(floor))) {
        clientErrors.push(`属性上限表第 ${idx + 1} 行：健康值下限必须是数字`);
        floorEl.classList.add("invalid");
        aerr = true;
      }
      if (!aerr) aparts.push(`${floor}=${vals.join(",")}`);
    });
    if (!aerr && aparts.length) params["PET_ATTR_MAX_RANGES"] = aparts.join("|");
  }
  if (clientErrors.length) {
    setStatus("status-params", "❌ " + clientErrors.join("；"));
    return null;
  }
  return { endpoint: "params", payload: { params } };
}

async function saveParams() {
  const b = buildParamsPayload();
  if (!b) return;
  setStatus("status-params", "保存中...");
  try {
    const resp = await bridge.apiPost(b.endpoint, b.payload);
    const errs = (resp && resp.errors) || {};
    const keys = Object.keys(errs);
    if (keys.length) {
      keys.forEach((k) => {
        const el = document.querySelector(`#params-list [data-key="${k}"]`);
        if (el) el.classList.add("invalid");
      });
      setStatus("status-params", "⚠️ 部分参数未生效：" + Object.values(errs).join("；"));
    } else {
      setStatus("status-params", "✅ 已保存并立即生效");
    }
  } catch (e) {
    setStatus("status-params", "❌ 保存失败：" + e.message);
  }
}

// ================= 同义口令 =================
let aliasHeads = [];

function aliasOptions(selected) {
  return aliasHeads
    .map((h) => `<option value="${h}"${h === selected ? " selected" : ""}>${h}</option>`)
    .join("");
}

function aliasRow(key, val) {
  return `<div class="alias-row">
    <input class="alias-key" value="${key}" placeholder="同义词，如：打卡" />
    <select class="alias-val">${aliasOptions(val)}</select>
    <button data-alias-del class="danger">删除</button>
  </div>`;
}

function renderAliasRows(aliases) {
  const box = $("aliases-list");
  const entries = Object.entries(aliases || {});
  const rows = entries.map(([k, v]) => aliasRow(k, v)).join("");
  box.innerHTML =
    `<div class="alias-row alias-head"><span>同义词（发送它效果相同）</span><span>目标指令</span><span></span></div>` +
    rows +
    `<div class="alias-row">
      <input id="alias-new-key" placeholder="输入新同义词，如：打卡" />
      <select id="alias-new-val">${aliasOptions("")}</select>
      <button id="btn-alias-add" class="primary">添加</button>
    </div>`;
  box.querySelectorAll("button[data-alias-del]").forEach((b) =>
    b.addEventListener("click", () => b.closest(".alias-row").remove()),
  );
  $("btn-alias-add").addEventListener("click", addAliasRow);
}

function addAliasRow() {
  const key = $("alias-new-key").value.trim();
  const val = $("alias-new-val").value;
  if (!key) {
    alert("请输入同义词");
    return;
  }
  const box = $("aliases-list");
  const row = document.createElement("div");
  row.className = "alias-row";
  row.innerHTML = aliasRow(key, val);
  row.querySelector("button[data-alias-del]").addEventListener("click", () => row.remove());
  box.insertBefore(row, $("alias-new-key").parentElement);
  $("alias-new-key").value = "";
}

async function loadAliases() {
  setStatus("status-aliases", "加载中...");
  try {
    const data = await bridge.apiGet("alias/list");
    aliasHeads = (data && data.heads) || [];
    renderAliasRows((data && data.aliases) || {});
    setStatus("status-aliases", "✅ 已加载");
  } catch (e) {
    setStatus("status-aliases", "❌ 加载失败：" + e.message);
  }
}

function buildAliasesPayload() {
  const rows = Array.from(document.querySelectorAll("#aliases-list .alias-row"));
  if (!rows.length || !document.querySelector("#aliases-list .alias-head")) {
    setStatus("status-aliases", "❌ 请先点击「加载」读取同义口令（若一直加载失败请重载插件）");
    return null;
  }
  const aliases = {};
  for (const row of rows) {
    const keyEl = row.querySelector(".alias-key");
    if (!keyEl) continue;
    const key = keyEl.value.trim();
    const val = row.querySelector(".alias-val").value;
    if (!key) continue;
    aliases[key] = val;
  }
  return { endpoint: "alias/save", payload: { aliases } };
}

async function saveAliases() {
  const b = buildAliasesPayload();
  if (!b) return;
  setStatus("status-aliases", "保存中...");
  try {
    await bridge.apiPost(b.endpoint, b.payload);
    setStatus("status-aliases", "✅ 已保存并立即生效");
    loadAliases();
  } catch (e) {
    setStatus("status-aliases", "❌ 保存失败：" + e.message);
  }
}

async function syncGroupNames() {
  setStatus("status-names", "同步中，请稍候...");
  try {
    const r = await bridge.apiPost("group/names/sync", {});
    if (r && r.ok) {
      setStatus("status-names", `✅ ${r.msg}`);
    } else {
      setStatus("status-names", "❌ " + ((r && r.msg) || "同步失败"));
    }
  } catch (e) {
    setStatus("status-names", "❌ 同步失败：" + e.message);
  }
}

// ================= 运行记录（2.0.4）：宠物记录 / 商店价格 =================
function tierLabel(tier) {
  return tier ? "T" + tier : "-";
}

function fmtNum(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "0";
  return (Math.round(n * 10) / 10).toString();
}

function attrCell(label, val, maxv, red) {
  const pct = maxv > 0 ? Math.min(100, Math.max(0, (val / maxv) * 100)) : 0;
  return `<span class="rec-attr${red ? " red" : ""}" title="${esc(label)} ${fmtNum(val)}/${fmtNum(maxv)}">
    <span class="rec-attr-label">${esc(label)}</span>
    <span class="rec-attr-bar"><i style="width:${pct}%"></i></span>
    <span class="rec-attr-num">${fmtNum(val)}/${fmtNum(maxv)}</span>
  </span>`;
}

function recordPetCard(p) {
  const a = p.attrs || {};
  const auto = p.auto || {};
  const busy = p.busy;
  const attrRows = [
    attrCell("饱食", a.sat, a.sat_max, a.sat_red),
    attrCell("口渴", a.thr, a.thr_max, a.thr_red),
    attrCell("体力", a.sta, a.sta_max, false),
    attrCell("心情", a.mood, a.mood_max, a.mood_red),
    attrCell("健康", a.health, a.health_max, a.health_red),
  ].join("");
  const feedLogs = (auto.feed_logs || []).slice().reverse().map((lg) => {
    const items = (lg.items || [])
      .map((it) => (it.src === "购买" ? "🛒" : "📦") + esc(it.name) + "×" + it.qty)
      .join("、");
    const loanTxt = lg.loan > 0 ? `（自动化贷款 ${lg.loan}）` : "";
    return `<div class="rec-line">${esc(lg.ts || lg.date || "")}（${esc(lg.trigger || "档位触发")}）${items ? "：" + items : ""}${lg.total ? `（共 ${lg.total} 金币${loanTxt}）` : ""}</div>`;
  }).join("") || '<div class="rec-line muted">暂无购买记录</div>';
  const workLogs = (auto.work_logs || []).slice().reverse().map((lg) =>
    `<div class="rec-line">${esc(lg.ts || lg.date || "")}：自动打工「${esc(lg.job)}」+${lg.coins} 金币${(lg.exp && Number(lg.exp) > 0) ? ` +${lg.exp} 经验` : ""}，基准 ${lg.base_before} → ${lg.base_after}</div>`,
  ).join("") || '<div class="rec-line muted">暂无打工记录</div>';
  // 可点击开关芯片（2.0.4：运行记录页直接控制每个用户的自动购买/自动打工）：
  // data-cur = 用户当前开关（0/1）；点击后向 records/pets/auto 切换（芯片 HTML 直接硬编码于下方）
  const purchaseOn = !!auto.purchase_on;
  const workOn = !!auto.work_on;
  const purchaseHint = auto.purchase_global ? "自动购买（点击切换开/关）" : "自动购买 · ⚠️ 总开关未开启（设置 → 宠物 → 自动购买）";
  const workHint = !auto.purchase_on
    ? "自动打工 · 需先开启自动购买（点击无效）"
    : (auto.work_global ? "自动打工（点击切换开/关；只给金币不给经验）" : "自动打工 · 总开关未开启（设置 → 宠物 → 自动打工）");
  return `<div class="rec-card pet-card" data-uid="${esc(p.uid)}">
    <div class="rec-card-head">
      <span class="rec-card-name">${esc(p.name || "宠物")}</span>
      <span class="rec-card-nick">${esc(p.nick || p.uid)}</span>
      <span class="rec-card-tag">Lv.${fmtNum(p.level)} ${tierLabel(p.tier)}</span>
      ${p.weak ? '<span class="rec-chip danger-chip">停用</span>' : ""}
    </div>
    <div class="rec-attrs">${attrRows}</div>
    <div class="rec-row">活动：${busy ? `${esc(busy.activity)}「${esc(busy.item)}」剩 ${busy.remaining_min} 分钟` : (p.weak ? "虚弱 · 自动购买/自动打工已暂停" : "空闲")}</div>
    <div class="rec-row">
      <span class="rec-row-label">自动购买</span>
      <button type="button" class="rec-chip${purchaseOn ? " on" : ""} toggle" data-auto="purchase" data-uid="${esc(p.uid)}" data-cur="${purchaseOn ? 1 : 0}" data-allow="1" title="${esc(purchaseHint)}">${purchaseOn ? "开" : "关"}</button>
      <span class="rec-row-label">自动打工</span>
      <button type="button" class="rec-chip${workOn ? " on" : ""} toggle" data-auto="work" data-uid="${esc(p.uid)}" data-cur="${workOn ? 1 : 0}" data-allow="${(workOn || purchaseOn) ? 1 : 0}" title="${esc(workHint)}">${workOn ? "开" : "关"}</button>
      <span class="rec-chip gold">基准 ${fmtNum(auto.work_base)}</span>
      ${auto.auto_loan_owed > 0 ? `<span class="rec-chip danger-chip" title="自动化专属贷款（获得金币自动优先还款；未还清前宠物持续自动打工）">自动化贷款 欠 ${fmtNum(auto.auto_loan_owed)}</span>` : ""}
    </div>
    <div class="rec-block"><div class="rec-block-title">购买 / 使用记录（×N 数量标记）</div>${feedLogs}</div>
    <div class="rec-block"><div class="rec-block-title">自动打工记录</div>${workLogs}</div>
    <div class="rec-row muted"><span class="rec-chip toggle-expand">点击卡片查看宠物详情 ▸</span></div>
  </div>`;
}

// 运行记录页：切换某用户的自动购买/自动打工（点击卡片上的「开/关」芯片）
async function toggleRecordAuto(uid, key, curOn) {
  setStatus("status-record-pets", "切换中...");
  try {
    const r = await bridge.apiPost("records/pets/auto", { uid, key, on: !curOn });
    if (r && r.ok) {
      setStatus("status-record-pets", `✅ ${r.msg}`);
      loadRecordPets(); // 刷新列表，显示最新开关状态
    } else {
      setStatus("status-record-pets", "❌ " + ((r && r.msg) || "切换失败"));
    }
  } catch (e) {
    setStatus("status-record-pets", "❌ 切换失败：" + e.message);
  }
}

async function loadRecordPets() {
  setStatus("status-record-pets", "加载中...");
  const box = $("records-pets-list");
  try {
    const r = await bridge.apiGet("records/pets");
    const pets = (r && r.pets) || [];
    recordPetsCache = pets;
    renderRecordPets();
    setStatus("status-record-pets", `✅ 共 ${pets.length} 只宠物（点击开/关可切换）`);
  } catch (e) {
    box.innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    setStatus("status-record-pets", "❌ 加载失败");
  }
}

let recordPetsCache = [];

// 2.2.2：宠物记录卡片（搜索 + 状态筛选 + 档位筛选 + 排序，全部前端本地完成）
function renderRecordPets() {
  const box = $("records-pets-list");
  const kw = ($("record-pets-search")?.value || "").trim().toLowerCase();
  const st = $("record-pets-status")?.value || "";
  const tier = $("record-pets-tier")?.value || "";
  const sort = $("record-pets-sort")?.value || "default";
  let list = recordPetsCache.slice();
  if (kw) {
    list = list.filter((p) =>
      String(p.name || "").toLowerCase().includes(kw) ||
      String(p.nick || "").toLowerCase().includes(kw) ||
      String(p.uid || "").toLowerCase().includes(kw));
  }
  if (st === "active") list = list.filter((p) => !p.weak);
  else if (st === "weak") list = list.filter((p) => !!p.weak);
  if (tier) list = list.filter((p) => String(p.tier || "") === tier);
  if (sort === "tier_asc") list.sort((a, b) => (a.tier || 5) - (b.tier || 5));
  else if (sort === "tier_desc") list.sort((a, b) => (b.tier || 0) - (a.tier || 0));
  else if (sort === "level_desc") list.sort((a, b) => (b.level || 0) - (a.level || 0));
  else if (sort === "level_asc") list.sort((a, b) => (a.level || 0) - (b.level || 0));
  if (!list.length) {
    box.innerHTML = kw
      ? '<p class="hint">没有匹配「' + esc($("record-pets-search").value) + '」的宠物。</p>'
      : '<p class="hint">当前筛选条件下暂无宠物（可以切换 状态 / 档位 筛选）。</p>';
    return;
  }
  box.innerHTML = `<div class="rec-grid cols-2">${list.map(recordPetCard).join("")}</div>`;
  if (!box._autoToggleHandler) {
    box._autoToggleHandler = (e) => {
      const btn = e.target.closest(".rec-chip[data-auto]");
      if (!btn) return;
      const key = btn.dataset.auto;
      const curOn = Number(btn.dataset.cur) === 1;
      // 未允许开启（如未开启自动购买、总开关关闭）：提示而不是发起将被后端拒绝的请求
      if (!curOn && Number(btn.dataset.allow || 0) !== 1) {
        setStatus("status-record-pets", "⚠️ " + (btn.title || "当前不允许开启"));
        return;
      }
      toggleRecordAuto(btn.dataset.uid, key, curOn);
    };
    box.addEventListener("click", box._autoToggleHandler);
  }
  if (!box._petDetailHandler) {
    // 2.2.1：点击宠物卡片（非开关芯片）→ 进入宠物详情页
    box._petDetailHandler = (e) => {
      const card = e.target.closest(".rec-card.pet-card");
      if (!card) return;
      if (e.target.closest(".rec-chip[data-auto]")) return; // 开关芯片不触发详情
      openPanel({ id: "record-pet-detail", title: "运行记录 · 宠物详情", params: { uid: card.dataset.uid } });
    };
    box.addEventListener("click", box._petDetailHandler);
  }
}

// ================= 运行记录：宠物详情（2.2.2：点击宠物记录卡片进入；模块化展示宠物详细信息 + 按时间排序的折叠属性变化记录） =================
async function loadRecordPetDetail(uid) {
  const title = $("record-pet-detail-title");
  const box = $("record-pet-detail-box");
  if (!uid) {
    box.innerHTML = '<p class="hint">缺少宠物用户ID，无法加载详情。</p>';
    return;
  }
  setStatus("status-record-pet-detail", "加载中...");
  box.innerHTML = '<p class="hint">加载宠物详情...</p>';
  try {
    const r = await bridge.apiPost("records/pets/detail", { uid });
    const d = (r && r.pet) || null;
    if (!d) {
      box.innerHTML = '<p class="hint">未找到该宠物（可能已解绑或数据不存在）。</p>';
      setStatus("status-record-pet-detail", "❌ 无数据");
      return;
    }
    if (title) title.textContent = "🐾 " + (d.name || "宠物") + "（" + (d.nick || d.uid) + "）";
    renderRecordPetDetail(d);
    setStatus("status-record-pet-detail", "✅ 属性变化记录共 " + ((d.attr_log || []).length) + " 条");
  } catch (e) {
    box.innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    setStatus("status-record-pet-detail", "❌ 加载失败");
  }
}

// 2.2.2：属性变化明细（正=绿，负=红，按固定顺序；exp 显示为「经验」）
const ATTR_CHG_ORDER = ["satiety", "thirst", "stamina", "mood", "health", "exp"];

function attrLabel(k, labels) {
  return (labels && labels[k]) || (k === "exp" ? "经验" : k);
}

function attrChangeHtml(changes, labels) {
  const parts = [];
  for (const k of ATTR_CHG_ORDER) {
    const v = Number(changes[k]);
    if (!Number.isFinite(v) || Math.abs(v) < 0.005) continue;
    const color = v > 0 ? "var(--success)" : "var(--danger)";
    parts.push('<span style="color:' + color + ';font-weight:600">' + esc(attrLabel(k, labels)) + (v > 0 ? "+" : "") + fmtNum(v) + "</span>");
  }
  return parts.length ? parts.join(" ") : '<span class="muted">无属性变化</span>';
}

// 2.2.2：属性变化记录 = 按操作时间排序的单列折叠列表（默认折叠，展开显示属性条可视化）
// 属性条 = 血条式：轨道 0→上限；稳定段（标准主色）0→较小值（增加时=变动前、减少时=当前值），
// 其后接淡绿增长区（升高）或淡红减少区（降低）。
// 特殊情况：无变动前快照（after）的旧记录 → 展开显示「无数据」。
function attrBarsHtml(lg, labels, maxOf) {
  const after = lg.after;
  if (!after || typeof after !== "object") {
    return '<div class="attr-bars"><span class="muted">无数据</span></div>';
  }
  const rows = [];
  for (const k of ATTR_CHG_ORDER) {
    if (k === "exp") continue; // 经验无固定上限，只在文字记录中展示
    const v = Number((lg.changes || {})[k]);
    if (!Number.isFinite(v) || Math.abs(v) < 0.005) continue;
    const scale = Number(lg.max && lg.max[k]) > 0 ? Number(lg.max[k])
      : (Number(maxOf[k]) > 0 ? Number(maxOf[k]) : 100); // 2.2.2：优先用变动发生时的上限快照
    const cls = v > 0 ? "pos" : "neg";
    const a = Number(after[k]);
    if (!Number.isFinite(a)) continue;
    const p0 = Math.min(100, Math.max(0, ((a - v) / scale) * 100)); // 变动前
    const p1 = Math.min(100, Math.max(0, (a / scale) * 100));       // 变动后
    const base = Math.min(p0, p1); // 血条式稳定段：0 → 较小值
    rows.push('<div class="attr-bar-row">'
      + '<span class="attr-bar-label">' + esc(attrLabel(k, labels)) + "</span>"
      + '<span class="attr-bar-track"><i class="base" style="width:' + base.toFixed(1) + '%"></i>'
      + '<i class="delta ' + cls + '" style="left:' + base.toFixed(1) + '%;width:' + Math.max(Math.abs(p1 - p0), 1.2).toFixed(1) + '%"></i></span>'
      + '<span class="attr-bar-num ' + cls + '">' + (v > 0 ? "+" : "") + fmtNum(v) + "</span>"
      + "</div>");
  }
  return rows.length ? '<div class="attr-bars">' + rows.join("") + "</div>"
    : '<div class="attr-bars"><span class="muted">无属性条（仅经验/其他变动，见上方文字）</span></div>';
}

function attrLogHtml(logs, labels, maxOf) {
  if (!logs || !logs.length) return '<p class="hint">暂无属性变化记录（从记录机制启用后开始统计）。</p>';
  // 2.2.2：按操作时间排序，新变动置于顶部、最旧的沉底
  const list = logs.slice().sort((a, b) => (Number(b.ts) || 0) - (Number(a.ts) || 0));
  return list.map((lg) => {
    let line = '<span class="muted">' + esc(lg.time || "") + "</span> " + esc(lg.behavior || "");
    line += "：" + attrChangeHtml(lg.changes || {}, labels);
    if (lg.extra) line += ' <span class="muted">（' + esc(lg.extra) + "）</span>";
    return '<details class="attr-log-item"><summary>' + line + "</summary>"
      + attrBarsHtml(lg, labels, maxOf) + "</details>";
  }).join("");
}

function renderRecordPetDetail(d) {
  const a = d.attrs || {};
  const auto = d.auto || {};
  const busy = d.busy;
  const labels = d.attr_labels || {};
  const attrRows = [
    attrCell("饱食", a.sat, a.sat_max, a.sat_red),
    attrCell("口渴", a.thr, a.thr_max, a.thr_red),
    attrCell("体力", a.sta, a.sta_max, false),
    attrCell("心情", a.mood, a.mood_max, a.mood_red),
    attrCell("健康", a.health, a.health_max, a.health_red),
  ].join("");

  // ── 基本档案 ──
  const expPct = d.exp_need > 0 ? Math.min(100, Math.max(0, (d.exp_got / d.exp_need) * 100)) : 100;
  const profileCard = `<div class="detail-card">
    <div class="detail-card-title">📋 基本档案</div>
    <div class="rec-line"><b>${esc(d.name || "宠物")}</b>（${esc(d.nick || d.uid)}）</div>
    <div class="rec-line">等级 Lv.${fmtNum(d.level)} · 档位 ${tierLabel(d.tier)}${d.weak ? ' · <span style="color:var(--danger)">虚弱停用</span>' : ""}</div>
    <div class="rec-line">经验 ${fmtNum(d.exp)}（本级 ${fmtNum(d.exp_got)}/${fmtNum(d.exp_need)}）</div>
    <div class="rec-attr-bar" style="display:block;height:8px"><i style="width:${expPct}%"></i></div>
    <div class="rec-line">看家：${d.guard ? '<span style="color:var(--success)">开</span>' : "关"}</div>
    <div class="rec-line muted">最近结算日：${esc(d.last_settle_date || "-")}</div>
  </div>`;

  // ── 当前状态（2.2.2：不再显示 最近结算） ──
  const statusCard = `<div class="detail-card">
    <div class="detail-card-title">🐾 当前状态</div>
    <div class="rec-attrs">${attrRows}</div>
    <div class="rec-line">活动：${busy ? `正在${esc(busy.activity)}「${esc(busy.item)}」剩 ${busy.remaining_min} 分钟` : (d.weak ? "虚弱 · 自动购买/自动打工已暂停" : "空闲")}</div>
  </div>`;

  // ── 自动化信息（2.2.2：整行加宽，横跨上方三张卡片 + 两个间隙） ──
  const feedLogs = (auto.feed_logs || []).slice().reverse().map((lg) => {
    const items = (lg.items || []).map((it) => (it.src === "购买" ? "🛒" : "📦") + esc(it.name) + "×" + it.qty).join("、");
    const loanTxt = lg.loan > 0 ? "（自动化贷款 " + lg.loan + "）" : "";
    return '<div class="rec-line">' + esc(lg.ts || lg.date || "") + "（" + esc(lg.trigger || "档位触发") + "）" + (items ? "：" + items : "") + (lg.total ? "（共 " + lg.total + " 金币" + loanTxt + "）" : "") + "</div>";
  }).join("") || '<div class="rec-line muted">暂无购买记录</div>';
  const workLogs = (auto.work_logs || []).slice().reverse().map((lg) =>
    '<div class="rec-line">' + esc(lg.ts || lg.date || "") + "：自动打工「" + esc(lg.job) + "」+" + lg.coins + " 金币" + ((lg.exp && Number(lg.exp) > 0) ? " +" + lg.exp + " 经验" : "") + "，基准 " + lg.base_before + " → " + lg.base_after + "</div>",
  ).join("") || '<div class="rec-line muted">暂无打工记录</div>';
  const autoCard = `<div class="detail-card span-all">
    <div class="detail-card-title">⚙️ 自动化</div>
    <div class="rec-line">自动购买：${auto.purchase_on ? '<span style="color:var(--success)">✅ 开</span>' : "关"}｜自动打工：${auto.work_on ? '<span style="color:var(--success)">✅ 开</span>' : "关"}</div>
    <div class="rec-line">打工基准金币：${fmtNum(auto.work_base)}</div>
    ${auto.auto_loan_owed > 0 ? '<div class="rec-line" style="color:var(--danger)">自动化贷款：欠 ' + fmtNum(auto.auto_loan_owed) + " 金币</div>" : ""}
    <div class="rec-block"><div class="rec-block-title">购买 / 使用记录（×N）</div>${feedLogs}</div>
    <div class="rec-block"><div class="rec-block-title">自动打工记录</div>${workLogs}</div>
  </div>`;

  // ── 特殊记录 + 仓库 ──
  const pill = d.pill || {};
  const me = d.money_event || {};
  const bagChips = (d.bag || []).slice(0, 10).map((b) => '<span class="bag-chip">' + esc(b.name) + " ×" + b.qty + "</span>").join("");
  const bagMore = (d.bag || []).length > 10 ? '<span class="bag-chip muted">…共 ' + (d.bag || []).length + " 种</span>" : "";
  const miscCard = `<div class="detail-card">
    <div class="detail-card-title">📊 特殊记录</div>
    <div class="rec-line">属性丸（今日）：${fmtNum(pill.used)}/${fmtNum(pill.limit)}${pill.today ? "（" + esc(pill.today) + "）" : ""}</div>
    <div class="rec-line">捡钱事件（今日）：${fmtNum(me.count)}/${fmtNum(me.max)}</div>
    <div class="rec-block"><div class="rec-block-title">📦 仓库道具</div><div class="rec-row bag-row">${bagChips || '<span class="rec-line muted">仓库为空</span>'}${bagMore}</div></div>
  </div>`;

  // 2.2.2：属性条轨道上限（各属性 0→max，来自当前状态）
  const maxOf = { satiety: a.sat_max, thirst: a.thr_max, stamina: a.sta_max, mood: a.mood_max, health: a.health_max };
  const logsHtml = attrLogHtml(d.attr_log || [], labels, maxOf);

  const box = $("record-pet-detail-box");
  // 2.2.2：模块顺序 = 基本档案 / 当前状态 / 特殊记录（一行三卡），自动化整行加宽置于其下
  box.innerHTML = `<div class="detail-grid pet-detail-grid">${profileCard}${statusCard}${miscCard}${autoCard}</div>
    <div class="rec-card">
      <div class="rec-card-head">
        <span class="rec-card-name">📜 属性变化记录</span>
        <span class="rec-card-nick">按操作时间排序（新变动在前，最多保留 300 条）· 点击条目展开属性条</span>
        <span class="rec-card-tag gold">共 ${fmtNum((d.attr_log || []).length)} 条</span>
      </div>
      ${logsHtml}
    </div>`;
}
async function loadRecordPrices() {
  setStatus("status-record-prices", "加载中...");
  const box = $("records-prices-list");
  try {
    const r = await bridge.apiGet("records/prices");
    if (!r || !r.enabled) {
      box.innerHTML = '<p class="hint">商店价格浮动未开启：请在「设置 → 商店 → 宠物商店」打开「宠物商店价格浮动开关」后查看价格变动。当前所有商品按原价出售。</p>';
      setStatus("status-record-prices", "未开启");
      return;
    }
    const priceHint = $("price-discount-range");
    if (priceHint) priceHint.textContent = "2~5";
    const disc = r.discount || null;
    let html = "";
    if (r.special && (r.current || []).length) {
      html += `<div class="rec-card"><div class="rec-card-head"><span class="rec-card-name">当前窗口 ${esc(r.window)}</span><span class="rec-card-tag gold">特价时段</span></div>`;
      html += (r.current || []).filter((it) => it.price !== it.base).map((it) =>
        `<div class="rec-line">${esc(it.name)}：${it.base} 金币 → <b>${it.price} 金币</b>（${(it.mult * 10).toFixed(1)} 折）</div>`).join("");
      html += `</div>`;
    }
    const recs = (r.records || []).slice().reverse();
    html += recs.map((rec) => {
      const items = (rec.items || []).map((it) =>
        `<div class="rec-line">${esc(it.name)}：${it.base} 金币 → <b>${it.price} 金币</b>（${(it.mult * 10).toFixed(1)} 折）</div>`).join("");
      return `<div class="rec-card">
        <div class="rec-card-head"><span class="rec-card-name">窗口 ${esc(rec.window)}</span>
        ${rec.ts ? `<span class="rec-card-nick">${esc(rec.ts)}</span>` : ""}
        <span class="rec-card-tag gold">${rec.items && rec.items.length ? rec.items.length + " 件打折" : "原价"}</span></div>
        ${items || '<div class="rec-line muted">该窗口无打折商品</div>'}
      </div>`;
    }).join("");
    if (!html) {
      html = '<p class="hint">暂无价格变动记录（插件刚开启价格浮动或还没有到达特价时段）。</p>';
    }
    box.innerHTML = `<div class="rec-grid">${html}</div>`;
    setStatus("status-record-prices", `✅ ${(recs || []).length} 条窗口记录`);
  } catch (e) {
    box.innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    setStatus("status-record-prices", "❌ 加载失败");
  }
}

// ================= 运行记录：用户信息（2.1.0） =================
let recordUsersCache = [];
let recordUsersSort = "last_active";   // 2.1.0 排序自定义
let recordUsersAsc = false;            // false=降序（默认）

const SORT_LABELS = {
  nick_pinyin: "昵称首拼", nick_stroke: "昵称首字笔画", last_active: "最后活跃",
  pet_level: "宠物等级", farm_level: "农场等级", fav_level: "好感度等级",
};

// 2.2.0：用户完整详情按需拉取（展开卡片时才请求 records/users/detail，缓存避免重复请求）
let recordUsersDetailCache = {};  // uid → 完整详情

// 详情独立附属卡片（2.1.0：每个模块一个卡片）——由按需拉取的详情数据渲染
function recordUserDetailGrid(d) {
  const pet = d.pet || null;
  const farm = d.farm || null;
  const bank = d.bank || null;
  const auto = d.auto || {};
  const weak = !!(pet && pet.weak);
  const bagRows = (d.bag || []).slice(0, 12).map((b) =>
    `<span class="bag-chip">${esc(b.name)} ×${b.qty}</span>`).join("");
  const bagMore = (d.bag || []).length > 12 ? `<span class="bag-chip muted">…共 ${(d.bag || []).length} 种</span>` : "";
  const bagCard = `<div class="detail-card">
    <div class="detail-card-title">📦 仓库（${(d.bag || []).length} 种）</div>
    <div class="rec-row bag-row">${bagRows || '<span class="rec-line muted">仓库为空</span>'}${bagMore}</div>
  </div>`;
  const farmCard = `<div class="detail-card">
    <div class="detail-card-title">🌾 农场实时状态${farm ? `（Lv.${fmtNum(farm.level)}，经验 ${fmtNum(farm.exp)}）` : ""}</div>
    ${farm
      ? (farm.plots && farm.plots.length
          ? farm.plots.map((p) =>
              `<div class="rec-line">#${p.no} ${p.crop ? `「${esc(p.crop)}」${p.mature ? "· 已成熟" : `· 剩 ${p.remain_min} 分钟`}` : "空地"}</div>`).join("")
          : '<div class="rec-line muted">农场无土地</div>')
      : '<div class="rec-line muted">未开通农场</div>'}
  </div>`;
  const petCard = `<div class="detail-card">
    <div class="detail-card-title">🐾 宠物状态</div>
    ${pet
      ? `<div class="rec-line">${esc(pet.name)} Lv.${fmtNum(pet.level)}${weak ? " · 😷虚弱（停用）" : ""}（经验 ${fmtNum(pet.exp)}）</div>`
        + `<div class="rec-line">活动：${pet.busy_activity ? `${esc(pet.busy_activity)}「${esc(pet.busy_item)}」` : "空闲"}</div>`
        + `<div class="rec-line muted">好感度：${fmtNum(d.fav)}（等级 ${d.fav_level}）｜金币：${fmtNum(d.coins)}</div>`
      : '<div class="rec-line muted">未领养宠物</div>'}
  </div>`;
  const bankCard = `<div class="detail-card">
    <div class="detail-card-title">🏦 银行${bank ? `（${bank.total_count} 笔）` : ""}</div>
    ${bank
      ? `<div class="rec-line">锁定 ${bank.locked_count} 笔 ${fmtNum(bank.locked_sum)} 金币｜可取（已成熟）${bank.matured_count} 笔 ${fmtNum(bank.matured_sum)} 金币</div>`
        + `<div class="rec-line">预计利息合计：${fmtNum(bank.interest_sum)} 金币</div>`
        + (bank.deposits && bank.deposits.length
            ? bank.deposits.map((dep) =>
                `<div class="rec-line">${esc(dep.deposit_time || "")} ${dep.amount} 金币（${fmtNum(dep.base_rate + dep.bonus_rate)}%/时 × ${dep.hours}h）${dep.status === "matured" ? "· 已解锁" : "· 锁定中"}</div>`).join("")
            : "")
      : '<div class="rec-line muted">银行无存款</div>'}
  </div>`;
  const autoCard = `<div class="detail-card">
    <div class="detail-card-title">⚙️ 自动化</div>
    <div class="rec-line">自动购买：${auto.purchase_on ? "✅ 开" : "关"}｜自动打工：${auto.work_on ? "✅ 开" : "关"}</div>
    <div class="rec-line">打工基准金币：${fmtNum(auto.work_base)}</div>
    ${auto.auto_loan_owed > 0 ? `<div class="rec-line" style="color:var(--danger)">自动化贷款：欠 ${fmtNum(auto.auto_loan_owed)} 金币（获得金币自动优先还款）</div>` : ""}
  </div>`;
  return `<div class="detail-grid">${bagCard}${farmCard}${petCard}${bankCard}${autoCard}</div>`;
}

async function ensureUserDetail(card, uid) {
  const box = card.querySelector(".user-detail");
  if (!box) return;
  if (box.dataset.state === "loaded") return;
  if (recordUsersDetailCache[uid]) {
    box.innerHTML = recordUserDetailGrid(recordUsersDetailCache[uid]);
    box.dataset.state = "loaded";
    return;
  }
  if (box.dataset.state === "loading") return;
  box.dataset.state = "loading";
  box.innerHTML = '<p class="hint">详情加载中...</p>';
  try {
    const r = await bridge.apiPost("records/users/detail", { uid });
    const d = (r && r.user) || null;
    recordUsersDetailCache[uid] = d;
    if (d) {
      box.innerHTML = recordUserDetailGrid(d);
      box.dataset.state = "loaded";
    } else if (r && r.error) {
      box.innerHTML = '<p class="hint">详情读取失败：' + esc(r.error) + "</p>";
      box.dataset.state = "error";
    } else {
      box.innerHTML = '<p class="hint">该用户暂无更多数据。</p>';
      box.dataset.state = "loaded";
    }
  } catch (e) {
    box.innerHTML = '<p class="hint">详情加载失败：' + esc(e.message) + "</p>";
    box.dataset.state = "error";
  }
}

function recordUserCard(u) {
  const pet = u.pet || null;
  const farm = u.farm || null;
  const bank = u.bank || null;
  const weak = !!(pet && pet.weak);
  // 2.2.0：卡片只使用列表接口返回的基础字段；完整详情（仓库/地块/存单等）展开时按需拉取
  const bankTotal = bank ? fmtNum((bank.locked_sum || 0) + (bank.matured_sum || 0)) : null;
  return `<div class="rec-card user-card" data-uid="${esc(u.uid)}">
    <div class="rec-card-head">
      <span class="rec-card-name">${esc(u.nick || u.uid)}</span>
      <span class="rec-card-nick">${u.nick_source === "account" ? "账户昵称" : esc(u.uid)}</span>
      <span class="rec-card-tag gold">好感 Lv.${fmtNum(u.fav_level)}</span>
      ${weak ? '<span class="rec-chip danger-chip">停用</span>' : ""}
    </div>
    <div class="rec-row">
      <span class="rec-chip gold">💰 ${fmtNum(u.coins)}</span>
      <span class="rec-chip">宠物 ${pet ? `Lv.${fmtNum(pet.level)}` : "未领养"}</span>
      <span class="rec-chip">农场 ${farm ? `Lv.${fmtNum(farm.level)}` : "未开通"}</span>
      <span class="rec-chip">银行 ${bankTotal != null ? `${bankTotal}金` : "未使用"}</span>
    </div>
    <div class="rec-row muted">最后活跃：${esc(u.last_active_text || "-")} <span class="rec-chip toggle-expand">点击展开详情 ▾</span></div>
    <div class="user-detail"><p class="hint">展开后加载详情</p></div>
    <div class="user-card-actions">
      <button class="btn-user-detail primary" data-uid-detail="${esc(u.uid)}">查看详情</button>
    </div>
  </div>`;
}

// 2.1.0：展开/收起用户详细信息（可同时展开多个卡片，不互斥）；
// 展开后的详情以网格流布局撑开，被展开卡片拉出的空白让同行卡片自然平移到侧边/下方，互不遮挡；同行卡片本身不展开。
// 排序键比较（支持数组成员逐项比较：sort_pinyin / sort_stroke 是 [类别, 子键, 兜底] 数组）
function cmpSortKeys(a, b) {
  const na = Array.isArray(a) ? a.length : 0;
  const nb = Array.isArray(b) ? b.length : 0;
  const n = Math.max(na, nb);
  for (let i = 0; i < n; i++) {
    const x = na > i ? a[i] : "";
    const y = nb > i ? b[i] : "";
    if (typeof x === "number" && typeof y === "number") {
      if (x !== y) return x - y;
    } else {
      const sx = String(x ?? "").toLowerCase();
      const sy = String(y ?? "").toLowerCase();
      if (sx < sy) return -1;
      if (sx > sy) return 1;
    }
  }
  return 0;
}

function getSortKey(u) {
  switch (recordUsersSort) {
    case "nick_pinyin": return u.sort_pinyin || [];
    case "nick_stroke": return u.sort_stroke || [];
    case "pet_level": return [u.pet ? u.pet.level : -1];
    case "farm_level": return [u.farm ? u.farm.level : -1];
    case "fav_level": return [u.fav_level];
    default: return [u.last_active || 0];
  }
}

// 2.2.2 排序修复：两类「无数据」键不随升降序翻转位置——
// ① 未领养宠物 / 未开通农场（键 [-1]）不是最低等级，升序降序都排在最后；
// ② 中文昵称的拼音/笔画未收录（服务端子键 ""/0）按文档应排在已收录字之后（组内偏后），升降序一致。
function userCardMissingKey(k) {
  return Array.isArray(k) && k.length === 1 && Number(k[0]) === -1;
}

function userCardUnknownHanKey(k) {
  return Array.isArray(k) && k.length >= 2 && k[0] === 0 && (k[1] === "" || Number(k[1]) === 0);
}

function cmpUserCards(x, y) {
  const ka = getSortKey(x), kb = getSortKey(y);
  const ma = userCardMissingKey(ka), mb = userCardMissingKey(kb);
  if (ma !== mb) return ma ? 1 : -1; // 无数据键：升降序都排最后（不参与方向翻转）
  const ua = userCardUnknownHanKey(ka), ub = userCardUnknownHanKey(kb);
  if (ua !== ub) return ua ? 1 : -1; // 未收录中文：组内偏后（不参与方向翻转）
  const c = cmpSortKeys(ka, kb);
  return recordUsersAsc ? c : -c; // 正常键才随升降序翻转
}

function renderRecordUsers() {
  const box = $("records-users-list");
  const kw = ($("record-users-search")?.value || "").trim().toLowerCase();
  let list = recordUsersCache.slice();
  // 本地排序（2.1.0：六种方式 + 升降序，无需重新请求；2.2.2 修复无数据键的位置）
  list.sort(cmpUserCards);
  if (kw) {
    list = list.filter((u) =>
      String(u.nick || "").toLowerCase().includes(kw) ||
      String(u.uid || "").toLowerCase().includes(kw));
  }
  if (!list.length) {
    box.innerHTML = kw
      ? '<p class="hint">没有匹配「' + esc($("record-users-search").value) + '」的用户。</p>'
      : '<p class="hint">暂无用户数据。</p>';
    return;
  }
  // 保留当前已展开的 uid 集合，刷新后保持展开状态
  const expandedSet = new Set(
    Array.from(box.querySelectorAll(".user-card.expanded")).map((c) => c.dataset.uid),
  );
  box.innerHTML = `<div class="rec-grid cols-2 user-grid">${list.map(recordUserCard).join("")}</div>`;
  box.querySelectorAll(".user-card").forEach((card) => {
    const uid = card.dataset.uid;
    if (expandedSet.has(uid)) {
      card.classList.add("expanded");
      // 2.2.0：重新渲染后为保持展开的卡片恢复详情（缓存命中则不重新请求）
      ensureUserDetail(card, uid);
    }
  });
  if (!box._userDetailHandler) {
    box._userDetailHandler = (e) => {
      // 2.2.2：展开卡片里的「查看详情」→ 进入用户详情子页（仅运行记录·用户信息页提供）
      const detailBtn = e.target.closest(".btn-user-detail");
      if (detailBtn) {
        openPanel({ id: "record-user-detail", title: "运行记录 · 用户详情", params: { uid: detailBtn.getAttribute("data-uid-detail") } });
        return;
      }
      const card = e.target.closest(".user-card");
      if (!card) return;
      // 阻止点开关芯片时触发详情展开
      if (e.target.closest(".rec-chip.toggle")) return;
      card.classList.toggle("expanded");
      // 2.2.0：展开时按需拉取该用户完整详情（缓存复用，不重复请求）
      if (card.classList.contains("expanded")) {
        ensureUserDetail(card, card.dataset.uid);
      }
    };
    box.addEventListener("click", box._userDetailHandler);
  }
}

async function loadRecordUsers() {
  setStatus("status-record-users", "加载中...");
  try {
    const r = await bridge.apiGet("records/users");
    const users = (r && r.users) || [];
    recordUsersCache = users;
    renderRecordUsers();
    const label = SORT_LABELS[recordUsersSort] || recordUsersSort;
    setStatus("status-record-users", `✅ 共 ${users.length} 名用户（${label} ${recordUsersAsc ? "升序" : "降序"}；点击卡片查看详情，可同时展开多个）`);
  } catch (e) {
    $("records-users-list").innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    setStatus("status-record-users", "❌ 加载失败");
  }
}

// 2.2.2：用户详情子页（运行记录·用户信息卡片展开后点「查看详情」进入；仅该页提供入口）
async function loadRecordUserDetail(uid) {
  setStatus("status-record-user-detail", "加载中...");
  const box = $("record-user-detail-box");
  try {
    const r = await bridge.apiPost("records/users/detail", { uid });
    const d = (r && r.user) || null;
    if (!d) {
      box.innerHTML = '<p class="hint">该用户暂无数据。</p>';
      setStatus("status-record-user-detail", r && r.error ? "❌ " + r.error : "无数据");
      return;
    }
    const headCard = `<div class="detail-grid" style="margin-bottom:12px">
      <div class="detail-card"><div class="detail-card-title">👤 基本信息</div>
        <div class="rec-line"><b>${esc(d.nick || uid)}</b>（${esc(d.uid || uid)}）</div>
        <div class="rec-line">💰 金币 ${fmtNum(d.coins)} · 好感 Lv.${fmtNum(d.fav_level)}（${fmtNum(d.fav)}）</div>
        <div class="rec-line">🐾 宠物 ${d.pet ? `Lv.${fmtNum(d.pet.level)}` : "未领养"} · 🌾 农场 ${d.farm ? `Lv.${fmtNum(d.farm.level)}` : "未开通"}</div>
      </div></div>`;
    box.innerHTML = headCard + recordUserDetailGrid(d);
    $("record-user-detail-title").textContent = (d.nick || uid) + " 的详情";
    setStatus("status-record-user-detail", "✅ 已加载");
  } catch (e) {
    box.innerHTML = '<p class="hint">加载失败：' + esc(e.message) + "</p>";
    setStatus("status-record-user-detail", "❌ 加载失败");
  }
}

// ================= 调试模式 =================
async function loadDebugStatus() {
  const bar = $("debug-bar");
  if (!bar) return;
  try {
    const s = await bridge.apiGet("debug/status");
    if (!s || !s.unlocked) {
      bar.classList.add("hidden");
      bar.innerHTML = "";
      return;
    }
    bar.classList.remove("hidden");
    bar.innerHTML = s.enabled
      ? `<span class="debug-on">🛠️ 调试模式已开启：所有人拥有无限资源，数据不写入磁盘，退出后自动恢复</span>
         <button id="btn-debug-toggle" class="danger">退出调试模式</button>`
      : `<span>🔓 已解锁调试模式</span>
         <button id="btn-debug-toggle" class="primary">开启调试模式</button>`;
    $("btn-debug-toggle").addEventListener("click", toggleDebug);
  } catch (e) {
    bar.classList.add("hidden");
  }
}

async function toggleDebug() {
  try {
    await bridge.apiPost("debug/toggle");
    await loadDebugStatus();
  } catch (e) {
    alert("操作失败：" + e.message);
  }
}

// ================= 导航（2.0.2：返回首页 / 返回上一级 + 子页面卡片） =================
// navStack 记录首页之后的视图；每项 {kind:'subpage'|'panel', id, title, params}
const navStack = [];

const SUBPAGE_TITLES = { shop: "商店编辑", settings: "设置", config: "打工玩耍", records: "运行记录" };

// 设置子页 → 子系统卡片（badge/icon/title/tag/group 用于过滤运行参数）
const SETTINGS_CARDS = [
  { badge: "签到", icon: "sign", title: "签到", tag: "奖励", group: "签到" },
  { badge: "农场", icon: "seed", title: "农场", tag: "种植", group: "农场" },
  { badge: "宠物", icon: "pet", title: "宠物", tag: "养成", group: "宠物" },
  { badge: "偷菜", icon: "steal", title: "偷菜", tag: "机制", group: "偷菜" },
  { badge: "贷款", icon: "bank", title: "贷款", tag: "利率", group: "贷款" },
  { badge: "红包", icon: "coin", title: "金币红包", tag: "红包", group: "金币红包" },
  { badge: "左轮", icon: "revolver", title: "左轮手枪", tag: "游戏", group: "左轮手枪" },
  { badge: "排行榜", icon: "rank", title: "排行榜", tag: "展示", group: "排行榜" },
  { badge: "商店", icon: "shop", title: "商店", tag: "商品", group: "商店" },
  { badge: "背包", icon: "backpack", title: "背包", tag: "道具", group: "背包" },
  { badge: "账单", icon: "ledger", title: "金币账单", tag: "流水", group: "金币账单" },
  { badge: "固定结算", icon: "clock", title: "固定结算", tag: "定时", group: "固定结算" },
  { badge: "撤回", icon: "undo", title: "撤回设置", tag: "消息", group: "撤回设置" },
  { badge: "调试", icon: "debug", title: "调试", tag: "调试", group: "调试" },
  // 功能开关 / 数据导入导出 / 系统（局域网·群昵称）
  { badge: "开关", icon: "switch", title: "功能开关", tag: "开关", panel: "features" },
  { badge: "数据", icon: "data", title: "数据导入导出", tag: "备份", panel: "data" },
  { badge: "系统", icon: "system", title: "系统设置", tag: "局域网", panel: "system" },
];

// 商店子页 → 六类独立按钮
const SHOP_CARDS = [
  { badge: "宠物商店", icon: "food", title: "食物", tag: "宠物", type: "食物" },
  { badge: "宠物商店", icon: "drink", title: "饮料", tag: "宠物", type: "饮料" },
  { badge: "宠物商店", icon: "medicine", title: "药物", tag: "宠物", type: "药物" },
  { badge: "宠物商店", icon: "toy", title: "玩具", tag: "宠物", type: "玩具" },
  { badge: "农场商店", icon: "seed", title: "种子", tag: "农场", sub: "crops" },
  { badge: "农场商店", icon: "fert", title: "化肥", tag: "农场", sub: "ferts" },
];

// 打工玩耍子页 → 打工 / 玩耍 独立按钮（2.0.2：按商店编辑模式拆分）
const CONFIG_CARDS = [
  { badge: "打工玩耍", icon: "work", title: "打工", tag: "项目", sub: "jobs" },
  { badge: "打工玩耍", icon: "toy", title: "玩耍", tag: "项目", sub: "plays" },
];

// 运行记录子页 → 宠物记录 / 用户信息 / 商店价格（2.0.4/2.1.0）
const RECORDS_CARDS = [
  { badge: "运行记录", icon: "pet", title: "宠物记录", tag: "宠物", sub: "pets" },
  { badge: "运行记录", icon: "user", title: "用户信息", tag: "用户", sub: "users" },
  { badge: "运行记录", icon: "shop", title: "商店价格", tag: "价格", sub: "prices" },
];

// 子页面卡片：深浅两态背景扫描互换（图标居中不动、仅变色）+ 底部图标/标题（2.0.2：无徽章无标签）
function subCard(c) {
  return `<button class="sub-card" data-card="${esc(c.key ?? "")}">
    <div class="sub-card-art">
      <div class="sub-art-sweep"></div>
      <div class="sub-art-icon">${icon(c.icon)}</div>
    </div>
    <div class="sub-card-foot">
      <span class="sub-foot-icon">${icon(c.icon)}</span>
      <span class="sub-foot-title">${esc(c.title)}</span>
    </div>
  </button>`;
}

function renderSubpage(id) {
  const wrap = $("subpage-view");
  // 不同子页面使用不同配色主题（浅↔深两态颜色互换）
  wrap.classList.remove("theme-shop", "theme-settings");
  wrap.classList.add(id === "shop" ? "theme-shop" : "theme-settings");
  if (id === "shop") {
    wrap.innerHTML = `<div class="sub-cards cols-3">${SHOP_CARDS.map((c, i) => subCard({ ...c, key: "shop-" + i })).join("")}</div>`;
    wrap.querySelectorAll(".sub-card").forEach((btn) => {
      const idx = Number(btn.dataset.card.split("-")[1]);
      const c = SHOP_CARDS[idx];
      btn.addEventListener("click", () => {
        if (c.type) openPanel({ id: "shoptype", title: `商店编辑 · ${c.title}`, params: { shopType: c.type } });
        else if (c.sub === "crops") openPanel({ id: "farm-crops", title: "农场商店 · 种子" });
        else if (c.sub === "ferts") openPanel({ id: "farm-ferts", title: "农场商店 · 化肥" });
      });
    });
  } else if (id === "config") {
    // 打工玩耍子页：打工 / 玩耍 两个独立按钮（与商店编辑同构）
    wrap.innerHTML = `<div class="sub-cards cols-3">${CONFIG_CARDS.map((c, i) => subCard({ ...c, key: "cfg-" + i })).join("")}</div>`;
    wrap.querySelectorAll(".sub-card").forEach((btn) => {
      const idx = Number(btn.dataset.card.split("-")[1]);
      const c = CONFIG_CARDS[idx];
      btn.addEventListener("click", () => {
        if (c.sub === "jobs") openPanel({ id: "config-jobs", title: "打工项目" });
        else openPanel({ id: "config-plays", title: "玩耍项目" });
      });
    });
  } else if (id === "settings") {
    // 设置子页：无右上角 tag、无顶部徽章，每行 5 个卡片
    wrap.innerHTML = `<div class="sub-cards cols-5">${SETTINGS_CARDS.map((c, i) => subCard({ ...c, key: "set-" + i })).join("")}</div>`;
    wrap.querySelectorAll(".sub-card").forEach((btn) => {
      const idx = Number(btn.dataset.card.split("-")[1]);
      const c = SETTINGS_CARDS[idx];
      btn.addEventListener("click", () => {
        if (c.panel === "features") openPanel({ id: "features", title: "功能开关" });
        else if (c.panel === "data") openPanel({ id: "data", title: "数据导入导出" });
        else if (c.panel === "system") openPanel({ id: "system", title: "系统设置" });
        else openPanel({ id: "params", title: `设置 · ${c.title}`, params: { group: c.group } });
      });
    });
  } else if (id === "records") {
    // 运行记录子页：宠物记录 / 用户信息 / 商店价格 三个按钮
    wrap.innerHTML = `<div class="sub-cards cols-3">${RECORDS_CARDS.map((c, i) => subCard({ ...c, key: "rec-" + i })).join("")}</div>`;
    wrap.querySelectorAll(".sub-card").forEach((btn) => {
      const idx = Number(btn.dataset.card.split("-")[1]);
      const c = RECORDS_CARDS[idx];
      btn.addEventListener("click", () => {
        if (c.sub === "pets") openPanel({ id: "records-pets", title: "运行记录 · 宠物记录" });
        else if (c.sub === "users") openPanel({ id: "records-users", title: "运行记录 · 用户信息" });
        else openPanel({ id: "records-prices", title: "运行记录 · 商店价格" });
      });
    });
  }
}

function currentView() {
  return navStack.length ? navStack[navStack.length - 1] : { kind: "home" };
}

function updateNavButtons() {
  const depth = navStack.length;
  $("btn-back-home").classList.toggle("hidden", depth === 0);
  // 深度=1 时「返回上一级」与「返回首页」效果相同 → 只显示返回首页
  $("btn-back-level").classList.toggle("hidden", depth < 2);
}

function getPanelTitle(v) {
  if (v.kind === "subpage") return SUBPAGE_TITLES[v.id] || "";
  return v.title || "";
}

function renderView() {
  const cur = currentView();
  document.querySelectorAll(".panel").forEach((p) => p.classList.add("hidden"));
  $("home-view").classList.add("hidden");
  $("subpage-view").classList.add("hidden");
  $("nav-bar").classList.toggle("hidden", cur.kind === "home");
  $("panel-title").textContent = getPanelTitle(cur);
  if (cur.kind === "home") {
    $("home-view").classList.remove("hidden");
  } else if (cur.kind === "subpage") {
    renderSubpage(cur.id);
    $("subpage-view").classList.remove("hidden");
  } else {
    const panel = $("panel-" + cur.id);
    if (panel) panel.classList.remove("hidden");
  }
  updateNavButtons();
  window.scrollTo(0, 0);
}

function loadCurrent() {
  const cur = currentView();
  if (cur.kind !== "panel") return;
  if (cur.id === "shoptype") loadPetShop(cur.params?.shopType || "食物");
  else if (cur.id === "farm-crops") loadCrops();
  else if (cur.id === "farm-ferts") loadFerts();
  else if (cur.id === "config-jobs") loadConfig("jobs");
  else if (cur.id === "config-plays") loadConfig("plays");
  else if (cur.id === "loanpkgs") loadLoanPkgs();
  else if (cur.id === "features") loadFeatures();
  else if (cur.id === "params") loadParams(cur.params?.group || "__all__");
  else if (cur.id === "system") {
    lanLoadSettings();
  } else if (cur.id === "activities") loadActivities();
  else if (cur.id === "aliases") loadAliases();
  else if (cur.id === "records-pets") loadRecordPets();
  else if (cur.id === "record-pet-detail") loadRecordPetDetail(cur.params?.uid);
  else if (cur.id === "records-users") loadRecordUsers();
  else if (cur.id === "record-user-detail") loadRecordUserDetail(cur.params?.uid);
  else if (cur.id === "records-prices") loadRecordPrices();
  else if (cur.id === "data") loadHistoryPanel(); // 2.2.2：历史数据保留/回溯列表
  // data 面板其余为导出/导入操作，无需自动加载
}

function openSubpage(id) {
  navStack.push({ kind: "subpage", id });
  renderView();
}

function openPanel(entry) {
  navStack.push({ kind: "panel", ...entry });
  renderView();
  loadCurrent();
}

function goBack() {
  // 2.2.2：有待保存修改时先弹窗询问
  guardNavAway(() => {
    if (navStack.length > 1) {
      navStack.pop();
      renderView();
      loadCurrent();
    } else {
      goHome();
    }
  });
}

function goHome() {
  guardNavAway(() => {
    navStack.length = 0;
    renderView();
  });
}

function openFeature(name) {
  // 从首页进入某个管理页（兼容旧调用）
  const map = {
    shop: () => openSubpage("shop"),
    settings: () => openSubpage("settings"),
    config: () => openSubpage("config"),
    loanpkgs: () => openPanel({ id: "loanpkgs", title: "贷款套餐" }),
    features: () => openPanel({ id: "features", title: "功能开关" }),
    params: () => openPanel({ id: "params", title: "设置" }),
    activities: () => openPanel({ id: "activities", title: "活动中心" }),
    aliases: () => openPanel({ id: "aliases", title: "同义口令" }),
    data: () => openPanel({ id: "data", title: "数据导入导出" }),
    records: () => openSubpage("records"),
  };
  (map[name] || (() => openSubpage(name)))();
}

// 首页卡片事件
document.querySelectorAll(".card[data-feature]").forEach((c) =>
  c.addEventListener("click", () => openFeature(c.dataset.feature)),
);
$("btn-back-home").addEventListener("click", goHome);
$("btn-back-level").addEventListener("click", goBack);

// 初始：显示首页卡片导航（所有面板隐藏，只有首页）
goHome();

// ================= Ctrl + 鼠标滚轮 = 左右滚动表格/页面（2.0.2） =================
// 按住 Ctrl 滚动滚轮时，把垂直滚轮位移转换为水平滚动：
// 优先滚动鼠标悬停处可横向滚动的容器（宽表格），没有则滚动整个页面（替代浏览器缩放）
document.addEventListener(
  "wheel",
  (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const factor = 1.6;
    const delta = e.deltaY * factor + (e.deltaX || 0);
    // 从鼠标所在元素向上找第一个可横向滚动的容器（scrollWidth > clientWidth 且 overflow-x 可滚动）
    let scroller = null;
    let node = e.target instanceof Element ? e.target : document.body;
    while (node && node !== document.documentElement) {
      if (node.scrollWidth > node.clientWidth + 1) {
        const cs = getComputedStyle(node);
        if (/(auto|scroll)/.test(cs.overflowX)) {
          scroller = node;
          break;
        }
      }
      node = node.parentElement;
    }
    if (scroller) {
      scroller.scrollLeft += delta;
    } else {
      window.scrollBy({ left: delta, top: 0, behavior: "auto" });
    }
  },
  { passive: false },
);

// ================= 数据编辑区绑定 =================
$("btn-load-jobs").addEventListener("click", () => loadConfig("jobs"));
$("btn-save-jobs").addEventListener("click", () => saveConfig("jobs"));
$("btn-load-plays").addEventListener("click", () => loadConfig("plays"));
$("btn-save-plays").addEventListener("click", () => saveConfig("plays"));
$("btn-jobs-add").addEventListener("click", () => {
  if (guardLoaded("jobs")) addItemRow("jobs-table", JOB_FIELDS);
});
$("btn-plays-add").addEventListener("click", () => {
  if (guardLoaded("plays")) addItemRow("plays-table", PLAY_FIELDS);
});
$("btn-load-petshop").addEventListener("click", () => loadPetShop(currentShopType));
$("btn-save-petshop").addEventListener("click", savePetShopAll);
$("btn-petshop-add").addEventListener("click", () => {
  if (guardLoaded("petshop")) addItemRow("petshop-table", SHOP_EFFECT_FIELDS, { forceType: currentShopType });
});
$("btn-crops-add").addEventListener("click", () => {
  if (guardLoaded("crops")) addItemRow("crops-table", CROP_FIELDS);
});
$("btn-ferts-add").addEventListener("click", () => {
  if (guardLoaded("ferts")) addItemRow("ferts-table", FERT_FIELDS);
});
$("btn-loans-add").addEventListener("click", () => {
  if (guardLoaded("loanpkgs")) addItemRow("loans-table", LOAN_FIELDS, { codeMode: true });
});
$("btn-apply-benchmark").addEventListener("click", applyBenchmark);
$("btn-captcha-ok").addEventListener("click", submitCaptcha);
$("btn-captcha-cancel").addEventListener("click", closeCaptchaModal);
$("captcha-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") submitCaptcha();
  if (e.key === "Escape") closeCaptchaModal();
});
$("captcha-modal").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeCaptchaModal();
});
$("btn-load-crops").addEventListener("click", loadCrops);
$("btn-save-crops").addEventListener("click", saveCrops);
$("btn-load-ferts").addEventListener("click", loadFerts);
$("btn-save-ferts").addEventListener("click", saveFerts);
$("btn-load-loanpkgs").addEventListener("click", loadLoanPkgs);
$("btn-save-loanpkgs").addEventListener("click", saveLoanPkgs);
$("btn-load-features").addEventListener("click", loadFeatures);
$("btn-save-features").addEventListener("click", saveFeatures);
$("btn-load-activities").addEventListener("click", loadActivities);
$("btn-save-activities").addEventListener("click", saveActivities);
$("btn-load-params").addEventListener("click", () => loadParams(currentView().params?.group || "__all__"));
$("btn-save-params").addEventListener("click", saveParams);
$("btn-load-aliases").addEventListener("click", loadAliases);
$("btn-save-aliases").addEventListener("click", saveAliases);
$("btn-load-record-pets").addEventListener("click", loadRecordPets);
// 2.2.1：宠物详情页刷新（重新加载当前宠物 uid 的详情）
$("btn-load-record-pet-detail").addEventListener("click", () => loadRecordPetDetail(currentView().params?.uid));
$("btn-load-record-users").addEventListener("click", loadRecordUsers);
$("btn-load-record-prices").addEventListener("click", loadRecordPrices);
$("record-pets-search").addEventListener("input", renderRecordPets);
// 2.2.2：宠物记录 状态 / 档位 筛选 + 排序（前端本地过滤，无需重新请求）
$("record-pets-status").addEventListener("change", renderRecordPets);
$("record-pets-tier").addEventListener("change", renderRecordPets);
$("record-pets-sort").addEventListener("change", renderRecordPets);
$("record-users-search").addEventListener("input", renderRecordUsers);
// 2.1.0：排序自定义（切换排序方式 / 升降序，前端本地排序，无需重新请求）
$("record-users-sort").addEventListener("change", (e) => {
  recordUsersSort = e.target.value;
  renderRecordUsers();
  const label = SORT_LABELS[recordUsersSort] || recordUsersSort;
  setStatus("status-record-users", `${label} ${recordUsersAsc ? "升序 ↑" : "降序 ↓"}`);
});
$("btn-record-users-order").addEventListener("click", () => {
  recordUsersAsc = !recordUsersAsc;
  $("btn-record-users-order").textContent = recordUsersAsc ? "升序 ↑" : "降序 ↓";
  renderRecordUsers();
});
$("btn-export-data").addEventListener("click", exportData);
$("btn-import-data").addEventListener("click", importData);
$("btn-sync-group-names").addEventListener("click", syncGroupNames);

await bridge.ready();
syncLockUI();

// ================= 局域网开放（1.7.9） =================
let lanState = null;

function showLanLock() {
  $("lan-lock").classList.remove("hidden");
  $("lan-unlock-input").value = "";
  $("lan-unlock-error").classList.add("hidden");
  $("lan-unlock-input").focus();
}
function hideLanLock() {
  $("lan-lock").classList.add("hidden");
}

async function lanRefresh() {
  try {
    lanState = await bridge.apiGet("lan/status");
  } catch (e) {
    lanState = null;
    return;
  }
  const s = lanState || {};
  if (s.is_local) {
    hideLanLock();
  } else if (s.enabled && !s.unlocked) {
    showLanLock();
  } else {
    hideLanLock();
  }
}

async function lanLoadSettings() {
  setStatus("status-lan", "加载中...");
  try {
    const s = await bridge.apiGet("lan/status");
    lanState = s;
    $("lan-enabled").checked = !!s.enabled;
    $("lan-password").value = "";
    setStatus("status-lan", s.is_local
      ? (s.enabled ? "✅ 已开启（本地免密）" : "⏸️ 已关闭（本地访问不受影响）")
      : "⚠️ 远程设备无法修改局域网设置（仅本地服务器可改）");
    const localOnly = !!s.is_local;
    ["lan-enabled", "lan-password", "btn-lan-save"].forEach((id) => {
      const el = $(id);
      if (el) el.disabled = !localOnly;
    });
  } catch (e) {
    setStatus("status-lan", "❌ 读取失败：" + e.message);
  }
}

async function lanSaveSettings() {
  const enabled = $("lan-enabled").checked;
  const password = $("lan-password").value;
  const payload = { enabled };
  if (password) payload.password = password;
  setStatus("status-lan", "保存中...");
  try {
    await bridge.apiPost("lan/setup", payload);
    $("lan-password").value = "";
    setStatus("status-lan", "✅ 已保存" + (password ? "，密码已更新（仅存哈希）" : ""));
    await lanLoadSettings();
  } catch (e) {
    if (e && e.message && /403|本地|仅本地/.test(String(e.message))) {
      setStatus("status-lan", "❌ 远程设备无法修改，请在本地服务器上操作");
    } else {
      setStatus("status-lan", "❌ 保存失败：" + (e && e.message ? e.message : String(e)));
    }
  }
}

async function lanUnlock() {
  const pw = $("lan-unlock-input").value;
  if (!pw) {
    $("lan-unlock-error").classList.remove("hidden");
    $("lan-unlock-error").textContent = "请输入密码";
    return;
  }
  try {
    const r = await bridge.apiPost("lan/unlock", { password: pw });
    if (r && r.unlocked) {
      hideLanLock();
      // 2.2.0：解锁后直接刷新页面重新读取状态（无需在此手动置 lanState）
      location.reload();
    }
  } catch (e) {
    $("lan-unlock-error").classList.remove("hidden");
    // 2.2.2：展示服务端消息（含剩余机会 / 锁定提示）；密码错误不再触发 AstrBot 登出
    $("lan-unlock-error").textContent = (e && e.message) ? String(e.message) : "密码错误，请重试";
  }
}

async function openLanRecords() {
  $("lan-records-modal").classList.remove("hidden");
  $("lan-records-body").innerHTML = '<p class="hint">加载中...</p>';
  try {
    const r = await bridge.apiGet("lan/records");
    const recs = (r && r.records) || [];
    if (!recs.length) {
      $("lan-records-body").innerHTML = '<p class="hint">暂无访问记录。</p>';
      return;
    }
    $("lan-records-body").innerHTML = `<table class="lan-table">
      <thead><tr><th>时间</th><th>设备</th><th>IP</th><th>密码</th></tr></thead>
      <tbody>${recs.map((rc) => `<tr>
        <td>${esc(rc.ts)}</td>
        <td>${esc(rc.name)}</td>
        <td>${esc(rc.ip)}</td>
        <td>${rc.ok ? '<span class="lan-ok">✅ 正确</span>' : '<span class="lan-bad">❌ 未通过</span>'}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  } catch (e) {
    $("lan-records-body").innerHTML = `<p class="hint">❌ 读取失败：${esc(e && e.message ? e.message : String(e))}（仅本地服务器可查看）</p>`;
  }
}
function closeLanRecords() { $("lan-records-modal").classList.add("hidden"); }

async function openLanBlacklist() {
  $("lan-blacklist-modal").classList.remove("hidden");
  $("lan-blacklist-input").value = "";
  await renderLanBlacklist();
}
async function renderLanBlacklist() {
  $("lan-blacklist-body").innerHTML = '<p class="hint">加载中...</p>';
  try {
    const r = await bridge.apiGet("lan/blacklist");
    const list = (r && r.blacklist) || [];
    if (!list.length) {
      $("lan-blacklist-body").innerHTML = '<p class="hint">黑名单为空。</p>';
      return;
    }
    $("lan-blacklist-body").innerHTML = `<table class="lan-table">
      <thead><tr><th>禁止的 IP / 网段</th><th>操作</th></tr></thead>
      <tbody>${list.map((ip) => `<tr>
        <td>${esc(ip)}</td>
        <td><button data-lan-bl-del="${esc(ip)}" class="row-del danger">解除</button></td>
      </tr>`).join("")}</tbody>
    </table>`;
    $("lan-blacklist-body").querySelectorAll("button[data-lan-bl-del]").forEach((b) =>
      b.addEventListener("click", () => lanBlacklist("remove", b.getAttribute("data-lan-bl-del"))),
    );
  } catch (e) {
    $("lan-blacklist-body").innerHTML = `<p class="hint">❌ 读取失败：${esc(e && e.message ? e.message : String(e))}（仅本地服务器可管理）</p>`;
  }
}
async function lanBlacklist(action, ip) {
  try {
    await bridge.apiPost("lan/blacklist", { action, ip });
    await renderLanBlacklist();
  } catch (e) {
    alert("操作失败：" + (e && e.message ? e.message : String(e)));
  }
}
function closeLanBlacklist() { $("lan-blacklist-modal").classList.add("hidden"); }

// ---- 局域网访问门：任何 API 返回 403（未解锁/被禁）时重新弹出锁屏 ----
function lanWrap(obj, name) {
  const orig = obj[name];
  obj[name] = async (...args) => {
    try {
      const r = await orig.apply(obj, args);
      if (r && r.status === "error" && /局域网|禁止/.test(String(r.message || ""))) {
        await lanRefresh();
        const s = lanState || {};
        if (!s.is_local && s.enabled && !s.unlocked) showLanLock();
      }
      return r;
    } catch (e) {
      const msg = e && (e.message || e.error || "") ? String(e.message || e.error || "") : "";
      if (/局域网|禁止|403/.test(msg)) {
        await lanRefresh();
        const s = lanState || {};
        if (!s.is_local && s.enabled && !s.unlocked) showLanLock();
      }
      throw e;
    }
  };
  return obj[name];
}
if (bridge && typeof bridge.apiPost === "function") {
  lanWrap(bridge, "apiGet");
  lanWrap(bridge, "apiPost");
}

// 事件绑定
$("btn-lan-save").addEventListener("click", lanSaveSettings);
$("btn-lan-records").addEventListener("click", openLanRecords);
$("btn-lan-records-close").addEventListener("click", closeLanRecords);
$("btn-lan-blacklist").addEventListener("click", openLanBlacklist);
$("btn-lan-blacklist-close").addEventListener("click", closeLanBlacklist);
$("btn-lan-blacklist-add").addEventListener("click", () => {
  const ip = ($("lan-blacklist-input").value || "").trim();
  if (!ip) { alert("请输入要禁止的 IP 或网段"); return; }
  lanBlacklist("add", ip);
});
$("btn-lan-unlock").addEventListener("click", lanUnlock);
$("lan-unlock-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") lanUnlock();
  if (e.key === "Escape") hideLanLock();
});
$("lan-records-modal").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeLanRecords();
});
$("lan-blacklist-modal").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) closeLanBlacklist();
});

// 2.2.2：保存成功（resp.saved）时自动退出当前面板的待保存状态（config/draft 自身除外）
function dirtyWrap(obj, name) {
  const orig = obj[name];
  obj[name] = async (...args) => {
    const r = await orig.apply(obj, args);
    try {
      if (r && r.saved && args[0] !== "config/draft") {
        const cur = currentView();
        if (cur.kind === "panel" && dirtyPanels.has(cur.id)) {
          dirtyPanels.delete(cur.id);
          if (!dirtyPanels.size) bridge.apiPost("config/draft", { clear: true }).catch(() => {});
        }
      }
    } catch (e) { /* 忽略 */ }
    return r;
  };
  return obj[name];
}
if (bridge && typeof bridge.apiPost === "function") {
  dirtyWrap(bridge, "apiPost");
}

// 2.2.2：待保存状态监听（输入/勾选/加行/删行 → 标记脏 + 暂存容灾草稿）
function _dirtyFromEvent(e) {
  const panel = e.target.closest && e.target.closest(".panel");
  if (!panel) return;
  // section.id 带 panel- 前缀，DIRTY_PANELS 用导航栈 id（无前缀）
  const pid = String(panel.id || "").replace(/^panel-/, "");
  if (!DIRTY_PANELS[pid]) return;
  if (e.type === "click" && !e.target.closest(".add-row, [data-alias-add], [data-alias-del], .row-del")) return;
  markDirty(pid);
}
document.addEventListener("input", _dirtyFromEvent, true);
document.addEventListener("change", _dirtyFromEvent, true);
document.addEventListener("click", _dirtyFromEvent, true);

// 2.2.2：未保存修改弹窗按钮
$("btn-unsaved-save").addEventListener("click", () => { if (_unsavedConfirm) _unsavedConfirm(); });
$("btn-unsaved-discard").addEventListener("click", () => { if (_unsavedDiscard) _unsavedDiscard(); });
$("btn-unsaved-cancel").addEventListener("click", closeUnsavedModal);
$("unsaved-modal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeUnsavedModal(); });

// ================= 2.2.2 历史配置数据（historydata） =================
async function loadHistoryPanel() {
  setStatus("status-history", "加载中...");
  try {
    const r = await bridge.apiGet("history/list");
    $("history-enabled").checked = !!(r && r.enabled);
    const versions = (r && r.versions) || [];
    renderHistoryList(versions);
    setStatus("status-history", `✅ ${r && r.enabled ? "已开启保留" : "未开启保留"} · 共 ${versions.length} 个版本`);
  } catch (e) {
    setStatus("status-history", "❌ 加载失败：" + e.message);
  }
}

function renderHistoryList(versions) {
  const box = $("history-list-body");
  if (!versions.length) {
    box.innerHTML = '<p class="hint">暂无历史版本（开启保留功能后，每次保存后台数据前会自动记录一份旧配置）。</p>';
    return;
  }
  box.innerHTML = '<table class="lan-table"><thead><tr><th>备份时间</th><th>说明</th><th>操作</th></tr></thead><tbody>'
    + versions.map((v) => `<tr><td>${esc(v.time || v.file)}</td><td>${esc(v.reason || "")}</td>
        <td><button data-hb-back="${esc(v.file)}">回溯</button>
        <button data-hb-del="${esc(v.file)}" class="danger">删除</button></td></tr>`).join("")
    + "</tbody></table>";
  box.querySelectorAll("button[data-hb-back]").forEach((b) =>
    b.addEventListener("click", () => openHistoryRollback(b.getAttribute("data-hb-back"))));
  box.querySelectorAll("button[data-hb-del]").forEach((b) =>
    b.addEventListener("click", () => historyDelete(b.getAttribute("data-hb-del"))));
}

async function historyToggle() {
  setStatus("status-history", "保存中...");
  try {
    const r = await bridge.apiPost("history/toggle", { enabled: $("history-enabled").checked });
    setStatus("status-history", r && r.enabled ? "✅ 已开启历史数据保留" : "已关闭历史数据保留");
    loadHistoryPanel();
  } catch (e) {
    setStatus("status-history", "❌ 保存失败：" + e.message);
  }
}

async function historyDelete(file) {
  if (!confirm("确定删除该历史版本？删除后不可恢复。")) return;
  try {
    await bridge.apiPost("history/delete", { file });
    loadHistoryPanel();
  } catch (e) {
    setStatus("status-history", "❌ 删除失败：" + e.message);
  }
}

// 回溯弹窗：第一步输入管理员密码 + 验证码点「确认校验」，第二步在另一位置点「确认执行回溯」
let _historyFile = "";
async function openHistoryRollback(file) {
  _historyFile = file;
  $("history-target").textContent = file;
  $("history-captcha-input").value = "";
  $("history-password-input").value = "";
  $("history-error").classList.add("hidden");
  $("history-step1").classList.remove("hidden");
  $("history-step2").classList.add("hidden");
  $("history-modal").classList.remove("hidden");
  try {
    const r = await bridge.apiPost("history/captcha", { file });
    $("history-captcha-code").textContent = (r && r.captcha) || "获取失败";
  } catch (e) {
    $("history-captcha-code").textContent = "获取失败";
  }
}

function historyShowError(msg) {
  $("history-error").textContent = msg;
  $("history-error").classList.remove("hidden");
}

async function historyVerifyStep() {
  const code = ($("history-captcha-input").value || "").trim();
  if (!code) { historyShowError("请输入验证码"); return; }
  try {
    await bridge.apiPost("history/verify", { file: _historyFile, captcha: code, password: $("history-password-input").value });
    $("history-error").classList.add("hidden");
    $("history-step1").classList.add("hidden");
    $("history-step2").classList.remove("hidden");
  } catch (e) {
    // 验证码未消费仍有效，直接提示错误即可
    historyShowError(e && e.message ? String(e.message) : "校验失败");
  }
}

async function historyRollbackStep() {
  try {
    const r = await bridge.apiPost("history/rollback", {
      file: _historyFile,
      captcha: ($("history-captcha-input").value || "").trim(),
      password: $("history-password-input").value,
    });
    $("history-modal").classList.add("hidden");
    setStatus("status-history", "✅ " + ((r && r.msg) || "已回溯"));
    loadHistoryPanel();
  } catch (e) {
    $("history-modal").classList.add("hidden");
    historyShowError(e && e.message ? String(e.message) : "回溯失败");
    setStatus("status-history", "❌ " + (e && e.message ? e.message : "回溯失败"));
  }
}

function closeHistoryModal() { $("history-modal").classList.add("hidden"); }

// 2.2.2 事件绑定
$("history-enabled").addEventListener("change", historyToggle); // 勾选即保存，无需再点按钮
$("btn-history-toggle").addEventListener("click", historyToggle);
$("btn-history-refresh").addEventListener("click", loadHistoryPanel);
$("btn-load-record-user-detail").addEventListener("click", () => loadRecordUserDetail(currentView().params?.uid));
$("btn-history-verify").addEventListener("click", historyVerifyStep);
$("btn-history-rollback").addEventListener("click", historyRollbackStep);
$("btn-history-modal-close").addEventListener("click", closeHistoryModal);
$("history-modal").addEventListener("click", (e) => { if (e.target === e.currentTarget) closeHistoryModal(); });

// 启动时检查局域网状态（lanWrap 已安装，403 会触发锁屏；调试条在解锁后加载）
lanRefresh();
loadDebugStatus();
checkDraftOnBoot(); // 2.2.2：启动时检查上次未保存修改的容灾草稿（未解锁时静默跳过）
// 2.2.0：局域网设置表单不再在启动时重复请求，打开「系统设置」面板时由 loadCurrent 加载
