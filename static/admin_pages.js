let minidramaSettingsState = { apps: [], editingAppId: "" };
let kuaishouSettingsState = { apps: [], editingAppId: "" };
let currentRemoteConversationId = null;
let passwordTargetUserId = null;
let editingUserId = null;
let usersCache = [];
let currentDevicesUserId = null;
let userEditorModal = null;

document.addEventListener("DOMContentLoaded", () => {
  cacheDefaultFeedbackMessages();
  if (window.adminPage === "users") initUsersPage();
  if (window.adminPage === "minidrama") initMinidramaSettingsPage();
  if (window.adminPage === "kuaishou") initKuaishouSettingsPage();
  if (window.adminPage === "remote") initRemotePage();
});

function initUsersPage() {
  userEditorModal = new bootstrap.Modal(document.getElementById("userEditorModal"));
  document.getElementById("addUserBtn")?.addEventListener("click", showUserEditor);
  document.getElementById("createUserBtn")?.addEventListener("click", saveUser);
  document.getElementById("cancelPasswordEditBtn")?.addEventListener("click", hidePasswordEditor);
  document.getElementById("generatePasswordBtn")?.addEventListener("click", fillGeneratedPassword);
  document.getElementById("savePasswordBtn")?.addEventListener("click", resetUserPassword);
  document.getElementById("refreshUsersBtn")?.addEventListener("click", loadUsers);
  document.getElementById("userTableBody")?.addEventListener("click", handleUserTableClick);
  document.getElementById("userMobileList")?.addEventListener("click", handleUserTableClick);
  document.getElementById("cancelDevicesBtn")?.addEventListener("click", hideDevicesPanel);
  document.getElementById("userDevicesTableBody")?.addEventListener("click", handleUserDevicesTableClick);
  document.getElementById("userDevicesMobileList")?.addEventListener("click", handleUserDevicesTableClick);
  document.getElementById("userEditorModal")?.addEventListener("hidden.bs.modal", resetUserForm);
  loadUsers();
}

function initMinidramaSettingsPage() {
  document.getElementById("refreshMinidramaSettingsBtn")?.addEventListener("click", loadMinidramaSettings);
  document.getElementById("newMinidramaSettingsBtn")?.addEventListener("click", () => showMinidramaEditor(null));
  document.getElementById("cancelMinidramaSettingsBtn")?.addEventListener("click", hideMinidramaEditor);
  document.getElementById("saveMinidramaSettingsBtn")?.addEventListener("click", saveMinidramaSettings);
  document.getElementById("minidramaSettingsTableBody")?.addEventListener("click", handleMinidramaSettingsTableClick);
  loadMinidramaSettings();
}

function initKuaishouSettingsPage() {
  document.getElementById("refreshKuaishouSettingsBtn")?.addEventListener("click", loadKuaishouSettings);
  document.getElementById("newKuaishouSettingsBtn")?.addEventListener("click", () => showKuaishouEditor(null));
  document.getElementById("cancelKuaishouSettingsBtn")?.addEventListener("click", hideKuaishouEditor);
  document.getElementById("saveKuaishouSettingsBtn")?.addEventListener("click", saveKuaishouSettings);
  document.getElementById("kuaishouSettingsTableBody")?.addEventListener("click", handleKuaishouSettingsTableClick);
  loadKuaishouSettings();
}

async function initRemotePage() {
  clearRemoteUnreadNotifications();
  document.getElementById("refreshRemoteClientsBtn")?.addEventListener("click", loadRemoteClients);
  document.getElementById("createRemoteClientBtn")?.addEventListener("click", createRemoteClient);
  document.getElementById("remoteClientSelect")?.addEventListener("change", handleRemoteClientChange);
  document.getElementById("sendRemoteImportBtn")?.addEventListener("click", sendRemoteImportCommand);
  await loadRemoteClients();
}

