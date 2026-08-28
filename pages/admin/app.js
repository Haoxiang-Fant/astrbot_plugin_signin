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

// ================= 数值表格编辑器（1.7.7：商店/打工/玩耍） =================
// 列定义：[key, 表头]；第一列固定为「名称+描述」，最后为金币/经验类收益列
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
// 商店：名称+描述 | 类型 | 五项效果 | 价格（金币）
const SHOP_EFFECT_FIELDS = [
  ["satiety", "饱食度"], ["thirst", "口渴值"], ["stamina", "体力"],
  ["mood", "心情值"], ["health", "健康度"], ["price", "价格(金币)"],
];
const PETSHOP_TYPES = ["食物", "饮料", "药物", "玩具"];
const PETSHOP_TYPE_ICONS = { 食物: "🍖", 饮料: "🥤", 药物: "💊", 玩具: "🧸" };
// 农场商店：作物 / 肥料
const CROP_FIELDS = [
  ["seed_price", "种子价格"], ["seed_sell_price", "种子卖价"], ["yield", "产量"],
  ["crop_price", "成熟售价"], ["exp", "收获经验"], ["min_level", "需要等级"],
  ["grow_minutes", "成熟(分钟)"],
];
const FERT_FIELDS = [
  ["price", "价格/时"], ["yield_add", "增产%/次"],
  ["max_accel", "可加速次数(-1不限)"],
];
// 贷款套餐：代码（3~10）+ 5 个数值列
const LOAN_FIELDS = [
  ["max_amount", "最大金额"], ["fav_req", "好感等级"], ["pet_req", "宠物等级"],
  ["farm_req", "农场等级"], ["rate", "日利率%"],
];

// ================= 「先加载后编辑」保护 =================
// 各数据编辑选项卡：未成功加载前禁用表格区域与「添加」按钮，并阻止保存（避免空表覆盖数据）
const loadedTabs = {};
const LOCKED_SECTIONS = {
  petshop: { tables: ["petshop-table"], adds: ["btn-petshop-add"], status: "status-petshop" },
  config: { tables: ["jobs-table", "plays-table"], adds: ["btn-jobs-add", "btn-plays-add"], status: "status-config" },
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
  let bodyRows;
  if (opts?.groupBy && Array.isArray(items)) {
    // 按类别分组：组头行（整行合并）+ 各组条目；未知类别排在最后
    const { field, order, icons } = opts.groupBy;
    const groups = new Map();
    for (const it of items) {
      const g = String(it?.[field] || "") || "其它";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(it);
    }
    const keys = [...order.filter((g) => groups.has(g)),
                  ...[...groups.keys()].filter((g) => !order.includes(g))];
    bodyRows = [];
    for (const g of keys) {
      const icon = (icons && icons[g]) || "📦";
      bodyRows.push(`<tr class="cat-row"><td colspan="${colCount}">${icon} ${esc(g)}</td></tr>`);
      for (const it of groups.get(g)) bodyRows.push(itemRowHtml(fields, it, opts));
    }
  } else {
    bodyRows = (items || []).map((it) => itemRowHtml(fields, it, opts));
  }
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
  const box = $(containerId);
  const tbody = box.querySelector("tbody");
  if (!tbody) {
    renderItemTable(containerId, fields, [], opts);
  }
  const tr = document.createElement("template");
  tr.innerHTML = itemRowHtml(fields, null, opts).trim();
  const row = tr.content.firstElementChild;
  row.querySelector(".row-del").addEventListener("click", () => row.remove());
  (box.querySelector("tbody") || box).appendChild(row);
  row.querySelector(".i-name, .i-code")?.focus();
}

