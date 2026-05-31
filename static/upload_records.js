const state = {
  page: 1,
  pageSize: 20,
  pages: 0,
  canViewAllUsers: false,
};

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("queryBtn")?.addEventListener("click", () => {
    state.page = 1;
    loadUploadRecords();
  });
  document.getElementById("prevPageBtn")?.addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      loadUploadRecords();
    }
  });
  document.getElementById("nextPageBtn")?.addEventListener("click", () => {
    if (state.page < state.pages) {
      state.page += 1;
      loadUploadRecords();
    }
  });
  loadUploadRecords();
});

async function requestJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Accept": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || data.message || "请求失败");
  return data;
}

async function loadUploadRecords() {
  const params = new URLSearchParams({
    page: String(state.page),
    page_size: String(state.pageSize),
  });
  for (const [id, key] of [
    ["platformSelect", "platform"],
    ["ownerSelect", "user_id"],
    ["statusInput", "status"],
    ["searchInput", "search"],
    ["dateFrom", "date_from"],
    ["dateTo", "date_to"],
  ]) {
    const value = document.getElementById(id)?.value?.trim();
    if (value) params.set(key, value);
  }

  try {
    const data = await requestJSON(`/api/upload-records?${params.toString()}`);
    state.pages = Number(data.pages || 0);
    state.canViewAllUsers = Boolean(data.can_view_all_users);
    document.getElementById("userFilterWrap").hidden = !state.canViewAllUsers;
    if (state.canViewAllUsers) loadOwnerOptions();
    renderUploadRecords(data.items || []);
    document.getElementById("pageInfo").textContent = `第 ${data.page || 1} 页 / 共 ${data.pages || 0} 页，${data.total || 0} 条`;
    document.getElementById("prevPageBtn").disabled = state.page <= 1;
    document.getElementById("nextPageBtn").disabled = state.page >= state.pages;
  } catch (error) {
    renderUploadRecords([]);
    document.getElementById("pageInfo").textContent = error.message || "加载失败";
  }
}

let ownerOptionsLoaded = false;
async function loadOwnerOptions() {
  if (ownerOptionsLoaded) return;
  ownerOptionsLoaded = true;
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
    ownerOptionsLoaded = false;
  }
}

function renderUploadRecords(items) {
  const tbody = document.getElementById("uploadRecordsTableBody");
  const mobileList = document.getElementById("uploadRecordsMobileList");
  tbody.innerHTML = "";
  mobileList.innerHTML = "";
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-4">暂无上传记录</td></tr>';
    mobileList.innerHTML = '<div class="mobile-empty text-muted">暂无上传记录</div>';
    return;
  }
  for (const item of items) {
    const progress = progressText(item);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(item.record_time || item.created_at || "-")}</td>
      <td>${escapeHtml(item.owner_username || "-")}</td>
      <td>${escapeHtml(item.platform_label || item.platform || "-")}</td>
      <td>${escapeHtml(item.original_name || "-")}</td>
      <td>${escapeHtml(item.new_name || "-")}</td>
      <td>${statusBadge(item.upload_status)}</td>
      <td>${escapeHtml(item.step_label || "-")}</td>
      <td>${escapeHtml(progress)}</td>
      <td>${escapeHtml(item.uploader_display || item.account_profile_name || "-")}</td>
      <td class="text-truncate" style="max-width: 260px">${escapeHtml(item.failure_reason || "-")}</td>
    `;
    tbody.appendChild(tr);

    const card = document.createElement("div");
    card.className = "mobile-record-card";
    card.innerHTML = `
      <div class="mobile-record-head">
        <div>
          <div class="mobile-record-title">${escapeHtml(item.new_name || item.original_name || item.project_name || "-")}</div>
          <div class="mobile-record-subtitle">${escapeHtml(item.record_time || "-")}</div>
        </div>
        ${statusBadge(item.upload_status)}
      </div>
      <div class="mobile-record-grid">
        <div><span>用户</span><strong>${escapeHtml(item.owner_username || "-")}</strong></div>
        <div><span>平台</span><strong>${escapeHtml(item.platform_label || "-")}</strong></div>
        <div><span>进度</span><strong>${escapeHtml(progress)}</strong></div>
        <div><span>上传者</span><strong>${escapeHtml(item.uploader_display || item.account_profile_name || "-")}</strong></div>
      </div>
    `;
    mobileList.appendChild(card);
  }
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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