async function loadUsers() {
  try {
    const users = await requestJSON("/api/users");
    if (!users) return;
    usersCache = users;
    const tbody = document.getElementById("userTableBody");
    const mobileList = document.getElementById("userMobileList");
    tbody.innerHTML = "";
    if (mobileList) mobileList.innerHTML = "";
    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted py-4">暂无用户</td></tr>';
      renderMobileEmptyState(mobileList, "暂无用户");
      return;
    }
    users.forEach((user) => {
      const tr = document.createElement("tr");
      const isSelf = user.username === window.currentUser.username;
      const statusText = user.status === "disabled" ? "停用" : "启用";
      const statusClass = user.status === "disabled" ? "text-bg-secondary" : "text-bg-success";
      const actionButtons = buildUserActionButtons(user, isSelf);
      tr.innerHTML = `
        <td>${escapeHtml(user.username)}</td>
        <td>${escapeHtml(user.email || "-")}</td>
        <td>${user.role === "admin" ? "管理员" : "普通用户"}</td>
        <td><span class="badge ${statusClass}">${statusText}</span> <span class="text-muted small">${escapeHtml(user.edition || "pro")}</span></td>
        <td>${Number(user.active_devices || 0)}/${Number(user.max_devices || 0)}</td>
        <td>${escapeHtml(user.expires_at || "永久")}</td>
        <td><span class="text-muted small">已加密保存</span></td>
        <td class="text-end">${actionButtons.join("")}</td>
      `;
      tbody.appendChild(tr);
      mobileList?.appendChild(buildUserMobileCard(user, actionButtons, statusText, statusClass));
    });
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function buildUserActionButtons(user, isSelf) {
  return [
    `<button class="btn btn-sm btn-outline-secondary" data-action="edit-user" data-id="${user.id}">编辑</button>`,
    `<button class="btn btn-sm btn-outline-info" data-action="view-devices" data-id="${user.id}" data-username="${escapeHtml(user.username)}">设备</button>`,
    `<button class="btn btn-sm btn-outline-primary" data-action="reset-password" data-id="${user.id}" data-username="${escapeHtml(user.username)}">重置密码</button>`,
    `<button class="btn btn-sm btn-outline-danger" data-action="delete-user" data-id="${user.id}" data-username="${escapeHtml(user.username)}" ${isSelf ? "disabled" : ""}>删除</button>`,
  ];
}

function buildUserMobileCard(user, actionButtons, statusText, statusClass) {
  const card = document.createElement("article");
  card.className = "mobile-record-card";
  card.innerHTML = `
    <div class="mobile-record-head">
      <div class="mobile-record-title">${escapeHtml(user.username)}</div>
      <span class="badge ${statusClass}">${statusText}</span>
    </div>
    <div class="mobile-record-subtitle">${escapeHtml(user.email || "未填写邮箱")}</div>
    <div class="mobile-record-grid">
      <div><span>角色</span><strong>${user.role === "admin" ? "管理员" : "普通用户"}</strong></div>
      <div><span>授权</span><strong>${escapeHtml(user.edition || "pro")}</strong></div>
      <div><span>设备</span><strong>${Number(user.active_devices || 0)}/${Number(user.max_devices || 0)}</strong></div>
      <div><span>到期</span><strong>${escapeHtml(user.expires_at || "永久")}</strong></div>
      <div><span>密码</span><strong>已加密保存</strong></div>
    </div>
    <div class="mobile-record-actions">${actionButtons.join("")}</div>
  `;
  return card;
}

function showUserEditor(user = null) {
  resetUserForm();
  hidePasswordEditor();
  hideDevicesPanel();
  editingUserId = user?.id || null;
  document.getElementById("userEditorTitle").textContent = editingUserId ? "编辑用户授权" : "新增用户";
  document.getElementById("createUserBtn").textContent = editingUserId ? "保存修改" : "保存";
  document.getElementById("userNameInput").value = user?.username || "";
  document.getElementById("userEmailInput").value = user?.email || "";
  document.getElementById("userPasswordInput").value = "";
  document.getElementById("userPasswordInput").disabled = Boolean(editingUserId);
  document.getElementById("userPasswordInput").placeholder = editingUserId ? "\u8bf7\u4f7f\u7528\u91cd\u7f6e\u5bc6\u7801\u5165\u53e3\u4fee\u6539" : "\u8bf7\u8f93\u5165\u5bc6\u7801";
  document.getElementById("userRoleInput").value = user?.role || "user";
  document.getElementById("userStatusInput").value = user?.status || "active";
  document.getElementById("userEditionInput").value = user?.edition || "pro";
  document.getElementById("userMaxDevicesInput").value = String(user?.max_devices || 1);
  document.getElementById("userExpiresAtInput").value = (user?.expires_at || "").slice(0, 10);
  userEditorModal?.show();
  document.getElementById("userNameInput")?.focus();
}

function hideUserEditor() {
  resetUserForm();
  userEditorModal?.hide();
}

function showPasswordEditor(userId, username) {
  hideUserEditor();
  hideDevicesPanel();
  passwordTargetUserId = userId;
  document.getElementById("passwordTargetUser").textContent = username || "-";
  document.getElementById("resetPasswordInput").value = "";
  document.getElementById("passwordResetValue").textContent = "";
  document.getElementById("passwordResetResult").hidden = true;
  clearFormValidation(document.getElementById("userPasswordPanel"));
  document.getElementById("userPasswordPanel").hidden = false;
  document.getElementById("resetPasswordInput")?.focus();
}

function hidePasswordEditor() {
  passwordTargetUserId = null;
  const panel = document.getElementById("userPasswordPanel");
  if (!panel) return;
  document.getElementById("resetPasswordInput").value = "";
  document.getElementById("passwordResetValue").textContent = "";
  document.getElementById("passwordResetResult").hidden = true;
  clearFormValidation(panel);
  panel.hidden = true;
}

function fillGeneratedPassword() {
  document.getElementById("resetPasswordInput").value = generateReadablePassword();
  document.getElementById("resetPasswordInput").focus();
}

function generateReadablePassword() {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = new Uint32Array(12);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => chars[value % chars.length]).join("");
}

