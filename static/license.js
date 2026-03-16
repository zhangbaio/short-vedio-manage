const licenseState = {
  page: 1,
  pageSize: 10,
  total: 0,
  pages: 1,
  sortBy: "created_at",
  sortDir: "desc",
  filters: {
    keyword: "",
    status: "",
    edition: "",
    show_deleted: "",
  },
};

let currentLicenseId = null;
let currentLicenseSecret = "";
let currentLicenseItems = [];
const selectedLicenseIds = new Set();

const licenseSecretModal = new bootstrap.Modal(document.getElementById("licenseSecretModal"));

document.addEventListener("DOMContentLoaded", initLicensePage);

function initLicensePage() {
  bindLicenseEvents();
  updateLicenseFiltersFromInputs();
  updateLicenseSortIcons();
  loadLicenses();
}

function bindLicenseEvents() {
  document.getElementById("licenseKeywordInput").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    document.getElementById("licenseQueryBtn").click();
  });
  document.getElementById("licenseQueryBtn").addEventListener("click", () => {
    updateLicenseFiltersFromInputs();
    licenseState.page = 1;
    loadLicenses();
  });
  document.getElementById("licenseResetBtn").addEventListener("click", () => {
    resetLicenseFilters();
    licenseState.page = 1;
    loadLicenses();
  });
  document.getElementById("licensePageSizeSelect").addEventListener("change", (event) => {
    licenseState.pageSize = Number.parseInt(event.target.value || "10", 10);
    licenseState.page = 1;
    loadLicenses();
  });
  document.getElementById("refreshLicenseBtn").addEventListener("click", () => loadLicenses({ preserveSelection: true }));
  document.getElementById("exportLicenseBtn").addEventListener("click", exportLicenses);
  document.getElementById("licenseFirstPageBtn").addEventListener("click", () => changeLicensePage(1));
  document.getElementById("licensePrevPageBtn").addEventListener("click", () => changeLicensePage(licenseState.page - 1));
  document.getElementById("licenseNextPageBtn").addEventListener("click", () => changeLicensePage(licenseState.page + 1));
  document.getElementById("licenseLastPageBtn").addEventListener("click", () => changeLicensePage(licenseState.pages));
  document.getElementById("createLicenseBtn").addEventListener("click", createLicense);
  document.getElementById("resetLicenseFormBtn").addEventListener("click", () => resetLicenseForm());
  document.getElementById("licenseTableBody").addEventListener("click", handleLicenseTableClick);
  document.getElementById("licenseTableBody").addEventListener("change", handleLicenseTableChange);
  document.getElementById("licenseActivationsBody").addEventListener("click", handleLicenseActivationTableClick);
  document.getElementById("copyLicenseSecretBtn").addEventListener("click", copyCurrentLicenseSecret);
  document.getElementById("licenseSelectAll").addEventListener("change", toggleSelectAllLicenses);
  document.getElementById("batchEnableLicenseBtn").addEventListener("click", batchEnableLicenses);
  document.getElementById("batchDisableLicenseBtn").addEventListener("click", batchDisableLicenses);
  document.getElementById("batchDeleteLicenseBtn").addEventListener("click", batchDeleteLicenses);
  document.getElementById("batchRestoreLicenseBtn").addEventListener("click", batchRestoreLicenses);
  document.querySelector("#licenseTableBody")?.closest("table")?.querySelector("thead")?.addEventListener("click", handleLicenseSortClick);
}

function updateLicenseFiltersFromInputs() {
  licenseState.filters.keyword = document.getElementById("licenseKeywordInput").value.trim();
  licenseState.filters.status = document.getElementById("licenseStatusFilter").value;
  licenseState.filters.edition = document.getElementById("licenseEditionFilter").value;
  licenseState.filters.show_deleted = document.getElementById("licenseShowDeletedCheckbox").checked ? "1" : "";
}

function resetLicenseFilters() {
  document.getElementById("licenseKeywordInput").value = "";
  document.getElementById("licenseStatusFilter").value = "";
  document.getElementById("licenseEditionFilter").value = "";
  document.getElementById("licenseShowDeletedCheckbox").checked = false;
  updateLicenseFiltersFromInputs();
}

