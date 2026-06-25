// 爆剧预测页前端逻辑：配置读写、任务状态、手动触发、今日预测/预警/日志展示。
(function () {
  "use strict";

  const GENRE_NAMES = { comic_series: "漫剧", ai_series: "AI短剧", short_play: "短剧" };
  const LEVEL_CLASS = { S: "text-bg-danger", A: "text-bg-warning", B: "text-bg-primary", C: "text-bg-secondary" };

  function $(id) { return document.getElementById(id); }

  function toast(msg, kind) {
    const el = $("boomToast");
    el.className = "alert mb-3 alert-" + (kind || "info");
    el.textContent = msg;
    el.classList.remove("d-none");
    setTimeout(() => el.classList.add("d-none"), 4000);
  }

  async function api(url, opts) {
    const res = await fetch(url, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ("请求失败 " + res.status));
    return data;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function pct(p) { return (Math.round((p || 0) * 1000) / 10).toFixed(1) + "%"; }

  // ---------- 配置 ----------
  async function loadConfig() {
    const cfg = await api("/api/boom-prediction/config");
    $("cfgEnabled").checked = cfg.enabled;
    $("cfgNewSync").checked = cfg.new_sync_enabled;
    $("cfgMetrics").checked = cfg.metrics_enabled;
    $("cfgPrediction").checked = cfg.prediction_enabled;
    $("cfgBoomAlert").checked = cfg.boom_alert_enabled;
    $("cfgNewSyncInterval").value = cfg.new_sync_interval_min;
    $("cfgMetricsInterval").value = cfg.metrics_interval_min;
    $("cfgPredictionInterval").value = cfg.prediction_interval_min;
    $("cfgBoomInterval").value = cfg.boom_alert_interval_min;
    $("cfgBoomMinScore").value = cfg.boom_alert_min_score;
    $("cfgPredictionLimit").value = cfg.prediction_limit;
    $("cfgMetricsLimit").value = cfg.metrics_limit;
    const genres = new Set(cfg.genres || []);
    document.querySelectorAll(".cfg-genre").forEach((el) => { el.checked = genres.has(el.value); });
  }

  async function saveConfig() {
    const genres = Array.from(document.querySelectorAll(".cfg-genre:checked")).map((el) => el.value);
    const patch = {
      enabled: $("cfgEnabled").checked,
      new_sync_enabled: $("cfgNewSync").checked,
      metrics_enabled: $("cfgMetrics").checked,
      prediction_enabled: $("cfgPrediction").checked,
      boom_alert_enabled: $("cfgBoomAlert").checked,
      new_sync_interval_min: +$("cfgNewSyncInterval").value,
      metrics_interval_min: +$("cfgMetricsInterval").value,
      prediction_interval_min: +$("cfgPredictionInterval").value,
      boom_alert_interval_min: +$("cfgBoomInterval").value,
      boom_alert_min_score: +$("cfgBoomMinScore").value,
      prediction_limit: +$("cfgPredictionLimit").value,
      metrics_limit: +$("cfgMetricsLimit").value,
      genres: genres,
    };
    try {
      await api("/api/boom-prediction/config", { method: "PUT", body: JSON.stringify(patch) });
      toast("配置已保存", "success");
      await loadConfig();
    } catch (e) { toast(e.message, "danger"); }
  }

  // ---------- 任务状态 ----------
  const TASK_NAMES = { new_sync: "上新同步", metrics_refresh: "指标补齐", today_prediction: "今日预测", boom_alert: "爆剧预警" };
  const STATUS_CLASS = { running: "text-bg-info", success: "text-bg-success", failed: "text-bg-danger", idle: "text-bg-secondary" };

  async function loadTasks() {
    const data = await api("/api/boom-prediction/task-status");
    const body = $("boomTaskBody");
    const rows = (data.tasks || []);
    if (!rows.length) { body.innerHTML = '<tr><td colspan="5" class="text-secondary">尚无任务记录</td></tr>'; return; }
    body.innerHTML = rows.map((t) => `
      <tr>
        <td>${esc(TASK_NAMES[t.task_type] || t.task_type)}</td>
        <td><span class="badge ${STATUS_CLASS[t.status] || "text-bg-secondary"}">${esc(t.status)}</span></td>
        <td class="small text-secondary">${esc(t.last_finished_at || "-")}</td>
        <td>${esc(t.duration_ms || 0)}</td>
        <td class="small">${esc(t.message || "-")}</td>
      </tr>`).join("");
  }

  async function runTask(taskType, btn) {
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = "执行中…";
    try {
      const r = await api("/api/boom-prediction/run/" + taskType, { method: "POST" });
      toast(TASK_NAMES[taskType] + " 完成", "success");
      await refreshAll();
    } catch (e) { toast(e.message, "danger"); }
    finally { btn.disabled = false; btn.textContent = old; }
  }

  // ---------- 今日预测 ----------
  async function loadPredictions() {
    const data = await api("/api/boom-prediction/today-predictions");
    const body = $("boomPredictBody");
    const rows = data.items || [];
    if (!rows.length) { body.innerHTML = '<tr><td colspan="10" class="text-secondary">暂无数据</td></tr>'; return; }
    body.innerHTML = rows.map((p) => `
      <tr>
        <td>${esc(p.title)}</td>
        <td>${esc(GENRE_NAMES[p.genre] || p.genre)}</td>
        <td class="small">${esc(p.category || "-")}</td>
        <td>${esc(p.episode_cnt || 0)}</td>
        <td><strong>${esc(p.potential)}</strong></td>
        <td><span class="badge ${LEVEL_CLASS[p.level] || "text-bg-secondary"}">${esc(p.level)}</span></td>
        <td>${pct(p.probability)}</td>
        <td>${esc(p.favorite_count || 0)}</td>
        <td>${esc(p.play_cnt || 0)}</td>
        <td class="small text-secondary">${esc(p.recommend_next || "-")}</td>
      </tr>`).join("");
  }

  // ---------- 预警 ----------
  async function loadAlerts() {
    const data = await api("/api/boom-prediction/boom-alerts");
    const body = $("boomAlertBody");
    const rows = data.items || [];
    if (!rows.length) { body.innerHTML = '<tr><td colspan="6" class="text-secondary">暂无数据</td></tr>'; return; }
    body.innerHTML = rows.map((a) => `
      <tr>
        <td class="small text-secondary">${esc(a.created_at)}</td>
        <td>${esc(a.title)}</td>
        <td>${esc(GENRE_NAMES[a.genre] || a.genre)}</td>
        <td><strong>${esc(a.score)}</strong> <span class="badge ${LEVEL_CLASS[a.level] || "text-bg-secondary"}">${esc(a.level)}</span></td>
        <td>${pct(a.probability)}</td>
        <td><span class="badge text-bg-secondary">${esc(a.status)}</span></td>
      </tr>`).join("");
  }

  // ---------- 日志 ----------
  const LEVEL_BADGE = { info: "text-bg-success", warn: "text-bg-warning", error: "text-bg-danger" };
  async function loadLogs() {
    const data = await api("/api/boom-prediction/logs");
    const body = $("boomLogBody");
    const rows = data.logs || [];
    if (!rows.length) { body.innerHTML = '<tr><td colspan="4" class="text-secondary">暂无数据</td></tr>'; return; }
    body.innerHTML = rows.map((l) => `
      <tr>
        <td class="small text-secondary">${esc(l.created_at)}</td>
        <td>${esc(TASK_NAMES[l.action] || l.action)}</td>
        <td><span class="badge ${LEVEL_BADGE[l.level] || "text-bg-secondary"}">${esc(l.level)}</span></td>
        <td class="small">${esc(l.message)}</td>
      </tr>`).join("");
  }

  async function refreshAll() {
    await Promise.all([loadTasks(), loadPredictions(), loadAlerts(), loadLogs()]);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    $("boomSaveCfg").addEventListener("click", saveConfig);
    $("boomRefresh").addEventListener("click", refreshAll);
    document.querySelectorAll("[data-run]").forEach((btn) => {
      btn.addEventListener("click", () => runTask(btn.getAttribute("data-run"), btn));
    });
    try { await loadConfig(); } catch (e) { toast(e.message, "danger"); }
    await refreshAll();
  });
})();
