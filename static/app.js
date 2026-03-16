const state = {
  page: 1,
  pageSize: 20,
  total: 0,
  pages: 0,
  currentItems: [],
  sortBy: "date",
  sortDir: "desc",
  filters: {
    search: "",
    company: "",
    review_passed: "",
    uploaded: "",
    date_from: "",
    date_to: "",
    hide_quick_add: "1",
  },
};

const selectedIds = new Set();
let editingId = null;
let deleteTargetId = null;
let deleteTargetName = "";

const dramaModal = new bootstrap.Modal(document.getElementById("dramaModal"));
const importModal = new bootstrap.Modal(document.getElementById("importModal"));
const deleteModal = new bootstrap.Modal(document.getElementById("deleteModal"));
const userModal = new bootstrap.Modal(document.getElementById("userModal"));
const changePasswordModal = new bootstrap.Modal(document.getElementById("changePasswordModal"));
const quickAddModal = new bootstrap.Modal(document.getElementById("quickAddModal"));
const licenseModal = new bootstrap.Modal(document.getElementById("licenseModal"));
const licenseSecretModal = new bootstrap.Modal(document.getElementById("licenseSecretModal"));
const remoteModal = new bootstrap.Modal(document.getElementById("remoteModal"));
let currentLicenseId = null;
let currentLicenseSecret = "";
let currentRemoteConversationId = null;
let remoteQrUnreadCount = 0;
let remoteNotificationsInitialized = false;
const seenRemoteMessageIds = new Set();
let remoteNotificationTimer = null;

async function initPage() {
  clearStaleModalBackdrop();
  cacheDefaultFeedbackMessages();
  applyRoleVisibility();
  bindEvents();
  await Promise.all([fetchCompanies(), loadDramas()]);
  updateSortIcons();
  startRemoteNotificationPolling();
}

document.addEventListener("DOMContentLoaded", initPage);

function clearStaleModalBackdrop() {
  document.body.classList.remove("modal-open");
  document.body.style.removeProperty("padding-right");
  document.body.style.removeProperty("overflow");
  document.querySelectorAll(".modal-backdrop").forEach((node) => node.remove());
  document.querySelectorAll(".modal.show").forEach((node) => {
    node.classList.remove("show");
    node.setAttribute("aria-hidden", "true");
    node.style.display = "none";
  });
}

function applyRoleVisibility() {
  if (window.currentUser?.role === "admin") {
    document.getElementById("adminActions").hidden = false;
  }
}

function bindEvents() {
  document.getElementById("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      document.getElementById("queryBtn").click();
    }
  });
  document.getElementById("queryBtn").addEventListener("click", () => {
    updateFiltersFromInputs();
    state.page = 1;
    loadDramas();
  });
  document.getElementById("resetBtn").addEventListener("click", () => {
    resetFilters();
    state.page = 1;
    loadDramas();
  });
  document.getElementById("pageSizeSelect").addEventListener("change", (e) => {
    state.pageSize = Number(e.target.value);
    state.page = 1;
    loadDramas();
  });
  document.getElementById("firstPageBtn").addEventListener("click", () => changePage(1));
  document.getElementById("prevPageBtn").addEventListener("click", () => changePage(state.page - 1));
  document.getElementById("nextPageBtn").addEventListener("click", () => changePage(state.page + 1));
  document.getElementById("lastPageBtn").addEventListener("click", () => changePage(state.pages));
  document.getElementById("addDramaBtn")?.addEventListener("click", openCreateModal);
  document.getElementById("saveDramaBtn").addEventListener("click", submitDramaForm);
  document.getElementById("dramaTableBody").addEventListener("click", handleTableClick);
  document.getElementById("selectAll").addEventListener("change", toggleSelectAll);
  document.getElementById("batchDeleteBtn")?.addEventListener("click", () => openDeleteModal("batch"));
  document.getElementById("confirmDeleteBtn").addEventListener("click", confirmDelete);
  document.getElementById("importBtn")?.addEventListener("click", handleImport);
  document.getElementById("importModal").addEventListener("hidden.bs.modal", () => {
    document.getElementById("importResult").hidden = true;
    document.getElementById("importFile").value = "";
    document.getElementById("conflictContainer").hidden = true;
    document.getElementById("conflictTableBody").innerHTML = "";
  });
  document.getElementById("dramaModal").addEventListener("hidden.bs.modal", () => {
    clearFormValidation(document.getElementById("dramaForm"));
  });
  document.getElementById("userModal").addEventListener("hidden.bs.modal", resetUserForm);
  document.getElementById("changePasswordModal").addEventListener("hidden.bs.modal", resetChangePasswordForm);
  document.getElementById("exportBtn").addEventListener("click", exportExcel);
  document.getElementById("userManageBtn")?.addEventListener("click", async () => {
    await loadUsers();
    userModal.show();
  });
  document.getElementById("licenseManageBtn")?.addEventListener("click", async () => {
    await loadLicenses();
    licenseModal.show();
  });
  document.getElementById("createUserBtn")?.addEventListener("click", createUser);
  document.getElementById("userTableBody")?.addEventListener("click", handleUserTableClick);
  document.getElementById("createLicenseBtn")?.addEventListener("click", createLicense);
  document.getElementById("refreshLicenseBtn")?.addEventListener("click", loadLicenses);
  document.getElementById("licenseTableBody")?.addEventListener("click", handleLicenseTableClick);
  document.getElementById("licenseActivationsBody")?.addEventListener("click", handleLicenseActivationTableClick);
  document.getElementById("copyLicenseSecretBtn")?.addEventListener("click", copyCurrentLicenseSecret);
  document.getElementById("changePasswordBtn")?.addEventListener("click", openChangePasswordModal);
  document.getElementById("confirmChangePasswordBtn")?.addEventListener("click", submitChangePassword);
  document.getElementById("quickAddBtn")?.addEventListener("click", openQuickAddModal);
  document.getElementById("quickAddSubmitBtn")?.addEventListener("click", handleQuickAdd);
  document.getElementById("quickAddModal").addEventListener("hidden.bs.modal", resetQuickAddModal);
  document.getElementById("licenseModal").addEventListener("hidden.bs.modal", resetLicensePanel);
  document.getElementById("remoteManageBtn")?.addEventListener("click", openRemoteModal);
  document.getElementById("refreshRemoteClientsBtn")?.addEventListener("click", loadRemoteClients);
  document.getElementById("createRemoteClientBtn")?.addEventListener("click", createRemoteClient);
  document.getElementById("remoteClientSelect")?.addEventListener("change", handleRemoteClientChange);
  document.getElementById("sendRemoteImportBtn")?.addEventListener("click", sendRemoteImportCommand);
  document.querySelector("thead")?.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const field = th.dataset.sort;
    if (state.sortBy === field) {
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
    } else {
      state.sortBy = field;
      state.sortDir = "asc";
    }
    state.page = 1;
    updateSortIcons();
    loadDramas();
  });
}