function changeLicensePage(target) {
  if (target < 1 || target > licenseState.pages || target === licenseState.page) return;
  licenseState.page = target;
  loadLicenses();
}

async function loadLicenses({ preserveSelection = false } = {}) {
  const params = buildLicenseParams({ includePage: true });
  try {
    const data = await requestJSON(`/api/licenses?${params.toString()}`);
    if (!data) return;

    licenseState.total = data.total || 0;
    licenseState.pages = data.pages || 1;
    if (licenseState.page > licenseState.pages) {
      licenseState.page = licenseState.pages;
      await loadLicenses({ preserveSelection });
      return;
    }

    currentLicenseItems = data.items || [];
    resetSelection();
    renderLicenseRows(currentLicenseItems);
    updateLicensePaginationInfo();
    updateLicenseBatchButtons();

    const selectedStillVisible = currentLicenseItems.some((item) => item.id === currentLicenseId);
    if (preserveSelection && selectedStillVisible && currentLicenseId) {
      await loadLicenseActivations(currentLicenseId, { silent: true });
      return;
    }
    if (!selectedStillVisible) {
      clearLicenseActivations();
    }
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function renderLicenseRows(items) {
  const tbody = document.getElementById("licenseTableBody");
  tbody.innerHTML = "";

  if (!items.length) {
    renderEmptyState(tbody, 9, "暂无符合条件的授权码");
    return;
  }

  items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.dataset.id = String(item.id);
    if (item.id === currentLicenseId) {
      tr.classList.add("license-row-active");
    }

    const canDelete = canDeleteLicense(item);
    const isDeleted = Boolean(item.deleted_at);
    const actionButtons = [
      `<button class="btn btn-sm btn-outline-dark" data-action="view-secret" data-id="${item.id}">完整码</button>`,
      `<button class="btn btn-sm btn-outline-primary" data-action="view-activations" data-id="${item.id}">查看设备</button>`,
    ];

    if (isDeleted) {
      actionButtons.push(
        `<button class="btn btn-sm btn-outline-success" data-action="restore-license" data-id="${item.id}" data-name="${escapeHtml(item.license_key_masked || "该授权码")}">恢复</button>`
      );
    } else {
      actionButtons.push(
        item.status === "active"
          ? `<button class="btn btn-sm btn-outline-warning" data-action="disable-license" data-id="${item.id}">停用</button>`
          : `<button class="btn btn-sm btn-outline-success" data-action="enable-license" data-id="${item.id}">启用</button>`
      );
      actionButtons.push(
        canDelete
          ? `<button class="btn btn-sm btn-outline-danger" data-action="delete-license" data-id="${item.id}" data-name="${escapeHtml(item.license_key_masked || "该授权码")}">删除</button>`
          : `<button class="btn btn-sm btn-outline-danger" disabled title="请先停用并解绑所有设备后删除">删除</button>`
      );
    }

    tr.innerHTML = `
      <td class="text-center">
        <input
          type="checkbox"
          class="form-check-input license-row-checkbox"
          data-id="${item.id}"
          ${selectedLicenseIds.has(item.id) ? "checked" : ""}
        />
      </td>
      <td class="font-monospace">${escapeHtml(item.license_key_masked || "-")}</td>
      <td title="${escapeHtml(item.licensee || "")}">${escapeHtml(truncateText(item.licensee || "-", 14))}</td>
      <td>${escapeHtml(item.edition || "-")}</td>
      <td>${item.active_activations || 0}/${item.max_activations || 0}</td>
      <td>${escapeHtml(item.expires_at || "永久")}</td>
      <td>${escapeHtml(item.last_verified_at || "-")}</td>
      <td>${buildLicenseStatusBadge(item)}</td>
      <td class="license-actions-cell">${actionButtons.join("")}</td>
    `;
    tbody.appendChild(tr);
  });

  syncLicenseSelectAllState();
}

function updateLicensePaginationInfo() {
  document.getElementById("licensePageInfo").textContent = `第 ${licenseState.page} 页 / 共 ${licenseState.pages} 页，共 ${licenseState.total} 条`;
  document.getElementById("licensePageSizeSelect").value = String(licenseState.pageSize);
}

