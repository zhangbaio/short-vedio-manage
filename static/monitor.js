const monitorState = {
  filters: {
    mode: "created",
    company: "",
    date_from: "",
    date_to: "",
    hide_quick_add: "1",
  },
  chart: null,
};

async function initMonitorPage() {
  await ensureEcharts();
  bindMonitorEvents();
  setDefaultMonitorFilters();
  await Promise.all([fetchMonitorCompanies(), loadMonitorData()]);
}

document.addEventListener("DOMContentLoaded", initMonitorPage);

function bindMonitorEvents() {
  document.getElementById("monitorQueryBtn").addEventListener("click", () => {
    updateMonitorFiltersFromInputs();
    loadMonitorData();
  });
  document.getElementById("monitorResetBtn").addEventListener("click", async () => {
    setDefaultMonitorFilters();
    await loadMonitorData();
  });
  document.getElementById("monitorDateTo").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      document.getElementById("monitorQueryBtn").click();
    }
  });
}

function setDefaultMonitorFilters() {
  const today = new Date();
  const start = new Date(today);
  start.setDate(today.getDate() - 29);
  document.getElementById("monitorMode").value = "created";
  document.getElementById("monitorCompany").value = "";
  document.getElementById("monitorDateFrom").value = formatDateInput(start);
  document.getElementById("monitorDateTo").value = formatDateInput(today);
  document.getElementById("monitorHideQuickAdd").checked = true;
  updateMonitorFiltersFromInputs();
}

function updateMonitorFiltersFromInputs() {
  monitorState.filters.mode = document.getElementById("monitorMode").value;
  monitorState.filters.company = document.getElementById("monitorCompany").value;
  monitorState.filters.date_from = document.getElementById("monitorDateFrom").value;
  monitorState.filters.date_to = document.getElementById("monitorDateTo").value;
  monitorState.filters.hide_quick_add = document.getElementById("monitorHideQuickAdd").checked ? "1" : "";
}

function formatDateInput(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function fetchMonitorCompanies() {
  try {
    const companies = await requestJSON("/api/companies");
    if (!companies) return;
    const select = document.getElementById("monitorCompany");
    const currentValue = select.value;
    select.innerHTML = '<option value="">全部</option>';
    companies.forEach((company) => {
      const option = document.createElement("option");
      option.value = company;
      option.textContent = company;
      select.appendChild(option);
    });
    select.value = currentValue;
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function loadMonitorData() {
  const params = new URLSearchParams();
  Object.entries(monitorState.filters).forEach(([key, value]) => {
    if (value) {
      params.set(key, value);
    }
  });
  try {
    const data = await requestJSON(`/api/monitor/daily?${params.toString()}`);
    if (!data) return;
    renderMonitorSummary(data.summary || {});
    renderMonitorChart(data.rows || [], data.summary?.label_suffix || "");
    renderMonitorTable(data.rows || []);
    renderMonitorUpdatedAt();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function ensureEcharts() {
  if (window.echarts) {
    return window.echarts;
  }
  const sources = [
    "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js",
    "https://unpkg.com/echarts@5.5.1/dist/echarts.min.js",
    "https://cdn.bootcdn.net/ajax/libs/echarts/5.5.1/echarts.min.js",
  ];
  for (const src of sources) {
    try {
      await loadScript(src);
      if (window.echarts) {
        return window.echarts;
      }
    } catch (error) {
      console.warn(`Failed to load chart library from ${src}`, error);
    }
  }
  showChartUnavailable("图表库加载失败，当前仅显示统计卡片和明细表。");
  return null;
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-monitor-src="${src}"]`);
    if (existing) {
      if (window.echarts) {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`Failed to load ${src}`)), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset.monitorSrc = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(script);
  });
}

function renderMonitorSummary(summary) {
  document.getElementById("metricTodayNew").textContent = summary.today_new_count ?? 0;
  document.getElementById("metricTodayReviewed").textContent = summary.today_review_passed_count ?? 0;
  document.getElementById("metricTodayUploaded").textContent = summary.today_uploaded_count ?? 0;
  document.getElementById("metricRangeNew").textContent = summary.range_new_count ?? 0;
  document.getElementById("metricRangeRates").textContent = `审核率 ${formatPercent(summary.range_review_rate)} / 上传率 ${formatPercent(summary.range_uploaded_rate)}`;
}

function renderMonitorChart(rows, labelSuffix) {
  const chartDom = document.getElementById("monitorChart");
  if (!window.echarts) {
    showChartUnavailable("图表库未加载，暂时无法展示趋势图。");
    return;
  }
  if (!monitorState.chart) {
    monitorState.chart = window.echarts.init(chartDom);
    window.addEventListener("resize", () => monitorState.chart?.resize());
  }
  const labels = rows.map((item) => item.day);
  const newCounts = rows.map((item) => item.new_count);
  const reviewCounts = rows.map((item) => item.review_passed_count);
  const uploadCounts = rows.map((item) => item.uploaded_count);

  monitorState.chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { data: ["新增剧集", "审核通过", "上传通过"] },
    grid: { left: 36, right: 24, top: 48, bottom: 36 },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { rotate: labels.length > 10 ? 35 : 0 },
    },
    yAxis: {
      type: "value",
      name: labelSuffix ? `数量（${labelSuffix}）` : "数量",
      minInterval: 1,
    },
    series: [
      {
        name: "新增剧集",
        type: "bar",
        data: newCounts,
        itemStyle: { color: "#2563eb" },
        barMaxWidth: 28,
      },
      {
        name: "审核通过",
        type: "line",
        smooth: true,
        data: reviewCounts,
        itemStyle: { color: "#16a34a" },
      },
      {
        name: "上传通过",
        type: "line",
        smooth: true,
        data: uploadCounts,
        itemStyle: { color: "#f59e0b" },
      },
    ],
  });
}

function showChartUnavailable(message) {
  const chartDom = document.getElementById("monitorChart");
  if (!chartDom) return;
  chartDom.innerHTML = `<div class="monitor-chart-empty">${escapeHtml(message)}</div>`;
}

function renderMonitorTable(rows) {
  const tbody = document.getElementById("monitorTableBody");
  tbody.innerHTML = "";
  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-secondary py-4">当前筛选条件下暂无数据</td></tr>';
    return;
  }
  rows.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(item.day)}</td>
      <td>${item.new_count}</td>
      <td>${item.review_passed_count}</td>
      <td>${item.uploaded_count}</td>
      <td>${formatPercent(item.review_rate)}</td>
      <td>${formatPercent(item.upload_rate)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderMonitorUpdatedAt() {
  const now = new Date();
  document.getElementById("monitorLastUpdated").textContent = `最近更新：${now.toLocaleString("zh-CN", { hour12: false })}`;
}

function formatPercent(value) {
  const numeric = Number(value || 0);
  return `${(numeric * 100).toFixed(1)}%`;
}

async function requestJSON(url, options = {}) {
  const fetchOptions = { ...options };
  if (!(fetchOptions.body instanceof FormData)) {
    fetchOptions.headers = {
      Accept: "application/json",
      ...(fetchOptions.headers || {}),
    };
  }
  const response = await fetch(url, fetchOptions);
  if (response.status === 401) {
    window.location.href = "/login";
    return null;
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || data.message || "请求失败");
  }
  return data;
}

function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `monitor-toast alert alert-${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.add("show");
  });
  setTimeout(() => {
    toast.classList.remove("show");
    setTimeout(() => toast.remove(), 220);
  }, 2200);
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