function collectItemTable(containerId, fields, opts) {
  // 返回 { items, errors }；客户端校验：名称（或代码）非空且不重复、数值必须是数字
  const items = [];
  const errors = [];
  const seen = new Set();
  const rows = document.querySelectorAll(`#${containerId} tbody tr`);
  rows.forEach((tr, idx) => {
    tr.querySelectorAll("input,select").forEach((el) => el.classList.remove("invalid"));
    let item, label;
    if (opts?.codeMode) {
      const codeEl = tr.querySelector('[data-f="code"]');
      if (!codeEl) return; // 分组行
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
      if (!nameEl) return; // 类别分组行，无输入框
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
    if (opts?.withType) item.type = tr.querySelector('[data-f="type"]').value;
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

// ---------- 打工 / 玩耍 ----------
async function loadConfig() {
  setStatus("status-config", "加载中...");
  try {
    const data = await bridge.apiGet("backend/config");
    renderItemTable("jobs-table", JOB_FIELDS, (data && data.jobs) || []);
    renderItemTable("plays-table", PLAY_FIELDS, (data && data.plays) || []);
    setLoaded("config", true);
    setStatus("status-config", "✅ 已加载");
  } catch (e) {
    setLoaded("config", false);
    setStatus("status-config", "❌ 加载失败：" + e.message);
  }
}

async function saveConfig() {
  if (!guardLoaded("config")) return;
  const jobs = collectItemTable("jobs-table", JOB_FIELDS);
  const plays = collectItemTable("plays-table", PLAY_FIELDS);
  const errors = [...jobs.errors.map((m) => "打工·" + m), ...plays.errors.map((m) => "玩耍·" + m)];
  if (errors.length) {
    setStatus("status-config", "❌ " + errors.join("；"));
    return;
  }
  await saveItemTables("status-config", "backend/config", { jobs: jobs.items, plays: plays.items });
}

// ---------- 宠物商店 ----------
async function loadPetShop() {
  setStatus("status-petshop", "加载中...");
  try {
    const data = await bridge.apiGet("petshop");
    renderItemTable("petshop-table", SHOP_EFFECT_FIELDS, (data && data.items) || [], {
      withType: true,
      groupBy: { field: "type", order: PETSHOP_TYPES, icons: PETSHOP_TYPE_ICONS },
    });
    setLoaded("petshop", true);
    setStatus("status-petshop", "✅ 已加载");
  } catch (e) {
    setLoaded("petshop", false);
    setStatus("status-petshop", "❌ 加载失败：" + e.message);
  }
}

async function savePetShopAll() {
  if (!guardLoaded("petshop")) return;
  const shop = collectItemTable("petshop-table", SHOP_EFFECT_FIELDS, { withType: true });
  if (shop.errors.length) {
    setStatus("status-petshop", "❌ " + shop.errors.join("；"));
    return;
  }
  await saveItemTables("status-petshop", "petshop", { items: shop.items });
}

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

async function saveCrops() {
  if (!guardLoaded("crops")) return;
  const crops = collectItemTable("crops-table", CROP_FIELDS);
  if (crops.errors.length) {
    setStatus("status-crops", "❌ " + crops.errors.join("；"));
    return;
  }
  await saveItemTables("status-crops", "farm/crops", { items: crops.items });
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

async function saveFerts() {
  if (!guardLoaded("ferts")) return;
  const ferts = collectItemTable("ferts-table", FERT_FIELDS);
  if (ferts.errors.length) {
    setStatus("status-ferts", "❌ " + ferts.errors.join("；"));
    return;
  }
  await saveItemTables("status-ferts", "farm/ferts", { items: ferts.items });
}

// ---------- 恢复默认道具数据（运行参数面板·危险操作区，需验证码确认） ----------
let _captchaCode = "";

function openCaptchaModal() {
  // 每次打开生成新的 6 位随机验证码，显示在输入框旁边
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

async function applyBenchmark() {
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

async function saveLoanPkgs() {
  if (!guardLoaded("loanpkgs")) return;
  const loans = collectItemTable("loans-table", LOAN_FIELDS, { codeMode: true });
  if (loans.errors.length) {
    setStatus("status-loanpkgs", "❌ " + loans.errors.join("；"));
    return;
  }
  await saveItemTables("status-loanpkgs", "loan/packages", { items: loans.items });
}

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
    // 新版：files 打包；旧版：仅 data.json 的 content 字段
    const payload = parsed && parsed.files ? { files: parsed.files } : { content };
    await bridge.apiPost("data/import", payload);
    setStatus("status-data", "✅ 导入成功（存档 + 自定义配置已还原）");
  } catch (e) {
    setStatus("status-data", "❌ 导入失败：" + e.message);
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

async function saveActivities() {
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
        v = 0; // 数值要求留空 = 0（不限）
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
    return;
  }
  setStatus("status-activities", "保存中...");
  try {
    const resp = await bridge.apiPost("activities", { enabled, configs });
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

async function loadParams() {
  setStatus("status-params", "加载中...");
  try {
    const data = await bridge.apiGet("params");
    const items = (data && data.params) || [];
    const box = $("params-list");
    if (!items.length) {
      box.innerHTML = '<p class="hint">没有可配置的设置项。</p>';
      return;
    }
    // 两级分组：组 → 子组 → 参数
    const groups = {};
    items.forEach((p) => {
      const g = p.group || "其他";
      const sg = p.subgroup || "通用";
      (groups[g] = groups[g] || {})[sg] = groups[g][sg] || [];
      groups[g][sg].push(p);
    });
    box.innerHTML = Object.entries(groups)
      .map(
        ([gname, subs]) => `<details class="param-group">
          <summary>${gname}（${Object.values(subs).flat().length}）</summary>
          <div class="param-subgroups">${Object.entries(subs)
            .map(
              ([sgname, arr]) => `<details class="param-subgroup">
                <summary>${sgname}（${arr.length}）</summary>
                <div class="param-items">${arr.map(renderParamItem).join("")}</div>
              </details>`,
            )
            .join("")}</div>
        </details>`,
      )
      .join("");
    setStatus("status-params", "✅ 已加载");
  } catch (e) {
    setStatus("status-params", "❌ 加载失败：" + e.message);
  }
}

function renderParamItem(p) {
  const min = p.min != null ? `min="${p.min}" ` : "";
  const max = p.max != null ? `max="${p.max}" ` : "";
  let displayVal = p.value;
  if (p.type === "list" && Array.isArray(p.value)) {
    displayVal = p.value.join(",");
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

async function saveParams() {
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
  if (clientErrors.length) {
    setStatus("status-params", "❌ " + clientErrors.join("；"));
    return;
  }
  setStatus("status-params", "保存中...");
  try {
    const resp = await bridge.apiPost("params", { params });
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

async function saveFeatures() {
  const switches = {};
  document.querySelectorAll("#features-list [data-feature]").forEach((cb) => {
    switches[cb.dataset.feature] = cb.checked;
  });
  setStatus("status-features", "保存中...");
  try {
    await bridge.apiPost("feature/status", { switches });
    setStatus("status-features", "✅ 已保存并立即生效");
  } catch (e) {
    setStatus("status-features", "❌ 保存失败：" + e.message);
  }
}

function switchTab(name) {
  document
    .querySelectorAll(".tab")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("panel-shopedit").classList.toggle("hidden", name !== "shopedit");
  $("panel-config").classList.toggle("hidden", name !== "config");
  $("panel-loanpkgs").classList.toggle("hidden", name !== "loanpkgs");
  $("panel-features").classList.toggle("hidden", name !== "features");
  $("panel-params").classList.toggle("hidden", name !== "params");
  $("panel-activities").classList.toggle("hidden", name !== "activities");
  $("panel-aliases").classList.toggle("hidden", name !== "aliases");
  $("panel-data").classList.toggle("hidden", name !== "data");
  if (name === "shopedit") {
    loadPetShop();
    loadCrops();
    loadFerts();
  }
  if (name === "features") loadFeatures();
  if (name === "params") loadParams();
  if (name === "activities") loadActivities();
  if (name === "aliases") loadAliases();
}

document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab)),
);
$("btn-load-config").addEventListener("click", loadConfig);
$("btn-save-config").addEventListener("click", saveConfig);
$("btn-jobs-add").addEventListener("click", () => {
  if (guardLoaded("config")) addItemRow("jobs-table", JOB_FIELDS);
});
$("btn-plays-add").addEventListener("click", () => {
  if (guardLoaded("config")) addItemRow("plays-table", PLAY_FIELDS);
});
$("btn-load-petshop-all").addEventListener("click", loadPetShop);
$("btn-save-petshop-all").addEventListener("click", savePetShopAll);
$("btn-petshop-add").addEventListener("click", () => {
  if (guardLoaded("petshop")) addItemRow("petshop-table", SHOP_EFFECT_FIELDS, { withType: true });
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
  if (e.target === e.currentTarget) closeCaptchaModal(); // 点击遮罩关闭
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
$("btn-load-params").addEventListener("click", loadParams);
$("btn-save-params").addEventListener("click", saveParams);
$("btn-load-aliases").addEventListener("click", loadAliases);
$("btn-save-aliases").addEventListener("click", saveAliases);
$("btn-export-data").addEventListener("click", exportData);
$("btn-import-data").addEventListener("click", importData);
$("btn-sync-group-names").addEventListener("click", syncGroupNames);

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
    // 静默（接口不存在时隐藏）
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

// ================= 同义口令 =================
let aliasHeads = []; // 标准指令列表（后端返回）

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

async function saveAliases() {
  const rows = Array.from(document.querySelectorAll("#aliases-list .alias-row"));
  // 保护：从未成功加载过表格就点保存 → 阻止（避免误清空全部同义词）
  if (!rows.length || !document.querySelector("#aliases-list .alias-head")) {
    setStatus("status-aliases", "❌ 请先点击「加载」读取同义口令（若一直加载失败请重载插件）");
    return;
  }
  const aliases = {};
  for (const row of rows) {
    const keyEl = row.querySelector(".alias-key");
    if (!keyEl) continue; // 表头 / 添加行 不含 .alias-key，跳过
    const key = keyEl.value.trim();
    const val = row.querySelector(".alias-val").value;
    if (!key) continue; // 空行跳过
    aliases[key] = val;
  }
  setStatus("status-aliases", "保存中...");
  try {
    await bridge.apiPost("alias/save", { aliases });
    setStatus("status-aliases", "✅ 已保存并立即生效");
    loadAliases();
  } catch (e) {
    setStatus("status-aliases", "❌ 保存失败：" + e.message);
  }
}

await bridge.ready();
// 初始锁定所有数据编辑区（加载成功后解锁）
syncLockUI();
// 默认进入「商店编辑」选项卡
loadPetShop();
loadCrops();
loadFerts();
loadDebugStatus();

// ================= 局域网开放（1.7.9） =================
let lanState = null; // {enabled, is_local, unlocked, password_set, records_count, ip}

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
    // 远程设备禁用设置区
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
      lanState = { ...(lanState || {}), unlocked: true };
      location.reload(); // 重新加载，进入完整后台
    }
  } catch (e) {
    $("lan-unlock-error").classList.remove("hidden");
    $("lan-unlock-error").textContent = "密码错误，请重试";
  }
}

// ---- 访问记录 ----
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

// ---- 黑名单 ----
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
      // 未解锁/被禁：后端返回网关拒绝，重新弹出锁屏
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

// 启动时检查局域网状态 & 加载设置
lanRefresh();
lanLoadSettings();
