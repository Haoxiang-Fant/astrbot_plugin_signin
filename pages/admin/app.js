const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);

function setStatus(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

async function loadConfig() {
  setStatus("status-config", "加载中...");
  try {
    const data = await bridge.apiGet("backend/config");
    $("config-text").value = (data && data.content) || "";
    setStatus("status-config", "✅ 已加载");
  } catch (e) {
    setStatus("status-config", "❌ 加载失败：" + e.message);
  }
}

async function saveConfig() {
  const content = $("config-text").value;
  setStatus("status-config", "保存中...");
  try {
    await bridge.apiPost("backend/config", { content });
    setStatus("status-config", "✅ 已保存");
  } catch (e) {
    setStatus("status-config", "❌ 保存失败：" + e.message);
  }
}

// 宠物商店固定类型（前端硬编码，不依赖后端返回；后端缺失 types 时按 content 解析填充）
const PETSHOP_TYPES = ["食物", "饮料", "药物", "玩具"];

// 从合并 content 中按 [商店:xxx] 段落提取属于某类型的文本
function extractTypeContent(content, typeKey) {
  if (!content) return "";
  const sections = [];
  let cur = null;
  for (const line of content.split("\n")) {
    const s = line.trim();
    if (s.startsWith("[") && s.endsWith("]") && s.includes(":")) {
      cur = { type: "", lines: [line + "\n"] };
      sections.push(cur);
      continue;
    }
    if (cur) cur.lines.push(line + "\n");
    if (cur && s.startsWith("类型=")) {
      let t = s.slice(3).trim();
      if (t === "食品") t = "食物";
      cur.type = t;
    }
  }
  const out = sections
    .filter((x) => x.type === typeKey)
    .map((x) => x.lines.join(""))
    .join("\n");
  return out;
}

async function loadPetShop() {
  setStatus("status-petshop", "加载中...");
  try {
    const data = await bridge.apiGet("petshop");
    const types = (data && data.types) || [];
    const mergedContent = (data && data.content) || "";
    const box = $("petshop-editors");
    // 永远渲染 4 个类型编辑器；内容优先 types，缺失时从合并 content 按类型提取
    box.innerHTML = PETSHOP_TYPES.map((key, i) => {
      const t = types.find((x) => x.key === key);
      const content =
        (t && t.content) || extractTypeContent(mergedContent, key);
      return `<details class="param-subgroup" ${i === 0 ? "open" : ""}>
        <summary>${key}</summary>
        <div class="sub-editor">
          <div class="toolbar">
            <button data-petshop-load="${key}">加载</button>
            <button data-petshop-save="${key}" class="primary">保存</button>
            <span id="status-petshop-${key}" class="status"></span>
          </div>
          <textarea id="petshop-text-${key}" spellcheck="false" placeholder="点击「加载」读取 ${key} 配置...">${content}</textarea>
        </div>
      </details>`;
    }).join("");
    PETSHOP_TYPES.forEach((key) => {
      document
        .querySelector(`[data-petshop-load="${key}"]`)
        .addEventListener("click", () => loadPetShopType(key));
      document
        .querySelector(`[data-petshop-save="${key}"]`)
        .addEventListener("click", () => savePetShopType(key));
    });
    setStatus("status-petshop", "✅ 已加载");
  } catch (e) {
    setStatus("status-petshop", "❌ 加载失败：" + e.message);
  }
}

async function loadPetShopType(key) {
  setStatus(`status-petshop-${key}`, "加载中...");
  try {
    const data = await bridge.apiGet("petshop");
    const types = (data && data.types) || [];
    const t = types.find((x) => x.key === key);
    const mergedContent = (data && data.content) || "";
    const content = (t && t.content) || extractTypeContent(mergedContent, key);
    $(`petshop-text-${key}`).value = content || "";
    setStatus(`status-petshop-${key}`, "✅ 已加载");
  } catch (e) {
    setStatus(`status-petshop-${key}`, "❌ 加载失败：" + e.message);
  }
}

async function savePetShopType(key) {
  const content = $(`petshop-text-${key}`).value;
  setStatus(`status-petshop-${key}`, "保存中...");
  try {
    await bridge.apiPost("petshop", { key, content });
    setStatus(`status-petshop-${key}`, "✅ 已保存");
  } catch (e) {
    setStatus(`status-petshop-${key}`, "❌ 保存失败：" + e.message);
  }
}

async function loadPetShopAll() {
  await loadPetShop(); // loadPetShop 已填充全部 4 个编辑器内容
}

async function savePetShopAll() {
  setStatus("status-petshop", "保存中...");
  let failed = false;
  for (const key of PETSHOP_TYPES) {
    const el = $(`petshop-text-${key}`);
    if (!el) continue;
    try {
      await bridge.apiPost("petshop", { key, content: el.value });
      setStatus(`status-petshop-${key}`, "✅ 已保存");
    } catch (e) {
      failed = true;
      setStatus(`status-petshop-${key}`, "❌ 保存失败：" + e.message);
    }
  }
  setStatus("status-petshop", failed ? "⚠️ 部分保存失败" : "✅ 已全部保存");
}

async function loadCrops() {
  setStatus("status-crops", "加载中...");
  try {
    const data = await bridge.apiGet("farm/crops");
    $("crops-text").value = (data && data.content) || "";
    setStatus("status-crops", "✅ 已加载");
  } catch (e) {
    setStatus("status-crops", "❌ 加载失败：" + e.message);
  }
}

async function saveCrops() {
  const content = $("crops-text").value;
  setStatus("status-crops", "保存中...");
  try {
    await bridge.apiPost("farm/crops", { content });
    setStatus("status-crops", "✅ 已保存");
  } catch (e) {
    setStatus("status-crops", "❌ 保存失败：" + e.message);
  }
}

async function loadFerts() {
  setStatus("status-ferts", "加载中...");
  try {
    const data = await bridge.apiGet("farm/ferts");
    $("ferts-text").value = (data && data.content) || "";
    setStatus("status-ferts", "✅ 已加载");
  } catch (e) {
    setStatus("status-ferts", "❌ 加载失败：" + e.message);
  }
}

async function saveFerts() {
  const content = $("ferts-text").value;
  setStatus("status-ferts", "保存中...");
  try {
    await bridge.apiPost("farm/ferts", { content });
    setStatus("status-ferts", "✅ 已保存");
  } catch (e) {
    setStatus("status-ferts", "❌ 保存失败：" + e.message);
  }
}

async function loadLoanPkgs() {
  setStatus("status-loanpkgs", "加载中...");
  try {
    const data = await bridge.apiGet("loan/packages");
    $("loanpkgs-text").value = (data && data.content) || "";
    setStatus("status-loanpkgs", "✅ 已加载");
  } catch (e) {
    setStatus("status-loanpkgs", "❌ 加载失败：" + e.message);
  }
}

async function saveLoanPkgs() {
  const content = $("loanpkgs-text").value;
  setStatus("status-loanpkgs", "保存中...");
  try {
    await bridge.apiPost("loan/packages", { content });
    setStatus("status-loanpkgs", "✅ 已保存");
  } catch (e) {
    setStatus("status-loanpkgs", "❌ 保存失败：" + e.message);
  }
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
      box.innerHTML = '<p class="hint">没有可配置的运行参数。</p>';
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
  const input =
    p.type === "int" || p.type === "float"
      ? `<input type="number" data-key="${p.key}" data-type="${p.type}" data-label="${p.label}" ${min}${max}value="${p.value}" />`
      : p.type === "bool"
        ? `<input type="checkbox" data-key="${p.key}" data-type="bool" data-label="${p.label}" ${p.value ? "checked" : ""} />`
        : `<input type="text" data-key="${p.key}" data-type="str" data-label="${p.label}" value="${p.value}" />`;
  return `<label class="param-item">
    <span class="param-label">${p.label}</span>
    <span class="param-input">${input}</span>
    <small>${p.desc || ""}${p.min != null ? `（范围 ${p.min}~${p.max ?? "∞"}）` : ""}</small>
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
  $("panel-data").classList.toggle("hidden", name !== "data");
  if (name === "shopedit") {
    loadPetShop();
    loadCrops();
    loadFerts();
  }
  if (name === "features") loadFeatures();
  if (name === "params") loadParams();
  if (name === "activities") loadActivities();
}

document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab)),
);
$("btn-load-config").addEventListener("click", loadConfig);
$("btn-save-config").addEventListener("click", saveConfig);
$("btn-load-petshop-all").addEventListener("click", loadPetShopAll);
$("btn-save-petshop-all").addEventListener("click", savePetShopAll);
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
$("btn-export-data").addEventListener("click", exportData);
$("btn-import-data").addEventListener("click", importData);

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

await bridge.ready();
// 默认进入「商店编辑」选项卡
loadPetShop();
loadCrops();
loadFerts();
loadDebugStatus();
