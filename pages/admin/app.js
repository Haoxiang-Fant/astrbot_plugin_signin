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

function switchTab(name) {
  document
    .querySelectorAll(".tab")
    .forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("panel-config").classList.toggle("hidden", name !== "config");
  $("panel-data").classList.toggle("hidden", name !== "data");
}

document.querySelectorAll(".tab").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab)),
);
$("btn-load-config").addEventListener("click", loadConfig);
$("btn-save-config").addEventListener("click", saveConfig);
$("btn-export-data").addEventListener("click", exportData);
$("btn-import-data").addEventListener("click", importData);

await bridge.ready();
loadConfig();