function updateFiltersFromInputs() {
  state.filters.search = document.getElementById("searchInput").value.trim();
  state.filters.company = document.getElementById("companySelect").value;
  state.filters.review_passed = document.getElementById("reviewSelect").value;
  state.filters.uploaded = document.getElementById("uploadedSelect").value;
  state.filters.date_from = document.getElementById("dateFrom").value;
  state.filters.date_to = document.getElementById("dateTo").value;
  state.filters.hide_quick_add = document.getElementById("hideQuickAdd").checked ? "1" : "";
}

function resetFilters() {
  document.getElementById("searchInput").value = "";
  document.getElementById("companySelect").value = "";
  document.getElementById("reviewSelect").value = "";
  document.getElementById("uploadedSelect").value = "";
  document.getElementById("dateFrom").value = "";
  document.getElementById("dateTo").value = "";
  document.getElementById("hideQuickAdd").checked = true;
  updateFiltersFromInputs();
}

function changePage(target) {
  if (target < 1 || target > state.pages || target === state.page) return;
  state.page = target;
  loadDramas();
}

async function loadDramas() {
  const params = new URLSearchParams({
    page: state.page,
    page_size: state.pageSize,
    sort_by: state.sortBy,
    sort_dir: state.sortDir,
  });
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  try {
    const data = await requestJSON(`/api/dramas?${params.toString()}`);
    if (!data) return;
    state.total = data.total;
    state.pages = data.pages || 1;
    renderDramas(data.items || []);
    updatePaginationInfo();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function updateSortIcons() {
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    const icon = th.querySelector(".sort-icon");
    if (!icon) return;
    if (th.dataset.sort === state.sortBy) {
      icon.textContent = state.sortDir === "asc" ? "鈫? : "鈫?;
      th.classList.add("sort-active");
    } else {
      icon.textContent = "鈬?;
      th.classList.remove("sort-active");
    }
  });
}