async function saveUser() {
  const usernameInput = document.getElementById("userNameInput");
  const emailInput = document.getElementById("userEmailInput");
  const passwordInput = document.getElementById("userPasswordInput");
  const maxDevicesInput = document.getElementById("userMaxDevicesInput");
  const payload = {
    username: usernameInput.value.trim(),
    email: emailInput.value.trim(),
    password: passwordInput.value.trim(),
    role: document.getElementById("userRoleInput").value,
    status: document.getElementById("userStatusInput").value,
    edition: document.getElementById("userEditionInput").value,
    max_devices: Number.parseInt(maxDevicesInput.value || "1", 10),
    expires_at: document.getElementById("userExpiresAtInput").value.trim(),
  };
  let isValid = true;
  let firstInvalid = null;

  if (!validateField(usernameInput, () => /^(?:[A-Za-z0-9_]{2,30}|[^@\s]+@[^@\s]+\.[^@\s]+)$/.test(payload.username))) {
    isValid = false;
    firstInvalid = firstInvalid || usernameInput;
  }
  if (!validateField(emailInput, () => !payload.email || /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(payload.email))) {
    isValid = false;
    firstInvalid = firstInvalid || emailInput;
  }
  if (!editingUserId && !validateField(passwordInput, () => payload.password.length >= 6)) {
    isValid = false;
    firstInvalid = firstInvalid || passwordInput;
  }
  if (!validateField(maxDevicesInput, () => Number.isInteger(payload.max_devices) && payload.max_devices > 0)) {
    isValid = false;
    firstInvalid = firstInvalid || maxDevicesInput;
  }
  if (!isValid) {
    firstInvalid?.focus();
    return;
  }

  try {
    const url = editingUserId ? `/api/users/${editingUserId}` : "/api/users";
    const method = editingUserId ? "PUT" : "POST";
    if (editingUserId) delete payload.password;
    await requestJSON(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    showToast(editingUserId ? "用户已更新" : "新增用户成功", "success");
    resetUserForm();
    userEditorModal?.hide();
    await loadUsers();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleUserTableClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = Number(button.dataset.id);
  const username = button.dataset.username;
  if (button.dataset.action === "edit-user") {
    const user = usersCache.find((item) => Number(item.id) === id);
    if (user) showUserEditor(user);
    return;
  }
  if (button.dataset.action === "view-devices") {
    await loadUserDevices(id, username);
    return;
  }
  if (button.dataset.action === "reset-password") {
    showPasswordEditor(id, username);
    return;
  }
  if (button.dataset.action !== "delete-user") return;
  if (!confirm(`确定删除用户 ${username} 吗？删除后该账号的桌面端登录设备也会失效。`)) return;
  try {
    await requestJSON(`/api/users/${id}`, { method: "DELETE" });
    showToast("用户删除成功", "success");
    await loadUsers();
    if (currentDevicesUserId === id) hideDevicesPanel();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function resetUserPassword() {
  if (!passwordTargetUserId) return;
  const input = document.getElementById("resetPasswordInput");
  const newPassword = input.value.trim();
  if (!validateField(input, () => newPassword.length >= 6)) {
    input.focus();
    return;
  }
  try {
    await requestJSON(`/api/users/${passwordTargetUserId}/password`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: newPassword }),
    });
    document.getElementById("passwordResetValue").textContent = newPassword;
    document.getElementById("passwordResetResult").hidden = false;
    showToast("密码已更新", "success");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function resetUserForm() {
  editingUserId = null;
  document.getElementById("userNameInput").value = "";
  document.getElementById("userEmailInput").value = "";
  document.getElementById("userPasswordInput").value = "";
  document.getElementById("userPasswordInput").disabled = false;
  document.getElementById("userPasswordInput").placeholder = "\u8bf7\u8f93\u5165\u5bc6\u7801";
  document.getElementById("userRoleInput").value = "user";
  document.getElementById("userStatusInput").value = "active";
  document.getElementById("userEditionInput").value = "pro";
  document.getElementById("userMaxDevicesInput").value = "1";
  document.getElementById("userExpiresAtInput").value = "";
  clearFormValidation(document.getElementById("userEditorPanel"));
}

async function loadUserDevices(userId, username = "") {
  try {
    hideUserEditor();
    hidePasswordEditor();
    currentDevicesUserId = userId;
    const data = await requestJSON(`/api/users/${userId}/devices`);
    const user = data?.user || {};
    const devices = data?.devices || [];
    document.getElementById("userDevicesTitle").textContent = `${username || user.username || "\u7528\u6237"} \u00b7 ${devices.length} \u53f0\u8bbe\u5907`;
    const tbody = document.getElementById("userDevicesTableBody");
    const mobileList = document.getElementById("userDevicesMobileList");
    tbody.innerHTML = "";
    if (mobileList) mobileList.innerHTML = "";
    if (!devices.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">暂无登录设备</td></tr>';
      renderMobileEmptyState(mobileList, "暂无登录设备");
    } else {
      devices.forEach((device) => {
        const revoked = Boolean(device.revoked_at);
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${escapeHtml(device.device_name || "-")}</td>
          <td class="font-monospace small">${escapeHtml(device.machine_id || "-")}</td>
          <td>${escapeHtml([device.app_name, device.app_version].filter(Boolean).join(" ") || "-")}</td>
          <td>${escapeHtml(device.logged_in_at || "-")}</td>
          <td>${escapeHtml(device.last_verified_at || "-")}</td>
          <td>${revoked ? '<span class="badge text-bg-secondary">已解绑</span>' : '<span class="badge text-bg-success">有效</span>'}</td>
          <td class="text-end">
            <button class="btn btn-sm btn-outline-warning" data-action="revoke-device" data-id="${device.id}" ${revoked ? "disabled" : ""}>解绑</button>
          </td>
        `;
        tbody.appendChild(tr);
        mobileList?.appendChild(buildUserDeviceMobileCard(device, revoked));
      });
    }
    document.getElementById("userDevicesPanel").hidden = false;
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function buildUserDeviceMobileCard(device, revoked) {
  const card = document.createElement("article");
  card.className = "mobile-record-card";
  card.innerHTML = `
    <div class="mobile-record-head">
      <div class="mobile-record-title">${escapeHtml(device.device_name || "-")}</div>
      ${revoked ? '<span class="badge text-bg-secondary">已解绑</span>' : '<span class="badge text-bg-success">有效</span>'}
    </div>
    <div class="mobile-record-subtitle font-monospace">${escapeHtml(device.machine_id || "-")}</div>
    <div class="mobile-record-grid">
      <div><span>应用</span><strong>${escapeHtml([device.app_name, device.app_version].filter(Boolean).join(" ") || "-")}</strong></div>
      <div><span>登录时间</span><strong>${escapeHtml(device.logged_in_at || "-")}</strong></div>
      <div><span>最近校验</span><strong>${escapeHtml(device.last_verified_at || "-")}</strong></div>
    </div>
    <div class="mobile-record-actions">
      <button class="btn btn-sm btn-outline-warning" data-action="revoke-device" data-id="${device.id}" ${revoked ? "disabled" : ""}>解绑</button>
    </div>
  `;
  return card;
}

function hideDevicesPanel() {
  currentDevicesUserId = null;
  const panel = document.getElementById("userDevicesPanel");
  if (panel) panel.hidden = true;
}

async function handleUserDevicesTableClick(event) {
  const button = event.target.closest("button[data-action='revoke-device']");
  if (!button || !currentDevicesUserId) return;
  const deviceId = Number(button.dataset.id);
  if (!confirm("确定解绑这台设备吗？客户端下次联网校验后需要重新登录。")) return;
  try {
    await requestJSON(`/api/users/${currentDevicesUserId}/devices/${deviceId}/revoke`, { method: "POST" });
    showToast("设备已解绑", "success");
    await loadUserDevices(currentDevicesUserId);
    await loadUsers();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function loadMinidramaSettings() {
  try {
    resetMinidramaSettingsForm();
    const data = await requestJSON("/api/settings/minidrama");
    if (!data) return;
    const apps = Array.isArray(data.apps) ? data.apps : [];
    minidramaSettingsState.apps = apps;
    renderMinidramaSettingsTable(apps);
    hideMinidramaEditor();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function loadKuaishouSettings() {
  try {
    resetKuaishouSettingsForm();
    const data = await requestJSON("/api/settings/kuaishou");
    if (!data) return;
    const apps = Array.isArray(data.apps) ? data.apps : [];
    kuaishouSettingsState.apps = apps;
    renderKuaishouSettingsTable(apps);
    hideKuaishouEditor();
  } catch (error) {
    showToast(error.message, "danger");
  }
}

function showMinidramaEditor(item) {
  fillMinidramaSettingsForm(item);
  document.getElementById("minidramaEditorTitle").textContent = item?.app_id ? "编辑配置" : "新增配置";
  document.getElementById("minidramaEditorPanel").hidden = false;
  document.getElementById("minidramaNameInput")?.focus();
}

function hideMinidramaEditor() {
  resetMinidramaSettingsForm();
  const panel = document.getElementById("minidramaEditorPanel");
  if (panel) panel.hidden = true;
}

function showKuaishouEditor(item) {
  fillKuaishouSettingsForm(item);
  document.getElementById("kuaishouEditorTitle").textContent = item?.app_id ? "编辑配置" : "新增配置";
  document.getElementById("kuaishouEditorPanel").hidden = false;
  document.getElementById("kuaishouNameInput")?.focus();
}

function hideKuaishouEditor() {
  resetKuaishouSettingsForm();
  const panel = document.getElementById("kuaishouEditorPanel");
  if (panel) panel.hidden = true;
}

function renderMinidramaSettingsTable(apps) {
  const tbody = document.getElementById("minidramaSettingsTableBody");
  const items = Array.isArray(apps) ? apps : [];
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-muted small">暂无小程序配置</td></tr>';
    return;
  }
  tbody.innerHTML = items
    .map((item) => {
      const appId = item.app_id || "";
      return `
        <tr>
          <td>${escapeHtml(item.name || "未命名")}</td>
          <td class="font-monospace">${escapeHtml(appId)}</td>
          <td>${item.app_secret_configured ? escapeHtml(item.app_secret_masked || "已配置") : '<span class="text-danger">未配置</span>'}</td>
          <td>${item.is_default ? '<span class="badge text-bg-primary">默认</span>' : ""}</td>
          <td class="small text-muted">${escapeHtml(item.updated_at || "-")}</td>
          <td class="text-end">
            <button type="button" class="btn btn-sm btn-outline-primary" data-action="edit-minidrama" data-app-id="${escapeHtml(appId)}">编辑</button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="delete-minidrama" data-app-id="${escapeHtml(appId)}">删除</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

function renderKuaishouSettingsTable(apps) {
  const tbody = document.getElementById("kuaishouSettingsTableBody");
  const items = Array.isArray(apps) ? apps : [];
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-muted small">暂无快手配置</td></tr>';
    return;
  }
  tbody.innerHTML = items
    .map((item) => {
      const appId = item.app_id || "";
      const tokenStatus = item.access_token_configured
        ? `已同步${item.access_token_expires_at ? ` / ${escapeHtml(formatTimestamp(item.access_token_expires_at))}` : ""}`
        : '<span class="text-danger">未同步</span>';
      return `
        <tr>
          <td>${escapeHtml(item.name || "未命名")}</td>
          <td class="font-monospace">${escapeHtml(appId)}</td>
          <td class="font-monospace">${escapeHtml(item.advertiser_id || "-")}</td>
          <td>${item.app_secret_configured ? escapeHtml(item.app_secret_masked || "已配置") : '<span class="text-danger">未配置</span>'}</td>
          <td>${tokenStatus}</td>
          <td class="small text-muted">${escapeHtml(item.updated_at || "-")}</td>
          <td class="text-end">
            <button type="button" class="btn btn-sm btn-outline-primary" data-action="edit-kuaishou" data-app-id="${escapeHtml(appId)}">编辑</button>
            <button type="button" class="btn btn-sm btn-outline-danger" data-action="delete-kuaishou" data-app-id="${escapeHtml(appId)}">删除</button>
          </td>
        </tr>
      `;
    })
    .join("");
}

function fillMinidramaSettingsForm(item) {
  const form = document.getElementById("minidramaSettingsForm");
  const app = item || {};
  minidramaSettingsState.editingAppId = app.app_id || "";
  document.getElementById("minidramaNameInput").value = app.name || "";
  document.getElementById("minidramaAppIdInput").value = app.app_id || "";
  document.getElementById("minidramaAppSecretInput").value = "";
  document.getElementById("minidramaDefaultInput").checked = Boolean(app.is_default);
  form.dataset.appSecretConfigured = app.app_secret_configured ? "1" : "0";
  clearFormValidation(form);
  renderMinidramaSettingsMeta(app);
}

function fillKuaishouSettingsForm(item) {
  const form = document.getElementById("kuaishouSettingsForm");
  const app = item || {};
  kuaishouSettingsState.editingAppId = app.app_id || "";
  document.getElementById("kuaishouNameInput").value = app.name || "";
  document.getElementById("kuaishouAppIdInput").value = app.app_id || "";
  document.getElementById("kuaishouAdvertiserIdInput").value = app.advertiser_id || "";
  document.getElementById("kuaishouAppSecretInput").value = "";
  document.getElementById("kuaishouDefaultInput").checked = Boolean(app.is_default);
  form.dataset.appSecretConfigured = app.app_secret_configured ? "1" : "0";
  clearFormValidation(form);
  renderKuaishouSettingsMeta(app);
}

function resetMinidramaSettingsForm() {
  document.getElementById("minidramaNameInput").value = "";
  document.getElementById("minidramaAppIdInput").value = "";
  document.getElementById("minidramaAppSecretInput").value = "";
  document.getElementById("minidramaDefaultInput").checked = false;
  document.getElementById("minidramaSettingsMeta").textContent = "";
  document.getElementById("minidramaSettingsForm").dataset.appSecretConfigured = "0";
  minidramaSettingsState.editingAppId = "";
  clearFormValidation(document.getElementById("minidramaSettingsForm"));
}

function resetKuaishouSettingsForm() {
  document.getElementById("kuaishouNameInput").value = "";
  document.getElementById("kuaishouAppIdInput").value = "";
  document.getElementById("kuaishouAdvertiserIdInput").value = "";
  document.getElementById("kuaishouAppSecretInput").value = "";
  document.getElementById("kuaishouDefaultInput").checked = false;
  document.getElementById("kuaishouSettingsMeta").textContent = "";
  document.getElementById("kuaishouSettingsForm").dataset.appSecretConfigured = "0";
  kuaishouSettingsState.editingAppId = "";
  clearFormValidation(document.getElementById("kuaishouSettingsForm"));
}

function renderMinidramaSettingsMeta(data) {
  const meta = document.getElementById("minidramaSettingsMeta");
  if (!data || !data.app_id) {
    meta.textContent = "请选择或新增一个小程序配置";
    return;
  }
  meta.textContent = [
    `AppID：${data.app_id || "-"}`,
    `名称：${data.name || "未命名"}`,
    `来源：${data.source || "后台配置"}`,
    `AppSecret：${data.app_secret_configured ? data.app_secret_masked || "已配置" : "未配置"}`,
    data.updated_at ? `更新时间：${data.updated_at}` : "",
  ].filter(Boolean).join(" · ");
}

function renderKuaishouSettingsMeta(data) {
  const meta = document.getElementById("kuaishouSettingsMeta");
  if (!data || !data.app_id) {
    meta.textContent = "请选择或新增一个快手配置";
    return;
  }
  meta.textContent = [
    `AppID：${data.app_id || "-"}`,
    `Advertiser ID：${data.advertiser_id || "-"}`,
    `名称：${data.name || "未命名"}`,
    `AppSecret：${data.app_secret_configured ? data.app_secret_masked || "已配置" : "未配置"}`,
    `AccessToken：${data.access_token_configured ? "已同步" : "未同步"}`,
    `RefreshToken：${data.refresh_token_configured ? "已同步" : "未同步"}`,
    data.updated_at ? `更新时间：${data.updated_at}` : "",
  ].filter(Boolean).join(" · ");
}

function handleMinidramaSettingsTableClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const appId = button.dataset.appId || "";
  const item = minidramaSettingsState.apps.find((entry) => entry.app_id === appId);
  if (button.dataset.action === "edit-minidrama") showMinidramaEditor(item || null);
  if (button.dataset.action === "delete-minidrama") deleteMinidramaSettings(appId);
}

function handleKuaishouSettingsTableClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const appId = button.dataset.appId || "";
  const item = kuaishouSettingsState.apps.find((entry) => entry.app_id === appId);
  if (button.dataset.action === "edit-kuaishou") showKuaishouEditor(item || null);
  if (button.dataset.action === "delete-kuaishou") deleteKuaishouSettings(appId);
}

async function deleteMinidramaSettings(appId) {
  if (!appId || !confirm(`确认删除小程序配置 ${appId} 吗？`)) return;
  try {
    const data = await requestJSON(`/api/settings/minidrama/${encodeURIComponent(appId)}`, { method: "DELETE" });
    const apps = Array.isArray(data.apps) ? data.apps : [];
    minidramaSettingsState.apps = apps;
    renderMinidramaSettingsTable(apps);
    hideMinidramaEditor();
    showToast("小程序配置已删除", "success");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function deleteKuaishouSettings(appId) {
  if (!appId || !confirm(`确认删除快手配置 ${appId} 吗？`)) return;
  try {
    const data = await requestJSON(`/api/settings/kuaishou/${encodeURIComponent(appId)}`, { method: "DELETE" });
    const apps = Array.isArray(data.apps) ? data.apps : [];
    kuaishouSettingsState.apps = apps;
    renderKuaishouSettingsTable(apps);
    hideKuaishouEditor();
    showToast("快手配置已删除", "success");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function saveMinidramaSettings() {
  const form = document.getElementById("minidramaSettingsForm");
  const nameInput = document.getElementById("minidramaNameInput");
  const appIdInput = document.getElementById("minidramaAppIdInput");
  const appSecretInput = document.getElementById("minidramaAppSecretInput");
  const defaultInput = document.getElementById("minidramaDefaultInput");
  const name = nameInput.value.trim();
  const appId = appIdInput.value.trim();
  const appSecret = appSecretInput.value.trim();
  const hasCurrentSecret = form.dataset.appSecretConfigured === "1";
  if (!validateSettingsInputs(appIdInput, appSecretInput, appId, appSecret, hasCurrentSecret)) return;
  await saveSettingsPayload("minidrama", "/api/settings/minidrama", {
    name,
    app_id: appId,
    app_secret: appSecret,
    is_default: defaultInput.checked,
    enabled: true,
  });
}

async function saveKuaishouSettings() {
  const form = document.getElementById("kuaishouSettingsForm");
  const nameInput = document.getElementById("kuaishouNameInput");
  const appIdInput = document.getElementById("kuaishouAppIdInput");
  const advertiserIdInput = document.getElementById("kuaishouAdvertiserIdInput");
  const appSecretInput = document.getElementById("kuaishouAppSecretInput");
  const defaultInput = document.getElementById("kuaishouDefaultInput");
  const name = nameInput.value.trim();
  const appId = appIdInput.value.trim();
  const advertiserId = advertiserIdInput.value.trim();
  const appSecret = appSecretInput.value.trim();
  const hasCurrentSecret = form.dataset.appSecretConfigured === "1";
  if (!validateSettingsInputs(appIdInput, appSecretInput, appId, appSecret, hasCurrentSecret)) return;
  await saveSettingsPayload("kuaishou", "/api/settings/kuaishou", {
    name,
    app_id: appId,
    advertiser_id: advertiserId,
    app_secret: appSecret,
    is_default: defaultInput.checked,
    enabled: true,
  });
}

function validateSettingsInputs(appIdInput, appSecretInput, appId, appSecret, hasCurrentSecret) {
  let isValid = true;
  let firstInvalid = null;
  if (!validateField(appIdInput, () => appId.length > 0)) {
    isValid = false;
    firstInvalid = firstInvalid || appIdInput;
  }
  if (!validateField(appSecretInput, () => hasCurrentSecret || appSecret.length > 0)) {
    isValid = false;
    firstInvalid = firstInvalid || appSecretInput;
  }
  if (!isValid) firstInvalid?.focus();
  return isValid;
}

async function saveSettingsPayload(type, url, payload) {
  const saveBtn = document.getElementById(type === "minidrama" ? "saveMinidramaSettingsBtn" : "saveKuaishouSettingsBtn");
  saveBtn.disabled = true;
  try {
    const data = await requestJSON(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const apps = Array.isArray(data.apps) ? data.apps : [];
    if (type === "minidrama") {
      minidramaSettingsState.apps = apps;
      renderMinidramaSettingsTable(apps);
      hideMinidramaEditor();
      showToast("小程序配置已保存", "success");
    } else {
      kuaishouSettingsState.apps = apps;
      renderKuaishouSettingsTable(apps);
      hideKuaishouEditor();
      showToast("快手配置已保存", "success");
    }
  } catch (error) {
    showToast(error.message, "danger");
  } finally {
    saveBtn.disabled = false;
  }
}

function clearRemoteUnreadNotifications() {
  const badge = document.getElementById("sidebarRemoteUnreadBadge");
  if (!badge) return;
  badge.hidden = true;
  badge.textContent = "0";
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
      currentRemoteConversationId = null;
      document.getElementById("remoteMessagesBox").innerHTML = '<div class="text-muted">暂无客户端</div>';
    }
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function createRemoteClient() {
  const input = document.getElementById("remoteClientNameInput");
  const clientName = input.value.trim() || "默认设备";
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
    showToast("客户端创建成功", "success");
  } catch (error) {
    showToast(error.message, "danger");
  }
}

async function handleRemoteClientChange() {
  const clientId = document.getElementById("remoteClientSelect").value;
  if (!clientId) {
    currentRemoteConversationId = null;
    document.getElementById("remoteMessagesBox").innerHTML = '<div class="text-muted">暂无会话</div>';
    return;
  }
  let conversations = await requestJSON(`/api/remote/conversations?client_id=${encodeURIComponent(clientId)}`);
  if (!conversations) return;
  let conversation = conversations[0];
  if (!conversation) {
    conversation = await requestJSON("/api/remote/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: clientId, title: `${clientId} 会话` }),
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
      box.innerHTML = '<div class="text-muted">暂无消息</div>';
      return;
    }
    items.forEach((item) => {
      const wrapper = document.createElement("div");
      wrapper.className = "remote-message";
      wrapper.innerHTML = `
        <div class="small text-muted mb-1">${escapeHtml(item.sender_type || "-")} · ${escapeHtml(item.message_type || "-")} · ${escapeHtml(item.status || "-")} · ${escapeHtml(item.created_at || "")}</div>
      `;
      if (item.content_text) {
        const body = document.createElement("div");
        body.textContent = item.content_text;
        wrapper.appendChild(body);
      }
      const detailLines = buildRemoteMessageDetailLines(item);
      if (detailLines.length) {
        const detail = document.createElement("pre");
        detail.className = "small mt-2 mb-0 p-2 rounded border bg-light";
        detail.textContent = detailLines.join("\n");
        wrapper.appendChild(detail);
      }
      if (Array.isArray(item.attachments)) {
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

function collectRemoteEnabledSteps() {
  return Array.from(document.querySelectorAll(".remote-step-checkbox:checked"))
    .map((input) => String(input.value || "").trim())
    .filter(Boolean);
}

function buildRemoteMessageDetailLines(message) {
  const lines = [];
  const payload = message && typeof message.payload === "object" ? message.payload : null;
  const result = message && typeof message.result === "object" ? message.result : null;
  if (payload && message.message_type === "command") {
    if (payload.command) lines.push(`命令: ${payload.command}`);
    if (Array.isArray(payload.titles) && payload.titles.length) lines.push(`剧名: ${payload.titles.join("、")}`);
    if (payload.workspace_path) lines.push(`工作目录: ${payload.workspace_path}`);
    if (Array.isArray(payload.enabled_steps) && payload.enabled_steps.length) lines.push(`步骤: ${payload.enabled_steps.join(", ")}`);
    if (payload.on_project_error) lines.push(`失败策略: ${payload.on_project_error}`);
    if (payload.parallel_projects) lines.push(`并发项目数: ${payload.parallel_projects}`);
  }
  if (result) {
    if (typeof result.success_count === "number" || typeof result.failed_count === "number") {
      lines.push(`导入结果: 成功 ${Number(result.success_count || 0)} 个，失败 ${Number(result.failed_count || 0)} 个，过滤 ${Number(result.filtered_count || 0)} 个`);
    }
    if (result.error) lines.push(`错误: ${result.error}`);
  }
  return lines;
}

async function sendRemoteImportCommand() {
  if (!currentRemoteConversationId) {
    showToast("请先选择客户端", "warning");
    return;
  }
  const rawTitles = document.getElementById("remoteDramaTitlesInput").value.trim();
  const titles = rawTitles.split("\n").map((item) => item.trim()).filter(Boolean);
  if (!titles.length) {
    showToast("请至少输入一个短剧名", "warning");
    return;
  }
  const enabledSteps = collectRemoteEnabledSteps();
  if (!enabledSteps.length) {
    showToast("请至少勾选一个执行步骤", "warning");
    return;
  }
  try {
    await requestJSON(`/api/remote/conversations/${currentRemoteConversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message_type: "command",
        payload: {
          command: "import_drama_titles",
          titles,
          workspace_path: document.getElementById("remoteWorkspacePathInput").value.trim(),
          sync_download: document.getElementById("remoteSyncDownloadCheckbox").checked,
          auto_run: document.getElementById("remoteAutoRunCheckbox").checked,
          enabled_steps: enabledSteps,
          on_project_error: document.getElementById("remoteOnProjectErrorSelect").value,
          parallel_projects: Number(document.getElementById("remoteParallelProjectsSelect").value || 2),
        },
      }),
    });
    document.getElementById("remoteDramaTitlesInput").value = "";
    await loadRemoteMessages(currentRemoteConversationId);
    showToast("远程导入命令已发送", "success");
  } catch (error) {
    showToast(error.message, "danger");
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
    showToast("权限不足", "danger");
    return null;
  }
  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  if (!response.ok) {
    const errorData = isJson ? await response.json().catch(() => ({})) : {};
    throw new Error(errorData.error || errorData.message || "请求失败");
  }
  return isJson ? response.json() : response;
}

function showToast(message, type = "info") {
  const container = document.getElementById("toastContainer");
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
  toastEl.innerHTML = `
    <div class="d-flex">
      <div class="toast-body"></div>
      <button type="button" class="btn-close me-2 m-auto${classes.includes("text-white") ? " btn-close-white" : ""}" data-bs-dismiss="toast" aria-label="Close"></button>
    </div>
  `;
  toastEl.querySelector(".toast-body").textContent = message;
  container.appendChild(toastEl);
  const toast = new bootstrap.Toast(toastEl, { delay: 2500, autohide: true });
  toastEl.addEventListener("hidden.bs.toast", () => toastEl.remove());
  toast.show();
}

function renderMobileEmptyState(container, message) {
  if (!container) return;
  container.innerHTML = `<div class="mobile-empty-state">${escapeHtml(message)}</div>`;
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
    if (feedback) feedback.textContent = message || feedback.dataset.defaultText || "";
  } else {
    input.classList.remove("is-invalid");
    if (feedback && feedback.dataset.defaultText !== undefined) feedback.textContent = feedback.dataset.defaultText;
  }
  return isValid;
}

function getFeedbackElement(input, createIfMissing = false) {
  let node = input.nextElementSibling;
  while (node && !(node.classList && node.classList.contains("invalid-feedback"))) node = node.nextElementSibling;
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
    if (el.dataset.defaultText !== undefined) el.textContent = el.dataset.defaultText;
  });
}

function cacheDefaultFeedbackMessages() {
  document.querySelectorAll(".invalid-feedback").forEach((el) => {
    if (el.dataset.defaultText === undefined) el.dataset.defaultText = el.textContent || "";
  });
}

function formatTimestamp(timestampSeconds) {
  const value = Number(timestampSeconds || 0);
  if (!value) return "-";
  const date = new Date(value * 1000);
  return date.toLocaleString("zh-CN", { hour12: false });
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