function updateLicenseSortIcons() {
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    const icon = th.querySelector(".sort-icon");
    if (!icon) return;
    if (th.dataset.sort === licenseState.sortBy) {
      icon.textContent = licenseState.sortDir === "asc" ? "↑" : "↓";
      th.classList.add("sort-active");
    } else {
      icon.textContent = "⇅";
      th.classList.remove("sort-active");
    }
  });
}

function buildLicenseStatusBadge(item) {
  if (item.deleted_at) {
    return '<span class="badge bg-danger-subtle text-danger-emphasis">已删除</span>';
  }
  const statusMap = {
    active: { text: "启用", cls: "bg-success" },
    disabled: { text: "停用", cls: "bg-secondary" },
    expired: { text: "过期", cls: "bg-warning text-dark" },
  };
  const meta = statusMap[item.status] || { text: item.status || "-", cls: "bg-secondary" };
  return `<span class="badge ${meta.cls}">${meta.text}</span>`;
}

function buildLicenseParams({ includePage = false } = {}) {
  const params = new URLSearchParams();
  if (includePage) {
    params.set("page", String(licenseState.page));
    params.set("page_size", String(licenseState.pageSize));
  }
  params.set("sort_by", licenseState.sortBy);
  params.set("sort_dir", licenseState.sortDir);
  Object.entries(licenseState.filters).forEach(([key, value]) => {
    if (value) {
      params.set(key, value);
    }
  });
  return params;
}

function handleLicenseSortClick(event) {
  const th = event.target.closest("th[data-sort]");
  if (!th) return;
  const field = th.dataset.sort;
  if (!field) return;
  if (licenseState.sortBy === field) {
    licenseState.sortDir = licenseState.sortDir === "asc" ? "desc" : "asc";
  } else {
    licenseState.sortBy = field;
    licenseState.sortDir = "asc";
  }
  licenseState.page = 1;
  updateLicenseSortIcons();
  loadLicenses({ preserveSelection: true });
}

function exportLicenses() {
  updateLicenseFiltersFromInputs();
  const params = buildLicenseParams();
  const query = params.toString();
  window.location.href = query ? `/api/licenses/export?${query}` : "/api/licenses/export";
}

