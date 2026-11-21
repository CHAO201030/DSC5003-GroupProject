/* CloudDisk single-page frontend. Talks only to the real FastAPI backend. */
(() => {
  "use strict";

  const TOKEN_KEY = "clouddisk_token";
  const state = {
    token: localStorage.getItem(TOKEN_KEY) || "",
    user: null,
    rootId: null,
    cwd: null,
    items: [],
    usage: null,
    authMode: "login",
    search: { mode: "all", query: "", page: 1, pageSize: 10, last: null, results: [], modeKind: "keyword" },
    indexSummary: null,
    indexTimer: null,
    loadSeq: 0,
    modal: null,
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  // ------------------------------------------------------------------ //
  // Utilities
  // ------------------------------------------------------------------ //
  const escapeHtml = (value) =>
    String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));

  function formatSize(bytes) {
    const value = Number(bytes || 0);
    if (value < 1024) return `${value} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let size = value / 1024;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return `${size >= 100 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`;
  }

  function formatDate(value) {
    if (!value) return "—";
    let text = String(value);
    // Backend timestamps are naive UTC; mark them so the browser converts to
    // the user's local time zone.
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(text)) text += "Z";
    const date = new Date(text);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("zh-CN", { hour12: false });
  }

  function formatScore(score) {
    const value = Number(score || 0);
    if (!Number.isFinite(value)) return "0";
    return Math.abs(value) >= 1 ? value.toFixed(3) : value.toFixed(4);
  }

  function fileIcon(name, mime) {
    const ext = String(name || "").toLowerCase().split(".").pop();
    if (["md", "markdown", "txt", "log"].includes(ext)) return "📝";
    if (["html", "htm"].includes(ext)) return "🌐";
    if (ext === "pdf") return "📕";
    if (["doc", "docx"].includes(ext)) return "📘";
    if (["ppt", "pptx"].includes(ext)) return "📙";
    if (["xls", "xlsx", "csv"].includes(ext)) return "📗";
    if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼️";
    if (["zip", "rar", "7z", "tar", "gz"].includes(ext)) return "📦";
    if (mime && mime.startsWith("image/")) return "🖼️";
    if (mime && mime.startsWith("audio/")) return "🎵";
    if (mime && mime.startsWith("video/")) return "🎬";
    return "📄";
  }

  let toastTimer = null;
  function toast(message, type = "info", duration = 3400) {
    const node = $("#toast");
    node.textContent = message;
    node.className = `toast ${type}`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.add("hidden"), duration);
  }

  class ApiError extends Error {
    constructor(message, code = "HTTP_ERROR", status = 0) {
      super(message);
      this.code = code;
      this.status = status;
    }
  }

  function authHeader() {
    return state.token ? { Authorization: `Bearer ${state.token}` } : {};
  }

  async function api(path, options = {}) {
    const request = { method: options.method || "GET", headers: { ...(options.headers || {}) } };
    Object.assign(request.headers, authHeader());
    if (options.body !== undefined) {
      if (options.body instanceof FormData) {
        request.body = options.body;
      } else {
        request.headers["Content-Type"] = "application/json";
        request.body = JSON.stringify(options.body);
      }
    }
    let response;
    try {
      response = await fetch(`/api${path}`, request);
    } catch (networkError) {
      throw new ApiError("无法连接服务器，请检查服务是否启动", "NETWORK_ERROR", 0);
    }

    let payload = null;
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try { payload = await response.json(); } catch { payload = null; }
    }

    if (response.status === 401 && state.token && !path.startsWith("/auth/")) {
      logout(false);
    }
    if (!response.ok) {
      const error = (payload && payload.error) || {};
      throw new ApiError(
        error.message || `请求失败（HTTP ${response.status}）`,
        error.code || "HTTP_ERROR",
        response.status
      );
    }
    return payload;
  }

  // ------------------------------------------------------------------ //
  // Session / auth
  // ------------------------------------------------------------------ //
  function setAuthMode(mode) {
    state.authMode = mode;
    $$(".auth-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.authTab === mode));
    $("#auth-submit").textContent = mode === "login" ? "登录" : "注册并登录";
    $("#auth-password").autocomplete = mode === "login" ? "current-password" : "new-password";
    $("#auth-error").textContent = "";
  }

  async function handleAuth(event) {
    event.preventDefault();
    const username = $("#auth-username").value.trim();
    const password = $("#auth-password").value;
    const errorBox = $("#auth-error");
    errorBox.textContent = "";
    if (!username || !password) {
      errorBox.textContent = "请输入用户名和密码";
      return;
    }
    const button = $("#auth-submit");
    button.disabled = true;
    try {
      const data = await api(`/auth/${state.authMode}`, {
        method: "POST",
        body: { username, password },
      });
      state.token = data.access_token;
      localStorage.setItem(TOKEN_KEY, state.token);
      state.user = data.user;
      $("#auth-password").value = "";
      await enterApp();
    } catch (error) {
      errorBox.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function enterApp() {
    $("#auth-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    $("#username").textContent = state.user ? state.user.username : "";
    await loadRoot();
    startIndexPolling();
  }

  function clearSession() {
    state.token = "";
    state.user = null;
    state.rootId = null;
    state.cwd = null;
    state.items = [];
    state.search.last = null;
    state.search.results = [];
    localStorage.removeItem(TOKEN_KEY);
    if (state.indexTimer) { clearInterval(state.indexTimer); state.indexTimer = null; }
  }

  function logout(showMessage = true) {
    clearSession();
    $("#app-view").classList.add("hidden");
    $("#auth-view").classList.remove("hidden");
    $("#file-tbody").innerHTML = "";
    $("#folder-tree").innerHTML = "";
    if (showMessage) toast("已退出登录", "info");
  }

  async function restoreSession() {
    if (!state.token) return;
    try {
      state.user = await api("/auth/me");
      await enterApp();
    } catch {
      clearSession();
    }
  }

  // ------------------------------------------------------------------ //
  // Folders, tree and files
  // ------------------------------------------------------------------ //
  async function loadRoot() {
    const root = await api("/nodes/root");
    state.rootId = root.id;
    await loadFolder(root.id);
  }

  async function loadFolder(folderId) {
    const seq = ++state.loadSeq;
    try {
      const data = await api(`/nodes?parent_id=${folderId}&page_size=100&sort_by=name&order=asc`);
      if (seq !== state.loadSeq) return;
      state.cwd = data.folder;
      state.items = data.items;
      state.usage = data.usage;
      showFilesView();
      renderUsage();
      renderFiles();
      await Promise.allSettled([loadBreadcrumb(folderId), loadTree()]);
      loadIndexSummary();
    } catch (error) {
      if (error.status === 404) {
        toast("文件夹不存在，已返回根目录", "error");
        const root = await api("/nodes/root");
        state.rootId = root.id;
        await loadFolder(root.id);
        return;
      }
      toast(error.message, "error");
    }
  }

  async function loadBreadcrumb(folderId) {
    try {
      const data = await api(`/nodes/${folderId}/path`);
      const wrap = $("#breadcrumb");
      wrap.innerHTML = "";
      data.items.forEach((node, index) => {
        if (index > 0) {
          const separator = document.createElement("span");
          separator.className = "crumb-sep";
          separator.textContent = "/";
          wrap.appendChild(separator);
        }
        const button = document.createElement("button");
        button.className = "crumb" + (index === data.items.length - 1 ? " current" : "");
        button.textContent = node.name;
        if (index !== data.items.length - 1) {
          button.dataset.folder = node.id;
        }
        wrap.appendChild(button);
      });
    } catch { /* breadcrumb is non-critical */ }
  }

  async function loadTree() {
    try {
      const data = await api(`/nodes/tree?root_id=${state.rootId}&depth=8`);
      const wrap = $("#folder-tree");
      wrap.innerHTML = "";
      const build = (node, depth) => {
        const button = document.createElement("button");
        button.className = "tree-item" + (state.cwd && node.id === state.cwd.id ? " active" : "");
        button.style.setProperty("--depth", depth);
        button.dataset.folder = node.id;
        button.innerHTML = `<span class="tree-icon">📁</span><span class="tree-name">${escapeHtml(node.name)}</span>`;
        wrap.appendChild(button);
        (node.children || [])
          .filter((child) => child.node_type === "FOLDER")
          .forEach((child) => build(child, depth + 1));
      };
      build(data.root, 0);
      if (!wrap.children.length) wrap.innerHTML = '<div class="tree-empty">暂无文件夹</div>';
    } catch { /* tree is non-critical */ }
  }

  function renderUsage() {
    if (!state.usage) { $("#usage").textContent = ""; return; }
    $("#usage").textContent =
      `${state.usage.file_count} 个文件 · ${state.usage.folder_count} 个文件夹 · ` +
      `已用 ${formatSize(state.usage.total_bytes)}`;
  }

  function renderFiles() {
    const tbody = $("#file-tbody");
    tbody.innerHTML = "";
    const items = state.items || [];
    $("#empty-state").classList.toggle("hidden", items.length > 0);

    items.forEach((node) => {
      const row = document.createElement("tr");
      const isFolder = node.node_type === "FOLDER";
      const nameHtml = isFolder
        ? `<button class="name-link" data-open="${node.id}">${escapeHtml(node.name)}</button>`
        : `<span class="name-static">${escapeHtml(node.name)}</span>`;
      const actions = isFolder
        ? `<button class="btn ghost small" data-open="${node.id}">打开</button>
           <button class="btn ghost small" data-rename="${node.id}">重命名</button>
           <button class="btn ghost small" data-move="${node.id}">移动</button>
           <button class="btn ghost small danger" data-delete="${node.id}">删除</button>`
        : `<button class="btn ghost small" data-download="${node.id}">下载</button>
           <button class="btn ghost small" data-similar="${node.id}">相似</button>
           <button class="btn ghost small" data-rename="${node.id}">重命名</button>
           <button class="btn ghost small" data-move="${node.id}">移动</button>
           <button class="btn ghost small danger" data-delete="${node.id}">删除</button>`;
      row.innerHTML = `
        <td>
          <div class="name-cell">
            <span class="file-icon">${isFolder ? "📁" : fileIcon(node.name, node.mime_type)}</span>
            ${nameHtml}
          </div>
        </td>
        <td>${isFolder ? "—" : formatSize(node.file_size)}</td>
        <td>${formatDate(node.updated_at)}</td>
        <td><div class="row-actions">${actions}</div></td>`;
      tbody.appendChild(row);
    });
  }

  // ------------------------------------------------------------------ //
  // File operations
  // ------------------------------------------------------------------ //
  function findItem(id) {
    return state.items.find((item) => item.id === Number(id));
  }

  async function createFolder() {
    await openModal({
      title: "新建文件夹",
      html: `<label class="field"><span>文件夹名称</span>
               <input id="modal-input" maxlength="255" placeholder="例如：课程资料"></label>`,
      okText: "创建",
      onOk: async () => {
        const name = $("#modal-input").value.trim();
        if (!name) throw new Error("请输入文件夹名称");
        await api("/folders", { method: "POST", body: { name, parent_id: state.cwd.id } });
        toast("文件夹已创建", "success");
        await loadFolder(state.cwd.id);
        return true;
      },
    });
  }

  async function renameNode(id) {
    const node = findItem(id);
    if (!node) return;
    await openModal({
      title: "重命名",
      html: `<label class="field"><span>新名称</span>
               <input id="modal-input" maxlength="255" value="${escapeHtml(node.name)}"></label>`,
      okText: "保存",
      onOk: async () => {
        const name = $("#modal-input").value.trim();
        if (!name) throw new Error("名称不能为空");
        if (name === node.name) return true;
        await api(`/nodes/${node.id}`, { method: "PATCH", body: { name } });
        toast("已重命名", "success");
        await loadFolder(state.cwd.id);
        return true;
      },
    });
  }

  function collectFolderOptions(node, depth, excluded, lines) {
    if (node.node_type !== "FOLDER" || excluded.has(node.id)) return;
    lines.push({ id: node.id, label: `${"　".repeat(depth)}${depth ? "└ " : ""}${node.name}` });
    (node.children || []).forEach((child) => collectFolderOptions(child, depth + 1, excluded, lines));
  }

  async function moveNode(id) {
    const node = findItem(id);
    if (!node) return;
    const tree = await api(`/nodes/tree?root_id=${state.rootId}&depth=20`);
    const excluded = new Set();
    if (node.node_type === "FOLDER") {
      const mark = (current) => { excluded.add(current.id); (current.children || []).forEach(mark); };
      const findNode = (current) => {
        if (current.id === node.id) { mark(current); return true; }
        return (current.children || []).some(findNode);
      };
      findNode(tree.root);
    }
    const lines = [];
    collectFolderOptions(tree.root, 0, excluded, lines);
    const options = lines
      .map((line) => `<option value="${line.id}"${line.id === node.parent_id ? " selected" : ""}>${escapeHtml(line.label)}</option>`)
      .join("");
    await openModal({
      title: `移动「${node.name}」`,
      html: `<label class="field"><span>目标文件夹</span><select id="modal-select">${options}</select></label>`,
      okText: "移动",
      onOk: async () => {
        const target = Number($("#modal-select").value);
        if (!target) throw new Error("请选择目标文件夹");
        await api(`/nodes/${node.id}`, { method: "PATCH", body: { parent_id: target } });
        toast("已移动", "success");
        await loadFolder(state.cwd.id);
        return true;
      },
    });
  }

  async function deleteNode(id) {
    const node = findItem(id);
    if (!node) return;
    await openModal({
      title: "删除确认",
      html: `<p>确定要删除 <b>「${escapeHtml(node.name)}」</b> 吗？</p>
             <p class="muted small-text">${node.node_type === "FOLDER"
               ? "文件夹内的所有内容会递归软删除。"
               : "文件会从搜索结果中立即消失。"}物理内容只有在没有任何逻辑文件引用时才会清理。</p>`,
      okText: "删除",
      okClass: "danger",
      onOk: async () => {
        await api(`/nodes/${node.id}`, { method: "DELETE" });
        toast("已删除", "success");
        await loadFolder(state.cwd.id);
        return true;
      },
    });
  }

  async function downloadFile(id) {
    const node = findItem(id) || state.search.results.find((item) => item.file_id === Number(id));
    const name = node ? (node.name || `file-${id}`) : `file-${id}`;
    try {
      const response = await fetch(`/api/files/${id}/download`, { headers: authHeader() });
      if (!response.ok) {
        let message = `下载失败（HTTP ${response.status}）`;
        try {
          const payload = await response.json();
          if (payload && payload.error) message = payload.error.message;
        } catch { /* ignore */ }
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);
    } catch (error) {
      toast(error.message, "error", 4500);
    }
  }

  // ------------------------------------------------------------------ //
  // Upload
  // ------------------------------------------------------------------ //
  function uploadOne(file) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      if (state.cwd) form.append("parent_id", String(state.cwd.id));

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/files/upload");
      if (state.token) xhr.setRequestHeader("Authorization", `Bearer ${state.token}`);

      const progress = $("#upload-progress");
      const bar = $("#upload-bar");
      const pct = $("#upload-pct");
      $("#upload-name").textContent = `上传：${file.name}`;
      bar.style.width = "0%";
      pct.textContent = "0%";
      progress.classList.remove("hidden");

      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) return;
        const percent = Math.min(100, Math.round((event.loaded / event.total) * 100));
        bar.style.width = `${percent}%`;
        pct.textContent = `${percent}%`;
      };
      xhr.onerror = () => {
        progress.classList.add("hidden");
        reject(new Error("网络错误"));
      };
      xhr.onload = () => {
        progress.classList.add("hidden");
        let payload = null;
        try { payload = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) {
          const index = payload && payload.index;
          if (index && (index.keyword_status === "FAILED" || index.semantic_status === "FAILED")) {
            toast(`${file.name} 已保存，但部分索引失败，可在索引状态中重试`, "info", 5000);
          } else {
            toast(`${file.name} 上传成功`, "success");
          }
          resolve(payload);
        } else {
          const message = (payload && payload.error && payload.error.message) || `上传失败（HTTP ${xhr.status}）`;
          toast(message, "error", 5000);
          reject(new Error(message));
        }
      };
      xhr.send(form);
    });
  }

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    for (const file of files) {
      try { await uploadOne(file); } catch { /* continue with the next file */ }
    }
    await loadFolder(state.cwd.id);
    loadIndexSummary();
  }

  // ------------------------------------------------------------------ //
  // Search
  // ------------------------------------------------------------------ //
  function showFilesView() {
    $("#search-view").classList.add("hidden");
    $("#files-view").classList.remove("hidden");
  }

  function showSearchView() {
    $("#files-view").classList.add("hidden");
    $("#search-view").classList.remove("hidden");
  }

  function showBanner(message, kind = "info") {
    const banner = $("#semantic-banner");
    banner.textContent = message;
    banner.className = `banner ${kind}`;
  }

  function hideBanner() {
    $("#semantic-banner").classList.add("hidden");
    $("#semantic-banner").textContent = "";
  }

  function setSearchMeta(text) {
    $("#search-meta").textContent = text;
  }

  function resultCard(item) {
    const icon = item.node_type === "FOLDER" ? "📁" : fileIcon(item.name, item.mime_type);
    const snippet = item.highlight
      ? item.highlight
      : escapeHtml(item.snippet || "（该文件没有可展示的正文摘要）");
    return `
      <article class="result-card">
        <div class="result-head">
          <div class="result-title">
            <span class="file-icon">${icon}</span>
            <button data-goto="${item.file_id}">${escapeHtml(item.name)}</button>
          </div>
          <span class="result-score">相关度 ${formatScore(item.score)}</span>
        </div>
        <p class="result-snippet">${snippet}</p>
        <div class="result-meta">
          <span>${formatSize(item.file_size)}</span>
          <span>${formatDate(item.updated_at)}</span>
          <span>#${item.file_id}</span>
          <span class="result-actions">
            <button class="btn ghost small" data-download="${item.file_id}">下载</button>
            <button class="btn ghost small" data-similar="${item.file_id}">相似文件</button>
          </span>
        </div>
      </article>`;
  }

  function renderResults(items, { meta = "", pagination = false } = {}) {
    state.search.results = items || [];
    const wrap = $("#search-results");
    if (!items || !items.length) {
      wrap.innerHTML = `<div class="empty"><div class="empty-icon">🔎</div><p>没有找到匹配的文件</p>
        <p class="muted">试试更短的关键词，或切换搜索模式</p></div>`;
    } else {
      wrap.innerHTML = items.map(resultCard).join("");
    }
    setSearchMeta(meta);
    $("#search-pagination").classList.toggle("hidden", !pagination);
  }

  async function runKeywordSearch(query, page) {
    hideBanner();
    const mode = state.search.mode;
    setSearchMeta("正在检索关键词索引…");
    $("#search-results").innerHTML = "";
    try {
      const data = await api(
        `/search/keyword?q=${encodeURIComponent(query)}&mode=${encodeURIComponent(mode)}&page=${page}&page_size=${state.search.pageSize}`
      );
      state.search.last = data;
      const total = Number(data.total || 0);
      const pages = Math.max(1, Math.ceil(total / state.search.pageSize));
      state.search.page = Math.min(page, pages);
      const metaBits = [
        `${total} 个可访问结果`,
        `${data.elapsed_ms} ms`,
        data.engine || "",
      ].filter(Boolean);
      if (data.truncated) metaBits.push("候选结果已截断，可缩小关键词范围");
      renderResults(data.results, { meta: metaBits.join(" · "), pagination: true });
      $("#search-page-label").textContent = `第 ${state.search.page} / ${pages} 页`;
      $("#search-prev").disabled = state.search.page <= 1;
      $("#search-next").disabled = state.search.page >= pages;
    } catch (error) {
      state.search.last = null;
      if (error.status === 503) {
        showBanner("关键词搜索服务暂不可用；文件上传、下载和文件管理不受影响。", "error");
      } else {
        showBanner(error.message, "error");
      }
      renderResults([], { meta: "搜索失败", pagination: false });
    }
  }

  async function runSemanticSearch(query) {
    hideBanner();
    setSearchMeta("正在使用语义向量索引检索…");
    $("#search-results").innerHTML = "";
    try {
      const data = await api(`/search/semantic?q=${encodeURIComponent(query)}&top_k=20`);
      if (data.message) showBanner(data.message, data.degraded ? "info" : "info");
      const meta = [
        `${data.count} 个语义结果`,
        `${data.elapsed_ms} ms`,
        data.model_version ? `模型 ${data.model_version}` : "",
        data.engine || "",
      ].filter(Boolean).join(" · ");
      renderResults(data.results, { meta, pagination: false });
      if (!data.results.length && !data.message) {
        showBanner("语义索引中没有匹配内容。若刚上传文件，请先确认索引状态。", "info");
      }
    } catch (error) {
      showBanner("语义搜索暂不可用，可继续使用关键词搜索；文件管理不受影响。", "error");
      renderResults([], { meta: "语义搜索不可用", pagination: false });
      toast(error.message, "error", 5000);
    }
  }

  async function doSearch(resetPage = true) {
    const query = $("#search-input").value.trim();
    if (!query) { toast("请输入搜索内容", "error"); return; }
    const mode = $("#search-mode").value;
    state.search.mode = mode;
    state.search.query = query;
    if (resetPage) state.search.page = 1;
    showSearchView();
    hideBanner();

    if (mode === "semantic") {
      $("#search-title").textContent = `语义搜索：${query}`;
      $("#search-pagination").classList.add("hidden");
      await runSemanticSearch(query);
    } else {
      $("#search-title").textContent = `关键词搜索：${query}`;
      await runKeywordSearch(query, state.search.page);
    }
  }

  async function goToFile(fileId) {
    try {
      const detail = await api(`/nodes/${fileId}`);
      state.search.page = 1;
      await loadFolder(detail.node.parent_id || state.rootId);
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function showSimilar(fileId) {
    let node;
    try {
      const detail = await api(`/nodes/${fileId}`);
      node = detail.node;
    } catch (error) {
      toast(error.message, "error");
      return;
    }
    showSearchView();
    hideBanner();
    $("#search-title").textContent = `与「${node.name}」相似的文件`;
    setSearchMeta("正在检索相似文件…");
    $("#search-results").innerHTML = "";
    $("#search-pagination").classList.add("hidden");
    try {
      const data = await api(`/files/${fileId}/similar?top_k=12`);
      renderResults(data.results, {
        meta: `${data.count} 个相似文件 · 模型 ${data.model_version || "-"}`,
        pagination: false,
      });
      if (!data.results.length) {
        showBanner("暂未找到相似文件。该文件可能还没有语义索引，或索引中没有其它内容。", "info");
      }
    } catch (error) {
      showBanner("语义服务暂不可用，无法计算相似文件。", "error");
      renderResults([], { meta: "相似文件不可用", pagination: false });
      toast(error.message, "error", 5000);
    }
  }

  // ------------------------------------------------------------------ //
  // Index status
  // ------------------------------------------------------------------ //
  function serviceDot(health) {
    if (!health || !health.healthy) return "bad";
    return health.status === "degraded" ? "warn" : "ok";
  }

  function renderIndexSummary(data) {
    const failed = Math.max(data.keyword_failed || 0, data.semantic_failed || 0);
    $("#index-summary").innerHTML = `
      <div class="index-line">
        <span class="label"><span class="service-dot ${serviceDot(data.search_service)}"></span>关键词</span>
        <span class="value">${data.keyword_ready} / ${data.total_files}</span>
      </div>
      <div class="index-line">
        <span class="label"><span class="service-dot ${serviceDot(data.semantic_service)}"></span>语义</span>
        <span class="value">${data.semantic_ready} / ${data.total_files}</span>
      </div>
      ${failed ? `<div class="index-line"><span class="label">失败待重试</span><span class="value" style="color:#ff9caf">${failed}</span></div>` : ""}
      ${data.semantic_service && data.semantic_service.status === "degraded"
        ? '<div class="index-line"><span class="label" style="font-size:12px">语义：离线降级模式</span></div>' : ""}
    `;
  }

  async function loadIndexSummary() {
    if (!state.token) return;
    try {
      const data = await api("/index/status");
      state.indexSummary = data;
      renderIndexSummary(data);
    } catch {
      $("#index-summary").innerHTML = '<div class="index-line"><span class="label">索引状态不可用</span></div>';
    }
  }

  function startIndexPolling() {
    if (state.indexTimer) clearInterval(state.indexTimer);
    state.indexTimer = setInterval(() => {
      if (!$("#app-view").classList.contains("hidden")) loadIndexSummary();
    }, 25000);
  }

  function statusBadge(status) {
    return `<span class="badge ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
  }

  async function loadIndexDetails() {
    const filter = $("#index-filter").value;
    const tbody = $("#index-tbody");
    tbody.innerHTML = `<tr><td colspan="5" class="muted">加载中…</td></tr>`;
    try {
      const data = await api(`/index/files?page_size=100&status_filter=${encodeURIComponent(filter)}`);
      const summary = state.indexSummary || await api("/index/status");
      const services = $("#index-services");
      const searchHealth = summary.search_service || {};
      const semanticHealth = summary.semantic_service || {};
      services.innerHTML = `
        <div class="health-chip"><span class="service-dot ${serviceDot(searchHealth)}"></span>
          关键词服务：<b>${escapeHtml(searchHealth.status || "unknown")}</b><br>
          <span class="muted">${escapeHtml(searchHealth.details || "")}</span></div>
        <div class="health-chip"><span class="service-dot ${serviceDot(semanticHealth)}"></span>
          语义服务：<b>${escapeHtml(semanticHealth.status || "unknown")}</b><br>
          <span class="muted">${escapeHtml(semanticHealth.details || "")}</span></div>`;

      if (!data.items.length) {
        tbody.innerHTML = `<tr><td colspan="5" class="muted">${filter ? "没有符合筛选条件的文件" : "还没有文件"}</td></tr>`;
        return;
      }
      tbody.innerHTML = data.items.map((item) => {
        const idx = item.index;
        const error = idx.last_error
          ? `<div class="error-detail" title="${escapeHtml(idx.last_error)}">${escapeHtml(idx.last_error.slice(0, 220))}</div>`
          : "";
        return `<tr>
          <td><div class="name-cell"><span class="file-icon">${fileIcon(item.name, item.mime_type)}</span>
            <div><div class="name-static">${escapeHtml(item.name)}</div>
            <div class="muted small-text">v${item.version} · #${item.file_id}</div>${error}</div></div></td>
          <td>${statusBadge(idx.keyword_status)}</td>
          <td>${statusBadge(idx.semantic_status)}</td>
          <td>${idx.retry_count}</td>
          <td><button class="btn ghost small" data-retry="${item.file_id}">重试</button></td>
        </tr>`;
      }).join("");
    } catch (error) {
      tbody.innerHTML = `<tr><td colspan="5" class="error-detail">${escapeHtml(error.message)}</td></tr>`;
    }
  }

  async function openIndexModal() {
    $("#index-modal").classList.remove("hidden");
    if (!state.indexSummary) await loadIndexSummary();
    await loadIndexDetails();
  }

  function closeIndexModal() {
    $("#index-modal").classList.add("hidden");
  }

  async function retryIndex(fileId, button) {
    button.disabled = true;
    button.textContent = "重试中…";
    try {
      const data = await api(`/index/${fileId}/retry`, { method: "POST" });
      const ok = data.index.keyword_status === "READY" && data.index.semantic_status === "READY";
      toast(ok ? "索引已恢复" : "索引仍然失败，请查看错误信息", ok ? "success" : "error", 4500);
      await loadIndexDetails();
      await loadIndexSummary();
    } catch (error) {
      toast(error.message, "error", 5000);
    } finally {
      button.disabled = false;
      button.textContent = "重试";
    }
  }

  async function rebuildIndexes(kind) {
    const labels = { keyword: "关键词", semantic: "语义", all: "全部" };
    const buttons = $$("#index-modal [data-rebuild]");
    buttons.forEach((button) => { button.disabled = true; });
    toast(`正在重建${labels[kind] || ""}索引，请稍候…`, "info", 5000);
    try {
      const data = await api("/index/rebuild", { method: "POST", body: { kinds: kind } });
      const parts = [];
      if (data.keyword) parts.push(`关键词：${data.keyword.ok ? "成功" : "失败"}（${data.keyword.indexed || 0} 个）`);
      if (data.semantic) parts.push(`语义：${data.semantic.ok ? "成功" : "失败"}（${data.semantic.chunks || 0} 切片）`);
      const hasError = (data.keyword && !data.keyword.ok) || (data.semantic && !data.semantic.ok);
      toast(`重建完成 · ${parts.join("；") || "无文件"}`, hasError ? "error" : "success", 6000);
      await loadIndexSummary();
      await loadIndexDetails();
    } catch (error) {
      toast(error.message, "error", 6000);
    } finally {
      buttons.forEach((button) => { button.disabled = false; });
    }
  }

  // ------------------------------------------------------------------ //
  // Modal
  // ------------------------------------------------------------------ //
  function openModal({ title, html, okText = "确定", okClass = "primary", onOk }) {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = html;
    $("#modal-ok").textContent = okText;
    $("#modal-ok").className = `btn ${okClass}`;
    $("#modal").classList.remove("hidden");
    setTimeout(() => {
      const input = $("#modal-body input, #modal-body select");
      if (input) input.focus();
    }, 30);
    return new Promise((resolve) => {
      state.modal = { resolve, onOk };
    });
  }

  function closeModal(result) {
    $("#modal").classList.add("hidden");
    const current = state.modal;
    state.modal = null;
    if (current) current.resolve(result ?? null);
  }

  async function confirmModal() {
    if (!state.modal) return;
    try {
      const result = await state.modal.onOk();
      if (result === false) return;
      closeModal(result === undefined ? true : result);
    } catch (error) {
      toast(error.message || "操作失败", "error", 4500);
    }
  }

  // ------------------------------------------------------------------ //
  // Events
  // ------------------------------------------------------------------ //
  function bindEvents() {
    $$(".auth-tab").forEach((tab) => tab.addEventListener("click", () => setAuthMode(tab.dataset.authTab)));
    $("#auth-form").addEventListener("submit", handleAuth);
    $("#logout-btn").addEventListener("click", () => logout(true));

    $("#refresh-btn").addEventListener("click", () => state.cwd && loadFolder(state.cwd.id));
    $("#index-refresh").addEventListener("click", loadIndexSummary);
    $("#index-detail-btn").addEventListener("click", openIndexModal);
    $("#index-close").addEventListener("click", closeIndexModal);
    $("#index-filter").addEventListener("change", loadIndexDetails);

    $("#new-folder-btn").addEventListener("click", createFolder);
    $("#upload-btn").addEventListener("click", () => $("#file-input").click());
    $("#file-input").addEventListener("change", (event) => {
      const files = Array.from(event.target.files || []);
      event.target.value = "";
      uploadFiles(files);
    });

    const dropZone = $("#drop-zone");
    ["dragenter", "dragover"].forEach((name) =>
      dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.add("dragover"); }));
    ["dragleave", "drop"].forEach((name) =>
      dropZone.addEventListener(name, (event) => { event.preventDefault(); dropZone.classList.remove("dragover"); }));
    dropZone.addEventListener("drop", (event) => {
      const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
      if (files.length) uploadFiles(files);
    });

    $("#search-btn").addEventListener("click", () => doSearch(true));
    $("#search-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); doSearch(true); }
    });
    $("#search-mode").addEventListener("change", () => {
      const mode = $("#search-mode").value;
      $("#search-input").placeholder = mode === "semantic"
        ? "用自然语言描述你想找的内容，例如：讲机器学习的文档"
        : "搜索文件名、正文…";
    });
    $("#back-to-files").addEventListener("click", showFilesView);
    $("#search-prev").addEventListener("click", () => {
      if (state.search.page <= 1) return;
      state.search.page -= 1;
      runKeywordSearch(state.search.query, state.search.page);
    });
    $("#search-next").addEventListener("click", () => {
      state.search.page += 1;
      runKeywordSearch(state.search.query, state.search.page);
    });

    // delegated actions
    $("#file-tbody").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const { open, download, rename, move, delete: remove, similar } = button.dataset;
      if (open) loadFolder(Number(open));
      else if (download) downloadFile(Number(download));
      else if (rename) renameNode(Number(rename));
      else if (move) moveNode(Number(move));
      else if (remove) deleteNode(Number(remove));
      else if (similar) showSimilar(Number(similar));
    });

    $("#folder-tree").addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder]");
      if (button) loadFolder(Number(button.dataset.folder));
    });
    $("#breadcrumb").addEventListener("click", (event) => {
      const button = event.target.closest("[data-folder]");
      if (button) loadFolder(Number(button.dataset.folder));
    });
    $("#search-results").addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      const { goto, download, similar } = button.dataset;
      if (goto) goToFile(Number(goto));
      else if (download) downloadFile(Number(download));
      else if (similar) showSimilar(Number(similar));
    });
    $("#index-tbody").addEventListener("click", (event) => {
      const button = event.target.closest("[data-retry]");
      if (button) retryIndex(Number(button.dataset.retry), button);
    });
    $$("#index-modal [data-rebuild]").forEach((button) =>
      button.addEventListener("click", () => rebuildIndexes(button.dataset.rebuild)));

    $("#modal-ok").addEventListener("click", confirmModal);
    $("#modal-cancel").addEventListener("click", () => closeModal(null));
    $("#modal").addEventListener("mousedown", (event) => {
      if (event.target.id === "modal") closeModal(null);
    });
    $("#modal-body").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); confirmModal(); }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      if (!$("#modal").classList.contains("hidden")) closeModal(null);
      else if (!$("#index-modal").classList.contains("hidden")) closeIndexModal();
    });
  }

  // ------------------------------------------------------------------ //
  // Boot
  // ------------------------------------------------------------------ //
  bindEvents();
  setAuthMode("login");
  restoreSession();
})();
