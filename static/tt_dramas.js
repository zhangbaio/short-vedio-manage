(() => {
  const PLATFORM = "tt";
  const EMPTY_COLSPAN = 13;
  const state = {
    page: 1,
    pageSize: 20,
    pages: 0,
    total: 0,
    sortBy: "record_time",
    sortDir: "desc",
    canViewAllUsers: false,
    ownerOptionsLoaded: false,
  };

  document.addEventListener("DOMContentLoaded", () => {
    bindControls();
    loadCompanies();
    loadDramas();
  });

  function bindControls() {
    document.getElementById("queryBtn")?.addEventListener("click", () => {
      state.page = 1;
      loadDramas();
    });
    document.getElementById("resetBtn")?.addEventListener("click", () => {
      for (const id of ["searchInput", "companySelect", "reviewSelect", "statusInput", "uploaderInput", "ownerSelect", "dateFrom", "dateTo"]) {
        const el = document.getElementById(id);
        if (el) el.value = "";
      }
      state.page = 1;
      loadDramas();
    });
    document.getElementById("exportBtn")?.addEventListener("click", exportCurrentFilter);
    document.getElementById("pageSizeSelect")?.addEventListener("change", (event) => {
      state.pageSize = Number(event.target.value || 20);
      state.page = 1;
      loadDramas();
    });
    document.getElementById("firstPageBtn")?.addEventListener("click", () => goToPage(1));
    document.getElementById("prevPageBtn")?.addEventListener("click", () => goToPage(state.page - 1));
    document.getElementById("nextPageBtn")?.addEventListener("click", () => goToPage(state.page + 1));
    document.getElementById("lastPageBtn")?.addEventListener("click", () => goToPage(state.pages));
    document.querySelectorAll("[data-sort]").forEach((button) => {
      button.addEventListener("click", () => {
        const sortBy = button.dataset.sort;
        if (state.sortBy === sortBy) {
          state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        } else {
          state.sortBy = sortBy;
          state.sortDir = "asc";
        }
        state.page = 1;
        loadDramas();
      });
    });
  }

  function goToPage(page) {
    const target = Math.min(Math.max(1, page), Math.max(1, state.pages));
    if (target !== state.page) {
      state.page = target;
      loadDramas();
    }
  }

  async function requestJSON(url, options = {}) {
    const response = await fetch(url, {
      headers: { Accept: "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || data.message || "请求失败");
    return data;
  }

  function buildParams(overrides = {}) {
    const params = new URLSearchParams({
      platform: PLATFORM,
      page: String(overrides.page || state.page),
      page_size: String(overrides.pageSize || state.pageSize),
      sort_by: state.sortBy,
      sort_dir: state.sortDir,
    });
    for (const [id, key] of [
      ["searchInput", "search"],
      ["companySelect", "company"],
      ["reviewSelect", "review_passed"],
      ["statusInput", "status"],
      ["uploaderInput", "uploader"],
      ["ownerSelect", "user_id"],
      ["dateFrom", "date_from"],
      ["dateTo", "date_to"],
    ]) {
      const value = document.getElementById(id)?.value?.trim();
      if (value) params.set(key, value);
    }
    return params;
  }

  async function loadDramas() {
    try {
      const data = await requestJSON(`/api/platform-dramas?${buildParams().toString()}`);
      state.page = Number(data.page || 1);
      state.pages = Number(data.pages || 0);
      state.total = Number(data.total || 0);
      state.canViewAllUsers = Boolean(data.can_view_all_users);
      document.getElementById("ownerFilterWrap").hidden = !state.canViewAllUsers;
      if (state.canViewAllUsers) loadOwners();
      renderRows(data.items || []);
      renderPagination();
      renderSortState();
    } catch (error) {
      renderRows([]);
      document.getElementById("pageInfo").textContent = error.message || "加载失败";
    }
  }

  async function loadCompanies() {
    try {
      const companies = await requestJSON("/api/companies");
      const select = document.getElementById("companySelect");
      for (const company of companies || []) {
        const option = document.createElement("option");
        option.value = company;
        option.textContent = company;
        select.appendChild(option);
      }
    } catch (_error) {
      // 公司列表加载失败时不影响主列表。
    }
  }

  async function loadOwners() {
    if (state.ownerOptionsLoaded) return;
    state.ownerOptionsLoaded = true;
    try {
      const users = await requestJSON("/api/users");
      const select = document.getElementById("ownerSelect");
      for (const user of users || []) {
        const option = document.createElement("option");
        option.value = String(user.id || "");
        option.textContent = user.username || `用户 ${user.id}`;
        select.appendChild(option);
      }
    } catch (_error) {
      state.ownerOptionsLoaded = false;
    }
  }

  function renderRows(items) {
    const tbody = document.getElementById("dramaTableBody");
    const mobileList = document.getElementById("dramaMobileList");
    tbody.innerHTML = "";
    mobileList.innerHTML = "";
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="${EMPTY_COLSPAN}" class="text-center text-muted py-4">暂无TT上传记录</td></tr>`;
      mobileList.innerHTML = '<div class="mobile-empty text-muted">暂无TT上传记录</div>';
      return;
    }
    items.forEach((item, index) => {
      const number = (state.page - 1) * state.pageSize + index + 1;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${number}</td>
        <td>${escapeHtml(item.record_time || item.date || "-")}</td>
        <td>${escapeHtml(item.owner_username || "-")}</td>
        <td>${escapeHtml(item.original_name || "-")}</td>
        <td>${escapeHtml(item.new_name || "-")}</td>
        <td>${escapeHtml(item.episodes || "-")}</td>
        <td>${escapeHtml(progressText(item))}</td>
        <td>${statusBadge(item.upload_status)}</td>
        <td>${escapeHtml(item.series_id || item.mini_series_id || "-")}</td>
        <td>${escapeHtml(item.audit_status || "-")}</td>
        <td class="text-truncate" style="max-width: 260px">${escapeHtml(item.audit_reject_detail || item.audit_reject_reason || "-")}</td>
        <td>${statusTextBadge(item.online_status)}</td>
        <td>${escapeHtml(item.uploader_display || item.account_profile_name || item.drama_uploader || "-")}</td>
      `;
      tbody.appendChild(tr);

      const card = document.createElement("div");
      card.className = "mobile-record-card";
      card.innerHTML = `
        <div class="mobile-record-head">
          <div>
            <div class="mobile-record-title">${escapeHtml(item.new_name || item.original_name || "-")}</div>
            <div class="mobile-record-subtitle">${escapeHtml(item.record_time || item.date || "-")}</div>
          </div>
          ${statusBadge(item.upload_status)}
        </div>
        <div class="mobile-record-grid">
          <div><span>用户</span><strong>${escapeHtml(item.owner_username || "-")}</strong></div>
          <div><span>TT ID</span><strong>${escapeHtml(item.series_id || item.mini_series_id || "-")}</strong></div>
          <div><span>上架</span><strong>${escapeHtml(item.online_status || "-")}</strong></div>
          <div><span>审核原因</span><strong>${escapeHtml(item.audit_reject_detail || item.audit_reject_reason || "-")}</strong></div>
        </div>
      `;
      mobileList.appendChild(card);
    });
  }

  function renderPagination() {
    document.getElementById("pageInfo").textContent = `第${state.page}页/共${state.pages || 1}页，共${state.total}条`;
    document.getElementById("firstPageBtn").disabled = state.page <= 1;
    document.getElementById("prevPageBtn").disabled = state.page <= 1;
    document.getElementById("nextPageBtn").disabled = state.page >= state.pages;
    document.getElementById("lastPageBtn").disabled = state.page >= state.pages;
  }

  function renderSortState() {
    document.querySelectorAll("[data-sort]").forEach((button) => {
      const active = button.dataset.sort === state.sortBy;
      button.classList.toggle("sort-active", active);
      const icon = button.querySelector(".sort-icon");
      if (icon) icon.textContent = active ? (state.sortDir === "asc" ? "↑" : "↓") : "↕";
    });
  }

  async function exportCurrentFilter() {
    const rows = [];
    const first = await requestJSON(`/api/platform-dramas?${buildParams({ page: 1, pageSize: 100 }).toString()}`);
    rows.push(...(first.items || []));
    const totalPages = Math.min(Number(first.pages || 1), 20);
    for (let page = 2; page <= totalPages; page += 1) {
      const data = await requestJSON(`/api/platform-dramas?${buildParams({ page, pageSize: 100 }).toString()}`);
      rows.push(...(data.items || []));
    }
    downloadCsv("TT短剧上传记录.csv", rows);
  }

  function downloadCsv(filename, rows) {
    const header = ["记录时间", "用户", "原剧名", "新剧名", "集数", "上传进度", "上传状态", "TT剧集ID", "审核状态", "审核未通过原因", "上架状态", "上传者"];
    const body = rows.map((item) => [
      item.record_time || item.date || "",
      item.owner_username || "",
      item.original_name || "",
      item.new_name || "",
      item.episodes || "",
      progressText(item),
      item.upload_status || "",
      item.series_id || item.mini_series_id || "",
      item.audit_status || "",
      item.audit_reject_detail || item.audit_reject_reason || "",
      item.online_status || "",
      item.uploader_display || item.account_profile_name || item.drama_uploader || "",
    ]);
    const csv = [header, ...body].map((row) => row.map(csvCell).join(",")).join("\r\n");
    const blob = new Blob([`﻿${csv}`], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function progressText(item) {
    const uploaded = Number(item.uploaded_video_count || 0);
    const total = Number(item.video_file_count || 0);
    if (total > 0) return `${uploaded}/${total}`;
    if (uploaded > 0) return String(uploaded);
    return "-";
  }

  function statusBadge(status) {
    const text = String(status || "-");
    const cls = /失败|错误|failed|error/i.test(text)
      ? "text-bg-danger"
      : /成功|完成|已上传|success|done/i.test(text)
        ? "text-bg-success"
        : "text-bg-secondary";
    return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
  }

  function statusTextBadge(status) {
    const text = String(status || "-").trim() || "-";
    const cls = /失败|错误|未通过|rejected|failed|error|deleted/i.test(text)
      ? "text-bg-danger"
      : /成功|已上架|已分销|online|manual_online|success|distributed|done|skipped_existing/i.test(text)
        ? "text-bg-success"
        : /待|审核中|pending|retry|skipped/i.test(text)
          ? "text-bg-warning"
          : "text-bg-secondary";
    return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
  }

  function csvCell(value) {
    return `"${String(value ?? "").replace(/"/g, '""')}"`;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }
})();