async function createLicense() {
  const payload = {
    license_key: document.getElementById("licenseKeyInput").value.trim(),
    licensee: document.getElementById("licenseeInput").value.trim(),
    edition: document.getElementById("licenseEditionInput").value,
    max_activations: Number.parseInt(document.getElementById("licenseMaxActivationsInput").value || "1", 10),
    expires_at: document.getElementById("licenseExpiresAtInput").value.trim(),
    notes: document.getElementById("licenseNotesInput").value.trim(),
  };

  try {
    const result = await requestJSON("/api/licenses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const createdKey = result?.item?.license_key || "";
    resetLicenseForm({ keepCreatedKey: true });
    document.getElementById("licenseCreateResult").hidden = !createdKey;
    document.getElementById("licenseCreatedKey").textContent = createdKey;
    showToast(`激活码创建成功：${result?.item?.license_key_masked || "已生成"}`, "success");
    licenseState.page = 1;
    await loadLicenses();
    document.getElementById("licenseeInput").focus();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function handleLicenseTableChange(event) {
  const checkbox = event.target.closest(".license-row-checkbox");
  if (!checkbox) return;
  const id = Number.parseInt(checkbox.dataset.id || "0", 10);
  if (!id) return;

  if (checkbox.checked) {
    selectedLicenseIds.add(id);
  } else {
    selectedLicenseIds.delete(id);
  }
  syncLicenseSelectAllState();
  updateLicenseBatchButtons();
}

function toggleSelectAllLicenses(event) {
  const checked = event.target.checked;
  document.querySelectorAll(".license-row-checkbox").forEach((checkbox) => {
    checkbox.checked = checked;
    const id = Number.parseInt(checkbox.dataset.id || "0", 10);
    if (!id) return;
    if (checked) {
      selectedLicenseIds.add(id);
    } else {
      selectedLicenseIds.delete(id);
    }
  });
  updateLicenseBatchButtons();
}

function syncLicenseSelectAllState() {
  const selectAll = document.getElementById("licenseSelectAll");
  const checkboxes = Array.from(document.querySelectorAll(".license-row-checkbox"));
  if (!checkboxes.length) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
    return;
  }
  const checkedCount = checkboxes.filter((checkbox) => checkbox.checked).length;
  selectAll.checked = checkedCount > 0 && checkedCount === checkboxes.length;
  selectAll.indeterminate = checkedCount > 0 && checkedCount < checkboxes.length;
}

function updateLicenseBatchButtons() {
  const selectedItems = getSelectedLicenseItems();
  const enableableCount = selectedItems.filter((item) => canEnableLicense(item)).length;
  const disableableCount = selectedItems.filter((item) => canDisableLicense(item)).length;
  const deletableCount = selectedItems.filter((item) => canDeleteLicense(item)).length;
  const restorableCount = selectedItems.filter((item) => Boolean(item.deleted_at)).length;

  document.getElementById("licenseSelectionInfo").textContent = `已选择 ${selectedItems.length} 条`;
  document.getElementById("batchEnableLicenseBtn").disabled = enableableCount === 0;
  document.getElementById("batchDisableLicenseBtn").disabled = disableableCount === 0;
  document.getElementById("batchDeleteLicenseBtn").disabled = deletableCount === 0;
  document.getElementById("batchRestoreLicenseBtn").disabled = restorableCount === 0;
}

function getSelectedLicenseItems() {
  return currentLicenseItems.filter((item) => selectedLicenseIds.has(item.id));
}

function resetSelection() {
  selectedLicenseIds.clear();
  document.getElementById("licenseSelectAll").checked = false;
  document.getElementById("licenseSelectAll").indeterminate = false;
}

function canDeleteLicense(item) {
  return !item.deleted_at && item.status !== "active" && Number(item.active_activations || 0) === 0;
}

function canEnableLicense(item) {
  return !item.deleted_at && item.status !== "active";
}

function canDisableLicense(item) {
  return !item.deleted_at && item.status !== "disabled";
}

async function batchEnableLicenses() {
  const ids = getSelectedLicenseItems()
    .filter((item) => canEnableLicense(item))
    .map((item) => item.id);
  if (!ids.length) {
    showToast("请选择可启用的授权码", "warning");
    return;
  }
  if (!confirm(`确定批量启用 ${ids.length} 条授权码吗？`)) return;

  try {
    const result = await requestJSON("/api/licenses/batch-enable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    showToast(result?.message || "批量启用成功", "success");
    await loadLicenses({ preserveSelection: true });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function batchDisableLicenses() {
  const ids = getSelectedLicenseItems()
    .filter((item) => canDisableLicense(item))
    .map((item) => item.id);
  if (!ids.length) {
    showToast("请选择可停用的授权码", "warning");
    return;
  }
  if (!confirm(`确定批量停用 ${ids.length} 条授权码吗？`)) return;

  try {
    const result = await requestJSON("/api/licenses/batch-disable", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    showToast(result?.message || "批量停用成功", "success");
    await loadLicenses({ preserveSelection: true });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function batchDeleteLicenses() {
  const ids = getSelectedLicenseItems()
    .filter((item) => canDeleteLicense(item))
    .map((item) => item.id);
  if (!ids.length) {
    showToast("请选择可删除的授权码", "warning");
    return;
  }
  if (!confirm(`确定批量删除 ${ids.length} 条授权码吗？`)) return;

  try {
    const result = await requestJSON("/api/licenses/batch-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    showToast(result?.message || "批量删除成功", "success");
    if (ids.includes(currentLicenseId)) {
      clearLicenseActivations();
    }
    await loadLicenses();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function batchRestoreLicenses() {
  const ids = getSelectedLicenseItems()
    .filter((item) => Boolean(item.deleted_at))
    .map((item) => item.id);
  if (!ids.length) {
    showToast("请选择已删除的授权码", "warning");
    return;
  }
  if (!confirm(`确定恢复 ${ids.length} 条授权码吗？`)) return;

  try {
    const result = await requestJSON("/api/licenses/batch-restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids }),
    });
    showToast(result?.message || "批量恢复成功", "success");
    await loadLicenses({ preserveSelection: true });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleLicenseTableClick(event) {
  const target = event.target.closest("button[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  const id = Number.parseInt(target.dataset.id || "0", 10);
  if (!action || !id) return;

  try {
    if (action === "view-secret") {
      await viewLicenseSecret(id);
      return;
    }
    if (action === "view-activations") {
      await loadLicenseActivations(id);
      return;
    }
    if (action === "disable-license") {
      if (!confirm("确定停用这条激活码吗？")) return;
      await requestJSON(`/api/licenses/${id}/disable`, { method: "POST" });
      showToast("激活码已停用", "success");
      await loadLicenses({ preserveSelection: true });
      return;
    }
    if (action === "enable-license") {
      await requestJSON(`/api/licenses/${id}/enable`, { method: "POST" });
      showToast("激活码已启用", "success");
      await loadLicenses({ preserveSelection: true });
      return;
    }
    if (action === "delete-license") {
      const name = target.dataset.name || "该授权码";
      if (!confirm(`确定删除 ${name} 吗？删除后默认列表中将不再显示。`)) return;
      await requestJSON(`/api/licenses/${id}`, { method: "DELETE" });
      showToast("激活码已删除", "success");
      if (currentLicenseId === id) {
        clearLicenseActivations();
      }
      await loadLicenses();
      return;
    }
    if (action === "restore-license") {
      const name = target.dataset.name || "该授权码";
      if (!confirm(`确定恢复 ${name} 吗？`)) return;
      await requestJSON(`/api/licenses/${id}/restore`, { method: "POST" });
      showToast("激活码已恢复", "success");
      await loadLicenses({ preserveSelection: true });
    }
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function viewLicenseSecret(id) {
  try {
    const data = await requestJSON(`/api/licenses/${id}/secret`);
    if (!data) return;
    currentLicenseSecret = data.license_key || "";
    document.getElementById("licenseSecretValue").value = currentLicenseSecret;
    const statusText = data.deleted_at ? "已删除" : data.status || "-";
    document.getElementById("licenseSecretMeta").textContent =
      `授权对象：${data.licensee || "-"} ｜ 版本：${data.edition || "-"} ｜ 状态：${statusText}`;
    licenseSecretModal.show();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function copyCurrentLicenseSecret() {
  if (!currentLicenseSecret) {
    showToast("当前没有可复制的完整激活码", "warning");
    return;
  }
  try {
    await navigator.clipboard.writeText(currentLicenseSecret);
    showToast("完整激活码已复制到剪贴板", "success");
  } catch (error) {
    document.getElementById("licenseSecretValue").select();
    showToast("复制失败，请手动复制输入框中的激活码", "warning");
  }
}

async function loadLicenseActivations(licenseId, { silent = false } = {}) {
  try {
    const data = await requestJSON(`/api/licenses/${licenseId}/activations`);
    if (!data) return;
    currentLicenseId = licenseId;
    document.getElementById("licenseActivationsTitle").textContent =
      `当前激活码：${data.license?.license_key_masked || "-"} ｜ 状态：${data.license?.deleted_at ? "已删除" : data.license?.status || "-"}`;
    const tbody = document.getElementById("licenseActivationsBody");
    tbody.innerHTML = "";

    document.querySelectorAll("#licenseTableBody tr").forEach((row) => row.classList.remove("license-row-active"));
    const selectedRow = document.querySelector(`#licenseTableBody tr[data-id="${licenseId}"]`);
    selectedRow?.classList.add("license-row-active");

    if (!(data.items || []).length) {
      renderEmptyState(tbody, 7, "该授权码暂无设备绑定记录");
      return;
    }

    (data.items || []).forEach((item) => {
      const tr = document.createElement("tr");
      const active = !item.revoked_at;
      tr.innerHTML = `
        <td class="font-monospace" title="${escapeHtml(item.machine_id || "")}">${escapeHtml(truncateText(item.machine_id || "-", 18))}</td>
        <td>${escapeHtml(item.app_name || "-")}</td>
        <td>${escapeHtml(item.app_version || "-")}</td>
        <td>${escapeHtml(item.activated_at || "-")}</td>
        <td>${escapeHtml(item.last_verified_at || "-")}</td>
        <td>${active ? '<span class="badge bg-success">已绑定</span>' : '<span class="badge bg-secondary">已解绑</span>'}</td>
        <td>
          ${
            active && !data.license?.deleted_at
              ? `<button class="btn btn-sm btn-outline-danger" data-action="unbind-machine" data-machine-id="${escapeHtml(item.machine_id || "")}">解绑</button>`
              : "-"
          }
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (error) {
    if (!silent) {
      showToast(error.message, "danger");
    }
  }
}

async function handleLicenseActivationTableClick(event) {
  const target = event.target.closest("button[data-action]");
  if (!target || target.dataset.action !== "unbind-machine" || !currentLicenseId) return;
  const machineId = target.dataset.machineId;
  if (!machineId) return;
  if (!confirm(`确定解绑设备 ${machineId} 吗？`)) return;

  try {
    await requestJSON(`/api/licenses/${currentLicenseId}/unbind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machine_id: machineId }),
    });
    showToast("设备解绑成功", "success");
    await loadLicenseActivations(currentLicenseId, { silent: true });
    await loadLicenses({ preserveSelection: true });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function resetLicenseForm({ keepCreatedKey = false } = {}) {
  document.getElementById("licenseKeyInput").value = "";
  document.getElementById("licenseeInput").value = "";
  document.getElementById("licenseEditionInput").value = "pro";
  document.getElementById("licenseMaxActivationsInput").value = "1";
  document.getElementById("licenseExpiresAtInput").value = "";
  document.getElementById("licenseNotesInput").value = "";
  if (!keepCreatedKey) {
    document.getElementById("licenseCreateResult").hidden = true;
    document.getElementById("licenseCreatedKey").textContent = "";
  }
}

function clearLicenseActivations() {
  currentLicenseId = null;
  document.getElementById("licenseActivationsTitle").textContent = "请选择一条激活码查看";
  renderEmptyState(document.getElementById("licenseActivationsBody"), 7, "请选择一条激活码查看");
  document.querySelectorAll("#licenseTableBody tr").forEach((row) => row.classList.remove("license-row-active"));
}

function renderEmptyState(tbody, colSpan, message) {
  tbody.innerHTML = `<tr><td colspan="${colSpan}" class="text-center text-muted py-4">${escapeHtml(message)}</td></tr>`;
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
  if (response.status === 403) {
    showToast("权限不足", "danger");
    return null;
  }

  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  if (!response.ok) {
    const errorData = isJson ? await response.json().catch(() => ({})) : {};
    throw new Error(errorData.error || "请求失败");
  }
  return isJson ? response.json() : response;
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  const toastEl = document.createElement("div");
  const variantClasses = {
    success: ["text-bg-success", "text-white"],
    danger: ["text-bg-danger", "text-white"],
    warning: ["text-bg-warning", "text-dark"],
    info: ["text-bg-info", "text-dark"],
  };
  const classes = variantClasses[type] || variantClasses.info;
  toastEl.classList.add("toast", "align-items-center", "border-0", ...classes);
  toastEl.setAttribute("role", "alert");
  toastEl.setAttribute("aria-live", "assertive");
  toastEl.setAttribute("aria-atomic", "true");

  const wrapper = document.createElement("div");
  wrapper.className = "d-flex";
  const body = document.createElement("div");
  body.className = "toast-body";
  body.textContent = message;
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = `btn-close me-2 m-auto${classes.includes("text-white") ? " btn-close-white" : ""}`;
  closeBtn.setAttribute("data-bs-dismiss", "toast");
  closeBtn.setAttribute("aria-label", "Close");

  wrapper.appendChild(body);
  wrapper.appendChild(closeBtn);
  toastEl.appendChild(wrapper);
  container.appendChild(toastEl);

  const toast = new bootstrap.Toast(toastEl, { delay: 2500, autohide: true });
  toastEl.addEventListener("hidden.bs.toast", () => {
    toastEl.remove();
  });
  toast.show();
}

function truncateText(text, max = 30) {
  if (!text) return "-";
  const value = String(text);
  return value.length > max ? `${value.slice(0, max)}...` : value;
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