function renderDramas(items) {
  const tbody = document.getElementById("dramaTableBody");
  tbody.innerHTML = "";
  selectedIds.clear();
  document.getElementById("selectAll").checked = false;
  updateBatchButton();

  items.forEach((item, index) => {
    const rowNumber = (state.page - 1) * state.pageSize + index + 1;
    const tr = document.createElement("tr");
    tr.dataset.id = item.id;
    tr.innerHTML = `
      <td><input type="checkbox" class="row-checkbox" data-id="${item.id}" /></td>
      <th scope="row">${rowNumber}</th>
      <td>${escapeHtml(item.date || "-")}</td>
      <td>${escapeHtml(item.original_name || "-")}</td>
      <td>${escapeHtml(item.new_name || "-")}</td>
      <td>${item.episodes ?? "-"}</td>
      <td>${item.duration ?? "-"}</td>
      <td>${buildBadge(item.review_passed)}</td>
      <td>${buildBadge(item.uploaded)}</td>
      <td>${escapeHtml(item.materials || "-")}</td>
      <td>${escapeHtml(item.promo_text || "-")}</td>
      <td title="${escapeHtml(item.description || "鏆傛棤绠€浠?)}">${escapeHtml(truncateText(item.description))}</td>
      <td>${escapeHtml(item.company || "-")}</td>
      <td>${escapeHtml(item.uploader || "-")}</td>
      <td title="${escapeHtml(item.remark1 || "")}">${escapeHtml(truncateText(item.remark1))}</td>
      <td title="${escapeHtml(item.remark2 || "")}">${escapeHtml(truncateText(item.remark2))}</td>
      <td title="${escapeHtml(item.remark3 || "")}">${escapeHtml(truncateText(item.remark3))}</td>
      <td>${buildActions(item)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function buildBadge(flag) {
  const isPositive = flag === "鏄?;
  const cls = isPositive ? "bg-success" : "bg-secondary";
  const text = isPositive ? "鏄? : "鍚?;
  return `<span class="badge ${cls}">${text}</span>`;
}

function buildActions(item) {
  const uploadBtn = `<button class="btn btn-sm btn-outline-success me-1" data-action="toggle-upload" data-id="${item.id}">${item.uploaded === "鏄? ? "鍙栨秷涓婁紶" : "鏍囪涓婁紶"}</button>`;
  if (window.currentUser?.role !== "admin") {
    return uploadBtn;
  }
  return `
    <button class="btn btn-sm btn-outline-primary me-1" data-action="edit" data-id="${item.id}">缂栬緫</button>
    <button class="btn btn-sm btn-outline-danger me-1" data-action="delete" data-id="${item.id}" data-name="${escapeHtml(item.new_name || item.original_name || "鐭墽")}">鍒犻櫎</button>
    ${uploadBtn}
  `;
}

function truncateText(text, max = 30) {
  if (!text) return "-";
  const str = String(text);
  return str.length > max ? `${str.slice(0, max)}...` : str;
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

function updatePaginationInfo() {
  document.getElementById("pageInfo").textContent = `绗?{state.page}椤?鍏?{state.pages}椤碉紝鍏?{state.total}鏉;
  document.getElementById("pageSizeSelect").value = state.pageSize;
}

async function fetchCompanies() {
  try {
    const companies = await requestJSON("/api/companies");
    if (!companies) return;
    const select = document.getElementById("companySelect");
    select.innerHTML = '<option value="">鍏ㄩ儴</option>';
    companies.forEach((company) => {
      const option = document.createElement("option");
      option.value = company;
      option.textContent = company;
      select.appendChild(option);
    });
  } catch (error) {
    console.error(error);
  }
}

function openCreateModal() {
  editingId = null;
  document.getElementById("dramaModalLabel").textContent = "鏂板鐭墽";
  document.getElementById("dramaForm").reset();
  clearFormValidation(document.getElementById("dramaForm"));
  document.getElementById("dramaId").value = "";
  document.getElementById("reviewInput").value = "鍚?;
  document.getElementById("remark1Input").value = "";
  document.getElementById("remark2Input").value = "";
  document.getElementById("remark3Input").value = "";
  document.getElementById("uploadedInput").value = "鍚?;
  dramaModal.show();
}

function handleTableClick(event) {
  const { target } = event;
  if (target.matches(".row-checkbox")) {
    const id = Number(target.dataset.id);
    if (target.checked) {
      selectedIds.add(id);
    } else {
      selectedIds.delete(id);
      document.getElementById("selectAll").checked = false;
    }
    updateBatchButton();
    return;
  }
  const action = target.dataset.action;
  if (!action) return;
  const id = Number(target.dataset.id);
  if (action === "edit") {
    openEditModal(id);
  } else if (action === "delete") {
    deleteTargetId = id;
    deleteTargetName = target.dataset.name || "璇ョ煭鍓?;
    document.getElementById("deleteMessage").textContent = `纭畾鍒犻櫎銆?{deleteTargetName}銆嬪悧锛焋;
    deleteModal.show();
  } else if (action === "toggle-upload") {
    toggleUpload(id);
  }
}

async function openEditModal(id) {
  try {
    const data = await requestJSON(`/api/dramas?page=1&page_size=1&sort_by=id&sort_dir=asc&id=${id}`);
    const item = data?.items?.find((drama) => drama.id === id);
    if (!item) {
      showToast("鏈壘鍒拌鐭墽", "warning");
      return;
    }
    editingId = id;
    document.getElementById("dramaModalLabel").textContent = "缂栬緫鐭墽";
    document.getElementById("dramaId").value = id;
    clearFormValidation(document.getElementById("dramaForm"));
    document.getElementById("dateInput").value = item.date || "";
    document.getElementById("originalNameInput").value = item.original_name || "";
    document.getElementById("newNameInput").value = item.new_name || "";
    document.getElementById("episodesInput").value = item.episodes ?? "";
    document.getElementById("durationInput").value = item.duration ?? "";
    document.getElementById("reviewInput").value = item.review_passed || "鍚?;
    document.getElementById("uploadedInput").value = item.uploaded || "鍚?;
    document.getElementById("materialsInput").value = item.materials || "";
    document.getElementById("promoTextInput").value = item.promo_text || "";
    document.getElementById("descriptionInput").value = item.description || "";
    document.getElementById("companyInput").value = item.company || "";
    document.getElementById("remark1Input").value = item.remark1 || "";
    document.getElementById("remark2Input").value = item.remark2 || "";
    document.getElementById("remark3Input").value = item.remark3 || "";
    dramaModal.show();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function submitDramaForm() {
  if (window.currentUser?.role !== "admin") {
    showToast("鏉冮檺涓嶈冻", "danger");
    return;
  }
  const dateInput = document.getElementById("dateInput");
  const originalNameInput = document.getElementById("originalNameInput");
  const newNameInput = document.getElementById("newNameInput");
  const episodesInput = document.getElementById("episodesInput");
  const durationInput = document.getElementById("durationInput");
  const companyInput = document.getElementById("companyInput");

  const values = {
    date: dateInput.value.trim(),
    originalName: originalNameInput.value.trim(),
    newName: newNameInput.value.trim(),
    episodes: episodesInput.value.trim(),
    duration: durationInput.value.trim(),
    review_passed: document.getElementById("reviewInput").value,
    uploaded: document.getElementById("uploadedInput").value,
    materials: document.getElementById("materialsInput").value.trim(),
    promo_text: document.getElementById("promoTextInput").value.trim(),
    description: document.getElementById("descriptionInput").value.trim(),
    company: companyInput.value.trim(),
    remark1: document.getElementById("remark1Input").value.trim(),
    remark2: document.getElementById("remark2Input").value.trim(),
    remark3: document.getElementById("remark3Input").value.trim(),
  };

  let isValid = true;
  let firstInvalid = null;
  const validations = [
    {
      input: dateInput,
      validator: () => Boolean(values.date) && /^\d{4}-\d{2}-\d{2}$/.test(values.date),
    },
    {
      input: originalNameInput,
      validator: () => values.originalName.length > 0 && values.originalName.length <= 100,
    },
    {
      input: newNameInput,
      validator: () => values.newName.length > 0 && values.newName.length <= 100,
    },
    {
      input: episodesInput,
      validator: () => {
        const num = Number(values.episodes);
        return Number.isInteger(num) && num >= 1 && num <= 9999;
      },
      message: "璇疯緭鍏?-9999涔嬮棿鐨勬鏁存暟",
    },
    {
      input: durationInput,
      validator: () => {
        const num = Number(values.duration);
        return Number.isInteger(num) && num >= 1 && num <= 99999;
      },
      message: "璇疯緭鍏?-99999涔嬮棿鐨勬鏁存暟",
    },
    {
      input: companyInput,
      validator: () => values.company.length > 0 && values.company.length <= 100,
    },
  ];

  validations.forEach(({ input, validator, message }) => {
    const valid = validateField(input, validator, message);
    if (!valid) {
      isValid = false;
      if (!firstInvalid) {
        firstInvalid = input;
      }
    }
  });

  if (!isValid) {
    firstInvalid?.focus();
    return;
  }

  const payload = {
    date: values.date,
    original_name: values.originalName,
    new_name: values.newName,
    episodes: Number.parseInt(values.episodes, 10),
    duration: Number.parseInt(values.duration, 10),
    review_passed: values.review_passed,
    uploaded: values.uploaded,
    materials: values.materials || null,
    promo_text: values.promo_text || null,
    description: values.description || null,
    company: values.company,
    remark1: values.remark1 || null,
    remark2: values.remark2 || null,
    remark3: values.remark3 || null,
  };
  try {
    const method = editingId ? "PUT" : "POST";
    const url = editingId ? `/api/dramas/${editingId}` : "/api/dramas";
    await requestJSON(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    dramaModal.hide();
    showToast(editingId ? "鐭墽鏇存柊鎴愬姛" : "鐭墽鏂板鎴愬姛", "success");
    loadDramas();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function toggleSelectAll(event) {
  const checked = event.target.checked;
  document.querySelectorAll(".row-checkbox").forEach((checkbox) => {
    checkbox.checked = checked;
    const id = Number(checkbox.dataset.id);
    if (checked) {
      selectedIds.add(id);
    } else {
      selectedIds.delete(id);
    }
  });
  updateBatchButton();
}

function updateBatchButton() {
  const btn = document.getElementById("batchDeleteBtn");
  if (!btn) return;
  btn.disabled = selectedIds.size === 0;
}

function openDeleteModal(mode) {
  if (mode === "batch") {
    deleteTargetId = null;
    document.getElementById("deleteMessage").textContent = `纭畾鍒犻櫎閫変腑鐨?{selectedIds.size}鏉＄煭鍓у悧锛焋;
    deleteModal.show();
  }
}

async function confirmDelete() {
  const isBatch = !deleteTargetId;
  try {
    if (deleteTargetId) {
      await requestJSON(`/api/dramas/${deleteTargetId}`, { method: "DELETE" });
    } else {
      await requestJSON("/api/dramas/batch-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids: Array.from(selectedIds) }),
      });
    }
    deleteModal.hide();
    deleteTargetId = null;
    selectedIds.clear();
    showToast(isBatch ? "鎵归噺鍒犻櫎瀹屾垚" : "鍒犻櫎鎴愬姛", "success");
    loadDramas();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleImport() {
  const fileInput = document.getElementById("importFile");
  const file = fileInput.files[0];
  if (!file) {
    showToast("璇峰厛閫夋嫨Excel鏂囦欢", "warning");
    return;
  }
  const fileName = file.name.toLowerCase();
  if (!fileName.endsWith(".xlsx") && !fileName.endsWith(".xls")) {
    showToast("璇烽€夋嫨 .xlsx 鎴?.xls 鏍煎紡鐨勬枃浠?, "warning");
    return;
  }
  const formData = new FormData();
  formData.append("file", file);
  try {
    const response = await requestJSON("/api/import", {
      method: "POST",
      body: formData,
    });
    if (!response) return;
    document.getElementById("importResult").hidden = false;
    document.getElementById("newCount").textContent = response.new_count;
    document.getElementById("duplicateCount").textContent = response.duplicate_count;
    const conflictBody = document.getElementById("conflictTableBody");
    conflictBody.innerHTML = "";
    if (response.conflicts && response.conflicts.length) {
      document.getElementById("conflictContainer").hidden = false;
      response.conflicts.forEach((c) => {
        const row = document.createElement("tr");
        row.innerHTML = `
          <td>${escapeHtml(c.original_name)}</td>
          <td>${escapeHtml(c.new_name)}</td>
          <td>${escapeHtml(c.existing_new_name)}</td>
        `;
        conflictBody.appendChild(row);
      });
    } else {
      document.getElementById("conflictContainer").hidden = true;
    }
    showToast(`瀵煎叆瀹屾垚锛氭柊澧?${response.new_count} 鏉★紝閲嶅 ${response.duplicate_count} 鏉, "success");
    await loadDramas();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function exportExcel() {
  updateFiltersFromInputs();
  const params = new URLSearchParams(state.filters);
  params.set("page_size", state.pageSize);
  params.set("page", state.page);
  const query = Array.from(params.entries())
    .filter(([, value]) => value)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("&");
  window.location.href = `/api/export${query ? `?${query}` : ""}`;
}

async function toggleUpload(id) {
  try {
    const result = await requestJSON(`/api/dramas/${id}/upload`, { method: "PATCH" });
    if (!result) return;
    showToast(result.uploaded === "鏄? ? "宸叉爣璁颁负宸蹭笂浼? : "宸插彇娑堜笂浼犳爣璁?, "success");
    loadDramas();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function loadUsers() {
  try {
    const users = await requestJSON("/api/users");
    if (!users) return;
    const tbody = document.getElementById("userTableBody");
    tbody.innerHTML = "";
    users.forEach((user) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(user.username)}</td>
        <td>${user.role === "admin" ? "绠＄悊鍛? : "鏅€氱敤鎴?}</td>
        <td>
          <button class="btn btn-sm btn-outline-danger" data-action="delete-user" data-id="${user.id}" data-username="${escapeHtml(user.username)}" ${user.username === window.currentUser.username ? "disabled" : ""}>鍒犻櫎</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function loadLicenses() {
  try {
    const data = await requestJSON("/api/licenses?page=1&page_size=50");
    const items = Array.isArray(data) ? data : data?.items || [];
    if (!items) return;
    const tbody = document.getElementById("licenseTableBody");
    tbody.innerHTML = "";
    currentLicenseId = null;
    clearLicenseActivations();
    items.forEach((item) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="font-monospace">${escapeHtml(item.license_key_masked || "-")}</td>
        <td>${escapeHtml(item.licensee || "-")}</td>
        <td>${escapeHtml(item.edition || "-")}</td>
        <td>${item.active_activations || 0}/${item.max_activations || 0}</td>
        <td>${escapeHtml(item.expires_at || "姘镐箙")}</td>
        <td>${buildLicenseStatusBadge(item.status)}</td>
        <td>
          <button class="btn btn-sm btn-outline-dark me-1" data-action="view-secret" data-id="${item.id}">瀹屾暣鐮?/button>
          <button class="btn btn-sm btn-outline-primary me-1" data-action="view-activations" data-id="${item.id}">鏌ョ湅璁惧</button>
          ${
            item.status === "active"
              ? `<button class="btn btn-sm btn-outline-warning" data-action="disable-license" data-id="${item.id}">鍋滅敤</button>`
              : `<button class="btn btn-sm btn-outline-success" data-action="enable-license" data-id="${item.id}">鍚敤</button>`
          }
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function buildLicenseStatusBadge(status) {
  const statusMap = {
    active: { text: "鍚敤", cls: "bg-success" },
    disabled: { text: "鍋滅敤", cls: "bg-secondary" },
    expired: { text: "杩囨湡", cls: "bg-warning text-dark" },
  };
  const meta = statusMap[status] || { text: status || "-", cls: "bg-secondary" };
  return `<span class="badge ${meta.cls}">${meta.text}</span>`;
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
    document.getElementById("licenseCreateResult").hidden = !createdKey;
    document.getElementById("licenseCreatedKey").textContent = createdKey;
    showToast(`婵€娲荤爜鍒涘缓鎴愬姛锛?{result?.item?.license_key_masked || "宸茬敓鎴?}`, "success");
    document.getElementById("licenseKeyInput").value = "";
    document.getElementById("licenseeInput").focus();
    await loadLicenses();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleLicenseTableClick(event) {
  const { target } = event;
  const action = target.dataset.action;
  if (!action) return;
  const id = Number(target.dataset.id);
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
      if (!confirm("纭畾鍋滅敤杩欐潯婵€娲荤爜鍚楋紵")) return;
      await requestJSON(`/api/licenses/${id}/disable`, { method: "POST" });
      showToast("婵€娲荤爜宸插仠鐢?, "success");
      await loadLicenses();
      return;
    }
    if (action === "enable-license") {
      await requestJSON(`/api/licenses/${id}/enable`, { method: "POST" });
      showToast("婵€娲荤爜宸插惎鐢?, "success");
      await loadLicenses();
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
    document.getElementById("licenseSecretMeta").textContent =
      `鎺堟潈瀵硅薄锛?{data.licensee || "-"} 锝?鐗堟湰锛?{data.edition || "-"} 锝?鐘舵€侊細${data.status || "-"}`;
    licenseSecretModal.show();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function copyCurrentLicenseSecret() {
  if (!currentLicenseSecret) {
    showToast("褰撳墠娌℃湁鍙鍒剁殑瀹屾暣婵€娲荤爜", "warning");
    return;
  }
  try {
    await navigator.clipboard.writeText(currentLicenseSecret);
    showToast("瀹屾暣婵€娲荤爜宸插鍒跺埌鍓创鏉?, "success");
  } catch (error) {
    document.getElementById("licenseSecretValue").select();
    showToast("澶嶅埗澶辫触锛岃鎵嬪姩澶嶅埗杈撳叆妗嗕腑鐨勬縺娲荤爜", "warning");
  }
}

async function loadLicenseActivations(licenseId) {
  try {
    const data = await requestJSON(`/api/licenses/${licenseId}/activations`);
    if (!data) return;
    currentLicenseId = licenseId;
    document.getElementById("licenseActivationsTitle").textContent =
      `褰撳墠婵€娲荤爜锛?{data.license?.license_key_masked || "-"}`;
    const tbody = document.getElementById("licenseActivationsBody");
    tbody.innerHTML = "";
    (data.items || []).forEach((item) => {
      const tr = document.createElement("tr");
      const active = !item.revoked_at;
      tr.innerHTML = `
        <td class="font-monospace" title="${escapeHtml(item.machine_id || "")}">${escapeHtml(truncateText(item.machine_id, 18))}</td>
        <td>${escapeHtml(item.app_name || "-")}</td>
        <td>${escapeHtml(item.app_version || "-")}</td>
        <td>${escapeHtml(item.activated_at || "-")}</td>
        <td>${escapeHtml(item.last_verified_at || "-")}</td>
        <td>${active ? '<span class="badge bg-success">宸茬粦瀹?/span>' : '<span class="badge bg-secondary">宸茶В缁?/span>'}</td>
        <td>
          ${
            active
              ? `<button class="btn btn-sm btn-outline-danger" data-action="unbind-machine" data-machine-id="${escapeHtml(item.machine_id || "")}">瑙ｇ粦</button>`
              : "-"
          }
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleLicenseActivationTableClick(event) {
  const { target } = event;
  const action = target.dataset.action;
  if (!action || action !== "unbind-machine" || !currentLicenseId) return;
  const machineId = target.dataset.machineId;
  if (!confirm(`纭畾瑙ｇ粦璁惧 ${machineId} 鍚楋紵`)) return;
  try {
    await requestJSON(`/api/licenses/${currentLicenseId}/unbind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ machine_id: machineId }),
    });
    showToast("璁惧瑙ｇ粦鎴愬姛", "success");
    await loadLicenseActivations(currentLicenseId);
    await loadLicenses();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function resetLicenseForm() {
  document.getElementById("licenseKeyInput").value = "";
  document.getElementById("licenseeInput").value = "";
  document.getElementById("licenseEditionInput").value = "pro";
  document.getElementById("licenseMaxActivationsInput").value = "1";
  document.getElementById("licenseExpiresAtInput").value = "";
  document.getElementById("licenseNotesInput").value = "";
  document.getElementById("licenseCreateResult").hidden = true;
  document.getElementById("licenseCreatedKey").textContent = "";
}

function clearLicenseActivations() {
  document.getElementById("licenseActivationsTitle").textContent = "璇烽€夋嫨涓€鏉℃縺娲荤爜鏌ョ湅";
  document.getElementById("licenseActivationsBody").innerHTML = "";
}

function resetLicensePanel() {
  resetLicenseForm();
  clearLicenseActivations();
  currentLicenseId = null;
}

async function createUser() {
  const usernameInput = document.getElementById("userNameInput");
  const passwordInput = document.getElementById("userPasswordInput");
  const roleInput = document.getElementById("userRoleInput");
  const username = usernameInput.value.trim();
  const password = passwordInput.value.trim();
  const role = roleInput.value;

  let isValid = true;
  let firstInvalid = null;
  const usernameValid = /^[A-Za-z0-9_]{2,30}$/.test(username);
  const passwordValid = password.length >= 6;

  if (!validateField(usernameInput, () => usernameValid)) {
    isValid = false;
    firstInvalid = firstInvalid || usernameInput;
  }
  if (!validateField(passwordInput, () => passwordValid)) {
    isValid = false;
    firstInvalid = firstInvalid || passwordInput;
  }
  if (!role) {
    showToast("璇烽€夋嫨瑙掕壊", "warning");
    isValid = false;
  }
  if (!isValid) {
    firstInvalid?.focus();
    return;
  }
  try {
    await requestJSON("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password, role }),
    });
    showToast("鏂板鐢ㄦ埛鎴愬姛", "success");
    resetUserForm();
    await loadUsers();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleUserTableClick(event) {
  const { target } = event;
  if (!target.dataset.action) return;
  const id = Number(target.dataset.id);
  const username = target.dataset.username;
  if (target.dataset.action === "delete-user") {
    if (!confirm(`纭畾鍒犻櫎鐢ㄦ埛 ${username} 鍚楋紵`)) return;
    try {
      await requestJSON(`/api/users/${id}`, { method: "DELETE" });
      showToast("鐢ㄦ埛鍒犻櫎鎴愬姛", "success");
      await loadUsers();
    } catch (error) {
      showToast(error.message, "danger");
    }
  }
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
    showToast("鏉冮檺涓嶈冻", "danger");
    return null;
  }
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  if (!response.ok) {
    const errorData = isJson ? await response.json().catch(() => ({})) : {};
    throw new Error(errorData.error || "璇锋眰澶辫触");
  }
  return isJson ? response.json() : response;
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
  if (!container) {
    if (type === "danger") {
      console.error(message);
    } else {
      console.log(message);
    }
    return;
  }
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

function validateField(input, validator, message) {
  if (!input) return true;
  const isValid = typeof validator === "function" ? validator() : Boolean(validator);
  const feedback = getFeedbackElement(input, !isValid && Boolean(message));
  if (feedback && feedback.dataset.defaultText === undefined) {
    feedback.dataset.defaultText = feedback.textContent || "";
  }
  if (!isValid) {
    input.classList.add("is-invalid");
    if (feedback) {
      if (message) {
        feedback.textContent = message;
      } else if (feedback.dataset.defaultText !== undefined) {
        feedback.textContent = feedback.dataset.defaultText;
      }
    }
  } else {
    input.classList.remove("is-invalid");
    if (feedback && feedback.dataset.defaultText !== undefined) {
      feedback.textContent = feedback.dataset.defaultText;
    }
  }
  return isValid;
}

function getFeedbackElement(input, createIfMissing = false) {
  if (!input) return null;
  let node = input.nextElementSibling;
  while (node && !(node.classList && node.classList.contains("invalid-feedback"))) {
    node = node.nextElementSibling;
  }
  if (!node && createIfMissing) {
    node = document.createElement("div");
    node.className = "invalid-feedback";
    input.insertAdjacentElement("afterend", node);
  }
  return node;
}

function clearFormValidation(container) {
  if (!container) return;
  container.querySelectorAll(".is-invalid").forEach((el) => el.classList.remove("is-invalid"));
  container.querySelectorAll(".invalid-feedback").forEach((el) => {
    if (el.dataset.defaultText !== undefined) {
      el.textContent = el.dataset.defaultText;
    }
  });
}

function cacheDefaultFeedbackMessages() {
  document.querySelectorAll(".invalid-feedback").forEach((el) => {
    if (el.dataset.defaultText === undefined) {
      el.dataset.defaultText = el.textContent || "";
    }
  });
}

function resetUserForm() {
  document.getElementById("userNameInput").value = "";
  document.getElementById("userPasswordInput").value = "";
  clearFormValidation(document.getElementById("userModal"));
}

function resetChangePasswordForm() {
  document.getElementById("currentPasswordInput").value = "";
  document.getElementById("newPasswordInput").value = "";
  document.getElementById("confirmPasswordInput").value = "";
  clearFormValidation(document.getElementById("changePasswordModal"));
}

function openChangePasswordModal() {
  resetChangePasswordForm();
  changePasswordModal.show();
}

async function submitChangePassword() {
  const currentInput = document.getElementById("currentPasswordInput");
  const newInput = document.getElementById("newPasswordInput");
  const confirmInput = document.getElementById("confirmPasswordInput");
  const current = currentInput.value.trim();
  const nextPassword = newInput.value.trim();
  const confirm = confirmInput.value.trim();

  let isValid = true;
  let firstInvalid = null;

  if (!validateField(currentInput, () => current.length > 0)) {
    isValid = false;
    firstInvalid = firstInvalid || currentInput;
  }
  if (!validateField(newInput, () => nextPassword.length >= 6)) {
    isValid = false;
    firstInvalid = firstInvalid || newInput;
  }
  if (!validateField(confirmInput, () => confirm.length > 0 && confirm === nextPassword)) {
    isValid = false;
    firstInvalid = firstInvalid || confirmInput;
  }
  if (!isValid) {
    firstInvalid?.focus();
    return;
  }

  try {
    await requestJSON("/api/profile/password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        current_password: current,
        new_password: nextPassword,
      }),
    });
    showToast("瀵嗙爜淇敼鎴愬姛", "success");
    changePasswordModal.hide();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function openQuickAddModal() {
  resetQuickAddModal();
  quickAddModal.show();
}

function resetQuickAddModal() {
  document.getElementById("quickAddNames").value = "";
  document.getElementById("quickAddNames").classList.remove("is-invalid");
  document.getElementById("quickAddCompany").value = "";
  document.getElementById("quickAddResult").hidden = true;
  document.getElementById("quickAddDuplicatesSection").hidden = true;
  document.getElementById("quickAddDuplicatesList").innerHTML = "";
}

async function openRemoteModal() {
  clearRemoteUnreadNotifications();
  await loadRemoteClients();
  remoteModal.show();
}

function startRemoteNotificationPolling() {
  if (remoteNotificationTimer) return;
  remoteNotificationTimer = setInterval(() => {
    refreshRemoteNotifications().catch((error) => {
      console.warn("remote notification polling failed", error);
    });
  }, 10000);
  refreshRemoteNotifications().catch((error) => {
    console.warn("remote notification bootstrap failed", error);
  });
}

async function refreshRemoteNotifications() {
  const button = document.getElementById("remoteManageBtn");
  if (!button) return;
  const clients = await requestJSON("/api/remote/clients");
  if (!Array.isArray(clients) || !clients.length) {
    remoteNotificationsInitialized = true;
    updateRemoteUnreadBadge();
    return;
  }

  const newlyDetected = [];
  for (const client of clients) {
    if (!client?.client_id) continue;
    const conversations = await requestJSON(`/api/remote/conversations?client_id=${encodeURIComponent(client.client_id)}`);
    if (!Array.isArray(conversations) || !conversations.length) continue;
    const conversation = conversations[0];
    if (!conversation?.id) continue;
    const messages = await requestJSON(`/api/remote/conversations/${conversation.id}/messages`);
    if (!Array.isArray(messages)) continue;
    for (const message of messages) {
      if (!isLoginQrMessage(message)) continue;
      const messageId = Number(message.id || 0);
      if (!messageId || seenRemoteMessageIds.has(messageId)) continue;
      seenRemoteMessageIds.add(messageId);
      if (remoteNotificationsInitialized) {
        newlyDetected.push({
          clientName: client.client_name || client.client_id,
          message,
        });
      }
    }
  }

  if (!remoteNotificationsInitialized) {
    remoteNotificationsInitialized = true;
    updateRemoteUnreadBadge();
    return;
  }

  if (newlyDetected.length) {
    remoteQrUnreadCount += newlyDetected.length;
    updateRemoteUnreadBadge();
    newlyDetected.forEach(({ clientName }) => {
      showToast(`鏀跺埌鏂扮殑鐧诲綍浜岀淮鐮佹埅鍥撅細${clientName}`, "warning");
    });
  }
}

function isLoginQrMessage(message) {
  if (!message || message.sender_type !== "client" || message.message_type !== "image") {
    return false;
  }
  const content = String(message.content_text || "").toLowerCase();
  if (content.includes("鐧诲綍浜岀淮鐮?) || content.includes("login qr") || content.includes("login-qr")) {
    return true;
  }
  if (Array.isArray(message.attachments)) {
    return message.attachments.some((attachment) => {
      const originalName = String(attachment.original_name || "").toLowerCase();
      return originalName.includes("login-qr") || originalName.includes("qr");
    });
  }
  return false;
}

function updateRemoteUnreadBadge() {
  const badge = document.getElementById("remoteUnreadBadge");
  if (!badge) return;
  if (remoteQrUnreadCount > 0) {
    badge.hidden = false;
    badge.textContent = String(remoteQrUnreadCount);
  } else {
    badge.hidden = true;
    badge.textContent = "0";
  }
}

function clearRemoteUnreadNotifications() {
  remoteQrUnreadCount = 0;
  updateRemoteUnreadBadge();
}

async function loadRemoteClients() {
  try {
    const items = await requestJSON("/api/remote/clients");
    if (!items) return;
    const select = document.getElementById("remoteClientSelect");
    select.innerHTML = "";
    items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.client_id;
      option.textContent = `${item.client_name} (${item.status || "offline"})`;
      select.appendChild(option);
    });
    if (items.length > 0) {
      select.value = items[0].client_id;
      await handleRemoteClientChange();
    } else {
      document.getElementById("remoteMessagesBox").innerHTML = '<div class="text-muted">鏆傛棤瀹㈡埛绔?/div>';
    }
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function createRemoteClient() {
  const input = document.getElementById("remoteClientNameInput");
  const clientName = input.value.trim() || "榛樿璁惧";
  try {
    const result = await requestJSON("/api/remote/clients", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_name: clientName }),
    });
    if (!result) return;
    document.getElementById("remoteClientSecretBox").hidden = false;
    document.getElementById("remoteClientIdValue").textContent = result.item.client_id;
    document.getElementById("remoteClientTokenValue").textContent = result.client_token;
    input.value = "";
    await loadRemoteClients();
    showToast("瀹㈡埛绔垱寤烘垚鍔?, "success");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleRemoteClientChange() {
  const clientId = document.getElementById("remoteClientSelect").value;
  if (!clientId) {
    document.getElementById("remoteMessagesBox").innerHTML = '<div class="text-muted">鏆傛棤浼氳瘽</div>';
    currentRemoteConversationId = null;
    return;
  }
  let conversations = await requestJSON(`/api/remote/conversations?client_id=${encodeURIComponent(clientId)}`);
  if (!conversations) return;
  let conversation = conversations[0];
  if (!conversation) {
    conversation = await requestJSON("/api/remote/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId, title: `${clientId} 浼氳瘽` }),
    });
  }
  currentRemoteConversationId = conversation.id;
  await loadRemoteMessages(conversation.id);
}

async function loadRemoteMessages(conversationId) {
  try {
    const items = await requestJSON(`/api/remote/conversations/${conversationId}/messages`);
    if (!items) return;
    const box = document.getElementById("remoteMessagesBox");
    box.innerHTML = "";
    if (!items.length) {
      box.innerHTML = '<div class="text-muted">鏆傛棤娑堟伅</div>';
      return;
    }
    items.forEach((item) => {
      const wrapper = document.createElement("div");
      wrapper.className = "border rounded p-2 mb-2 bg-white";
      const title = document.createElement("div");
      title.className = "small text-muted mb-1";
      title.textContent = `${item.sender_type} 路 ${item.message_type} 路 ${item.status} 路 ${item.created_at || ""}`;
      wrapper.appendChild(title);
      if (item.content_text) {
        const body = document.createElement("div");
        body.textContent = item.content_text;
        wrapper.appendChild(body);
      }
      const commandResult = buildRemoteCommandResultSummary(item);
      if (commandResult) {
        const resultBlock = document.createElement("pre");
        resultBlock.className = "small bg-light border rounded p-2 mt-2 mb-0 text-wrap";
        resultBlock.style.whiteSpace = "pre-wrap";
        resultBlock.textContent = commandResult;
        wrapper.appendChild(resultBlock);
      }
      if (Array.isArray(item.attachments) && item.attachments.length) {
        item.attachments.forEach((attachment) => {
          if (attachment.file_type === "image") {
            const img = document.createElement("img");
            img.src = attachment.download_url;
            img.className = "img-fluid rounded mt-2";
            wrapper.appendChild(img);
          }
        });
      }
      box.appendChild(wrapper);
    });
    box.scrollTop = box.scrollHeight;
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function buildRemoteCommandResultSummary(item) {
  if (!item || item.message_type !== "command" || !item.result || typeof item.result !== "object") {
    return "";
  }
  const result = item.result;
  const lines = [];
  const successCount = Number(result.success_count || 0);
  const failedCount = Number(result.failed_count || 0);
  const workspacePath = String(result.workspace_path || "").trim();
  const autoRun = Boolean(result.auto_run);
  const execution = result.execution && typeof result.execution === "object" ? result.execution : null;
  const failedItems = Array.isArray(result.failed) ? result.failed.filter(Boolean) : [];
  const errorText = String(result.error || "").trim();

  if (workspacePath) {
    lines.push(`工作目录：${workspacePath}`);
  }
  if ("success_count" in result || "failed_count" in result) {
    lines.push(`导入结果：成功 ${successCount}，失败 ${failedCount}`);
  }
  if (execution) {
    lines.push(`自动执行：${execution.started ? "已启动" : autoRun ? "未启动" : "未请求"}`);
    if (Array.isArray(execution.enabled_steps) && execution.enabled_steps.length) {
      lines.push(`执行步骤：${execution.enabled_steps.join(", ")}`);
    }
    if (execution.on_project_error) {
      lines.push(`失败策略：${execution.on_project_error}`);
    }
    if (execution.parallel_projects) {
      lines.push(`并行项目数：${execution.parallel_projects}`);
    }
  } else if (autoRun) {
    lines.push("自动执行：已请求");
  }
  if (errorText) {
    lines.push(`错误：${errorText}`);
  }
  if (failedItems.length) {
    lines.push("失败明细：");
    failedItems.slice(0, 10).forEach((entry) => lines.push(`- ${entry}`));
    if (failedItems.length > 10) {
      lines.push(`- 其余 ${failedItems.length - 10} 条失败未展开`);
    }
  }
  return lines.join("\n").trim();
}

async function sendRemoteImportCommand() {
  if (!currentRemoteConversationId) {
    showToast("璇峰厛閫夋嫨瀹㈡埛绔?, "warning");
    return;
  }
  const rawTitles = document.getElementById("remoteDramaTitlesInput").value.trim();
  const titles = rawTitles
    .split("\n")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
  if (!titles.length) {
    showToast("璇疯嚦灏戣緭鍏ヤ竴涓煭鍓у悕", "warning");
    return;
  }
  const syncDownload = document.getElementById("remoteSyncDownloadCheckbox").checked;
  const autoRun = document.getElementById("remoteAutoRunCheckbox").checked;
  const workspacePath = document.getElementById("remoteWorkspacePathInput").value.trim();
  const onProjectError = document.getElementById("remoteOnProjectErrorSelect").value;
  const parallelProjects = Math.max(1, Math.min(4, Number.parseInt(document.getElementById("remoteParallelProjectsInput").value || "1", 10) || 1));
  const enabledStepsRaw = document.getElementById("remoteEnabledStepsInput").value.trim();
  const enabledSteps = enabledStepsRaw
    ? enabledStepsRaw
        .split(/[\n,]/)
        .map((item) => item.trim())
        .filter((item) => item.length > 0)
    : [];
  try {
    await requestJSON(`/api/remote/conversations/${currentRemoteConversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message_type: "command",
        content_text: `导入短剧：${titles.join("、")}${autoRun ? "（自动执行）" : ""}`,
        payload: {
          command: "import_drama_titles",
          titles,
          sync_download: syncDownload,
          auto_run: autoRun,
          workspace_path: workspacePath,
          enabled_steps: enabledSteps,
          on_project_error: onProjectError,
          parallel_projects: parallelProjects,
        },
      }),
    });
    document.getElementById("remoteDramaTitlesInput").value = "";
    await loadRemoteMessages(currentRemoteConversationId);
    showToast("杩滅▼瀵煎叆鍛戒护宸插彂閫?, "success");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleQuickAdd() {
  const namesTextarea = document.getElementById("quickAddNames");
  const namesRaw = namesTextarea.value.trim();
  if (!namesRaw) {
    namesTextarea.classList.add("is-invalid");
    namesTextarea.focus();
    return;
  }
  namesTextarea.classList.remove("is-invalid");
  const names = namesRaw
    .split("\n")
    .map((n) => n.trim())
    .filter((n) => n.length > 0);
  if (names.length === 0) {
    namesTextarea.classList.add("is-invalid");
    namesTextarea.focus();
    return;
  }
  const company = document.getElementById("quickAddCompany").value.trim() || null;
  try {
    const result = await requestJSON("/api/dramas/quick-add", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names, company }),
    });
    if (!result) return;
    document.getElementById("quickAddResult").hidden = false;
    document.getElementById("quickAddSuccessCount").textContent = result.success_count;
    const dupSection = document.getElementById("quickAddDuplicatesSection");
    const dupList = document.getElementById("quickAddDuplicatesList");
    dupList.innerHTML = "";
    if (result.duplicates && result.duplicates.length > 0) {
      dupSection.hidden = false;
      result.duplicates.forEach((name) => {
        const li = document.createElement("li");
        li.textContent = name;
        dupList.appendChild(li);
      });
    } else {
      dupSection.hidden = true;
    }
    showToast(
      `蹇€熷綍鍏ュ畬鎴愶細鎴愬姛 ${result.success_count} 鏉?{
        result.duplicates.length ? "锛岄噸澶嶈烦杩?" + result.duplicates.length + " 鏉? : ""
      }`,
      "success"
    );
    await loadDramas();
    await fetchCompanies();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

