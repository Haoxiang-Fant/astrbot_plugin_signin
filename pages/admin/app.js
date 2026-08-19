const bridge = window.AstrBotPluginPage;

const $ = (id) => document.getElementById(id);

function setStatus(id, text) {
  $(id).textContent = text;
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
    a.download = "data.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus("status-data", "✅ 已导出 data.json");
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
    await bridge.apiPost("data/import", { content });
    setStatus("status-data", "✅ 导入成功");
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

function switchTab(name) {
  document
    .querySelectorAll(".tab")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("panel-config").classList.toggle("hidden", name !== "config");
  $("panel-crops").classList.toggle("hidden", name !== "crops");
  $("panel-ferts").classList.toggle("hidden", name !== "ferts");
  $("panel-loanpkgs").classList.toggle("hidden", name !== "loanpkgs");
  $("panel-params").classList.toggle("hidden", name !== "params");
  $("panel-activities").classList.toggle("hidden", name !== "activities");
  $("panel-data").classList.toggle("hidden", name !== "data");
  if (name === "params") loadParams();
  if (name === "activities") loadActivities();
}

document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab)),
);
$("btn-load-config").addEventListener("click", loadConfig);
$("btn-save-config").addEventListener("click", saveConfig);
$("btn-load-crops").addEventListener("click", loadCrops);
$("btn-save-crops").addEventListener("click", saveCrops);
$("btn-load-ferts").addEventListener("click", loadFerts);
$("btn-save-ferts").addEventListener("click", saveFerts);
$("btn-load-loanpkgs").addEventListener("click", loadLoanPkgs);
$("btn-save-loanpkgs").addEventListener("click", saveLoanPkgs);
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
loadConfig();
loadDebugStatus();
