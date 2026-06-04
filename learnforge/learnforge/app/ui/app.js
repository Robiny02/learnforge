const conversation = document.querySelector("#conversation");
const composer = document.querySelector("#composer");
const promptInput = document.querySelector("#promptInput");
const modeSelect = document.querySelector("#modeSelect");
const modeTabs = Array.from(document.querySelectorAll("#modeTabs button"));
const attachBtn = document.querySelector("#attachBtn");
const fileInput = document.querySelector("#fileInput");
const attachSlot = document.querySelector("#attachSlot");
const attachEmpty = document.querySelector("#attachEmpty");
const contextPct = document.querySelector("#contextPct");
const contextFill = document.querySelector("#contextFill");
const statusContext = document.querySelector("#statusContext");

// 待发送的本地附件（背包栏 → 随下一条消息发送）。
let pendingAttachments = [];  // [{filename, mime, data(dataURL)}]
let lastMemoryTokens = 0;
const CONTEXT_BUDGET_TOKENS = 128000;

function estimateTokens(value) {
  const text = String(value || "");
  if (!text) return 0;
  let cjk = 0;
  for (const ch of text) {
    if (("一" <= ch && ch <= "鿿") || ("぀" <= ch && ch <= "ヿ") || ("가" <= ch && ch <= "힣")) {
      cjk += 1;
    }
  }
  return Math.ceil(cjk + (text.length - cjk) / 4);
}

function updateContextUsage(extraTokens = 0) {
  const historyText = state.history
    .filter((item) => item.k === "msg" || item.k === "system" || item.k === "actions")
    .map((item) => item.text || (item.actions || []).join("\n"))
    .join("\n");
  const pendingText = pendingAttachments.map((a) => `${a.filename} ${a.mime}`).join("\n");
  const used = Math.max(0, estimateTokens(historyText) + estimateTokens(pendingText) + lastMemoryTokens + extraTokens);
  const pct = Math.min(100, Math.round((used / CONTEXT_BUDGET_TOKENS) * 100));
  if (contextPct) contextPct.textContent = `${pct}%`;
  if (contextFill) {
    contextFill.style.width = `${pct}%`;
    contextFill.classList.toggle("warn", pct >= 65 && pct < 85);
    contextFill.classList.toggle("danger", pct >= 85);
  }
  if (statusContext) statusContext.textContent = `${pct}% · ${used.toLocaleString()} tok`;
}

function renderAttachChips() {
  if (!attachSlot) return;
  attachSlot.innerHTML = "";
  attachSlot.hidden = pendingAttachments.length === 0;
  if (attachEmpty) attachEmpty.hidden = pendingAttachments.length > 0;
  pendingAttachments.forEach((a, i) => {
    const chip = document.createElement("span");
    chip.className = "attach-chip";
    const kind = a.mime.startsWith("image/") ? "IMG" : a.mime.includes("pdf") ? "PDF" : "TXT";
    chip.innerHTML = `<span class="attach-kind">${kind}</span><span class="attach-name">${a.filename}</span><button type="button" class="attach-x" aria-label="移除">×</button>`;
    chip.querySelector(".attach-x").addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderAttachChips();
    });
    attachSlot.appendChild(chip);
  });
  updateContextUsage();
}

function readFileAsDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

if (attachBtn && fileInput) {
  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", async () => {
    for (const file of Array.from(fileInput.files || [])) {
      if (file.size > 15 * 1024 * 1024) {  // 15MB 上限，避免内联 base64 过大
        addMessage("agent", `附件 ${file.name} 过大（>15MB），已跳过。`, "LearnForge · error");
        continue;
      }
      try {
        const dataUrl = await readFileAsDataURL(file);
        pendingAttachments.push({ filename: file.name, mime: file.type || "", data: dataUrl });
      } catch (e) { /* 单文件读失败忽略 */ }
    }
    fileInput.value = "";  // 允许重复选同一文件
    renderAttachChips();
  });
}
const activityLog = document.querySelector("#activityLog");
const agentChain = document.querySelector("#agentChain");
const chainStatus = document.querySelector("#chainStatus");
const traceId = document.querySelector("#traceId");
const statusMode = document.querySelector("#statusMode");
const sessionId = document.querySelector("#sessionId");
const viewButtons = Array.from(document.querySelectorAll(".view-tabs button"));
const views = Array.from(document.querySelectorAll(".view"));
const fileList = document.querySelector("#fileList");
const filePreview = document.querySelector("#filePreview");
const mockToggle = document.querySelector("#mockToggle");
const memoryLog = document.querySelector("#memoryLog");
const memTokens = document.querySelector("#memTokens");
const convList = document.querySelector("#convList");
const newChatBtn = document.querySelector("#newChatBtn");

const GREETING =
  "早上好，今天的学习地块已经晒到太阳了。把一个概念、项目问题或面试主题丢过来，我们先从最需要浇水的地方开始。";

const state = {
  mode: "qa",
  sessionId: "",  // 启动时由 initConversations() 设为活跃对话；每个对话 = 一个 session_id。
  mock: { active: false, sessionId: null },
  history: [],
  replaying: false,
};

const modeCopy = {
  qa: "问一个概念、项目或复习问题...",
  diagnose: "输入你想聚焦诊断的 topic，可留空...",
  plan: "描述你的学习目标和截止时间...",
  note: "输入主题，例如 Redis 分布式锁、MVCC、TCP 挥手...",
  mock: "输入模拟面试主题，例如 并发、Redis、系统设计...",
};

const agentLabels = {
  manager: ["Manager", "拆解请求，生成调用计划"],
  qa: ["QA", "路由、检索、合成与校验"],
  diagnosis: ["Diagnosis", "只读读取事件和 mastery，找薄弱点"],
  planning: ["Planning", "生成或修改学习路径"],
  mock: ["Mock", "启动模拟面试子图"],
  note: ["Note", "生成结构化学习笔记"],
};

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// 聊天持久化：把每条渲染过的消息记进 history → localStorage；刷新后回放。
const HISTORY_KEY = () => `lf_chat_${state.sessionId}`;

function saveHistory() {
  if (state.replaying) return;
  try {
    localStorage.setItem(HISTORY_KEY(), JSON.stringify(state.history.slice(-200)));
  } catch (e) { /* 隐私模式 / 配额 → 忽略 */ }
}

function pushHistory(entry) {
  if (state.replaying) return;
  state.history.push(entry);
  saveHistory();
  updateContextUsage();
}

// 载入当前 session 的聊天记录并回放到界面（无记录 → 显示欢迎语）。
// 回放期间 state.replaying=true，避免回放本身又被写回 history。
function loadTranscript(showRestoredNote) {
  let items = [];
  try {
    const raw = localStorage.getItem(HISTORY_KEY());
    if (raw) items = JSON.parse(raw);
  } catch (e) { items = []; }
  state.replaying = true;
  conversation.innerHTML = "";
  if (Array.isArray(items) && items.length) {
    items.forEach((it) => {
      if (it.k === "msg") addMessage(it.role, it.text, it.meta);
      else if (it.k === "system") addSystem(it.text);
      else if (it.k === "actions") addActions(it.actions);
      else if (it.k === "image") addImage(it.url, it.caption);
      else if (it.k === "score") addScore(it.score);
    });
    state.history = items;
    if (showRestoredNote) addSystem("（已载入历史对话，上下文已带上，可继续。）");
  } else {
    state.history = [];
    addMessage("agent", GREETING, "LearnForge · Manager");
  }
  state.replaying = false;
  updateContextUsage();
}

// ---------------- 多对话管理（Claude 风格左侧列表）----------------
// 每个对话 = 一个 session_id；切换对话即切 session_id，后端 session_state 据此带上其上下文。
const CONV_INDEX_KEY = "lf_conv_index";   // [{id, title, updatedAt}]，按 updatedAt 倒序
const ACTIVE_KEY = "lf_active_session";

function readConvIndex() {
  try { return JSON.parse(localStorage.getItem(CONV_INDEX_KEY) || "[]"); }
  catch (e) { return []; }
}
function writeConvIndex(idx) {
  try { localStorage.setItem(CONV_INDEX_KEY, JSON.stringify(idx)); } catch (e) { /* 忽略 */ }
}
function newSessionId() {
  return `ui-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 6)}`;
}

function setActiveSession(id) {
  state.sessionId = id;
  try { localStorage.setItem(ACTIVE_KEY, id); } catch (e) { /* 忽略 */ }
  if (sessionId) sessionId.textContent = id;
}

function resetMemoryPanel() {
  if (memoryLog) memoryLog.innerHTML = `<p class="log-line"><span>Idle</span> 等待对话，加载的 prompt / 文件会显示在这里</p>`;
  if (memTokens) memTokens.textContent = "0 tok";
  lastMemoryTokens = 0;
  updateContextUsage();
}

function renderConvList() {
  if (!convList) return;
  const idx = readConvIndex();
  convList.innerHTML = "";
  if (!idx.length) {
    convList.innerHTML = `<p class="conv-empty">还没有对话</p>`;
    return;
  }
  idx.forEach((conv) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `conv-item${conv.id === state.sessionId ? " active" : ""}`;
    const when = conv.updatedAt ? new Date(conv.updatedAt) : new Date();
    item.innerHTML =
      `<strong>${escapeHtml(conv.title || "新对话")}</strong>` +
      `<small>${escapeHtml(when.toLocaleString())}</small>` +
      `<span class="conv-del" title="删除对话">×</span>`;
    item.addEventListener("click", () => switchConversation(conv.id));
    const del = item.querySelector(".conv-del");
    if (del) del.addEventListener("click", (e) => { e.stopPropagation(); deleteConversation(conv.id); });
    convList.appendChild(item);
  });
}

// 本轮收尾：刷新当前对话的标题(取首条用户消息)/时间戳，并置顶。
function touchConversation(firstUserText) {
  const idx = readConvIndex();
  let conv = idx.find((c) => c.id === state.sessionId);
  if (!conv) conv = { id: state.sessionId, title: "新对话", updatedAt: Date.now() };
  if ((!conv.title || conv.title === "新对话") && firstUserText) {
    conv.title = firstUserText.slice(0, 24);
  }
  conv.updatedAt = Date.now();
  writeConvIndex([conv, ...idx.filter((c) => c.id !== conv.id)]);
  renderConvList();
}

function switchConversation(id) {
  if (id === state.sessionId) return;
  if (state.mock.active) setMockActive(false);
  setActiveSession(id);
  loadTranscript(true);
  resetMemoryPanel();
  renderConvList();
}

function newConversation() {
  if (state.mock.active) setMockActive(false);
  const id = newSessionId();
  writeConvIndex([{ id, title: "新对话", updatedAt: Date.now() }, ...readConvIndex()]);
  setActiveSession(id);
  loadTranscript(false);  // 空 → 显示欢迎语
  resetMemoryPanel();
  renderConvList();
}

function deleteConversation(id) {
  writeConvIndex(readConvIndex().filter((c) => c.id !== id));
  try { localStorage.removeItem(`lf_chat_${id}`); } catch (e) { /* 忽略 */ }
  if (id === state.sessionId) {
    const idx = readConvIndex();
    if (idx.length) switchConversation(idx[0].id);
    else newConversation();
  } else {
    renderConvList();
  }
}

// 启动：选中活跃对话(没有则建一个)，载入其记录并渲染列表。
function initConversations() {
  let active = null;
  try { active = localStorage.getItem(ACTIVE_KEY); } catch (e) { /* 忽略 */ }
  const idx = readConvIndex();
  if (active && idx.some((c) => c.id === active)) {
    setActiveSession(active);
  } else if (idx.length) {
    setActiveSession(idx[0].id);
  } else {
    const id = newSessionId();
    writeConvIndex([{ id, title: "新对话", updatedAt: Date.now() }]);
    setActiveSession(id);
  }
  loadTranscript(true);
  renderConvList();
}

if (newChatBtn) newChatBtn.addEventListener("click", newConversation);

// 记忆面板：展示本轮注入 prompt 的来源(文件名·摘要·token) + 记忆读写事件。
const MEM_CAT = { read: "读", inject: "注", write: "写", maintain: "维" };

function renderMemoryLog(mem) {
  if (!memoryLog) return;
  if (!mem) return;
  const loaded = Array.isArray(mem.loaded) ? mem.loaded : [];
  const events = Array.isArray(mem.events) ? mem.events : [];
  const total = loaded.reduce((acc, x) => acc + (x.tokens || 0), 0);
  lastMemoryTokens = total;
  if (memTokens) memTokens.textContent = `${total} tok`;
  const rows = [];
  loaded.forEach((x) => {
    const summary = x.summary ? ` · ${escapeHtml(x.summary)}` : "";
    rows.push(
      `<p class="log-line mem-loaded"><span>${escapeHtml(x.kind || "load")}</span>` +
      `${escapeHtml(x.name || "")}${summary} · ~${x.tokens || 0} tok</p>`
    );
  });
  events.forEach((e) => {
    const reason = e.reason ? `（${escapeHtml(e.reason)}）` : "";
    rows.push(
      `<p class="log-line mem-evt"><span>${escapeHtml(MEM_CAT[e.category] || "·")}</span>` +
      `${escapeHtml(e.action)}：${escapeHtml(e.result)}${reason}</p>`
    );
  });
  if (!rows.length) {
    rows.push(`<p class="log-line"><span>Memory</span>本轮未经记忆流水线（fast / mock 路径）</p>`);
  }
  memoryLog.innerHTML = rows.join("");
  updateContextUsage();
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function renderRichText(value) {
  const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let listType = null;

  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };

  lines.forEach((raw) => {
    const line = raw.trim();
    if (!line) {
      closeList();
      return;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 5);
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      return;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${inlineMarkdown(bullet[1])}</li>`);
      return;
    }
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    if (ordered) {
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${inlineMarkdown(ordered[1])}</li>`);
      return;
    }
    closeList();
    html.push(`<p>${inlineMarkdown(line)}</p>`);
  });
  closeList();
  return `<div class="rich-text">${html.join("") || "<p></p>"}</div>`;
}

function setView(name) {
  viewButtons.forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  views.forEach((view) => view.classList.toggle("active", view.id === `view-${name}`));
}

viewButtons.forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

function setMode(mode) {
  state.mode = mode;
  promptInput.placeholder = modeCopy[mode] || modeCopy.qa;
  statusMode.textContent = mode.toUpperCase();
  if (modeSelect) modeSelect.value = mode;
  modeTabs.forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
}

if (modeSelect) modeSelect.addEventListener("change", () => setMode(modeSelect.value));
modeTabs.forEach((button) => {
  button.addEventListener("click", () => setMode(button.dataset.mode || "qa"));
});

// 意图判定（作答/插问/退出/暂停 + 退出确认）全部由后端轻量 LLM 语义完成，前端不再硬编码关键词。

// 状态持久化：长会话 / 刷新后不丢失“正在面试”及当前题目。
function persistMock() {
  try {
    localStorage.setItem("lf_mock", JSON.stringify(state.mock));
  } catch (e) { /* 隐私模式等 → 忽略 */ }
}
function restoreMock() {
  try {
    const raw = localStorage.getItem("lf_mock");
    if (!raw) return;
    const m = JSON.parse(raw);
    if (m && m.active && m.sessionId) {
      Object.assign(state.mock, m);
      setMockActive(true, m.sessionId);
      addSystem(`已恢复进行中的模拟面试（已答 ${state.mock.turns || 0} 轮）。`);
      if (state.mock.lastQuestion) addMessage("agent", state.mock.lastQuestion, "LearnForge · Interviewer");
    }
  } catch (e) { /* 损坏数据 → 忽略 */ }
}

// mock 进行中：切换控件文案/状态条/输入提示。mode 仍可用（切到 QA 等即可插问）。
function setMockActive(active, sessionId) {
  state.mock.active = active;
  state.mock.sessionId = active ? sessionId : null;
  if (!active) {
    state.mock.pendingExit = false;
    state.mock.lastQuestion = "";
    state.mock.turns = 0;
  }
  if (mockToggle) {
    mockToggle.textContent = active ? "结束 Mock" : "开始 Mock";
    mockToggle.setAttribute("aria-pressed", active ? "true" : "false");
    mockToggle.classList.toggle("active", active);
  }
  if (active) {
    statusMode.textContent = "MOCK";
    promptInput.placeholder = "直接作答；问号/“解释一下”可插问；说“结束”出复盘…";
  } else {
    setMode((modeSelect && modeSelect.value) || state.mode || "qa");
  }
  persistMock();
}

// 显式点"结束 Mock"按钮 = 明确退出意图，前端直接进入确认（无需 LLM 判意图）。
function requestExitConfirm() {
  state.mock.pendingExit = true;
  persistMock();
  addSystem(`确定结束本场模拟面试吗？已进行 ${state.mock.turns || 0} 轮。回复“确定”出复盘与诊断图，或继续作答以取消。`);
}

// 进行中 mock 的输入分流：显式切到非 Mock 子模式 = 明确插问；否则 auto（后端 4 类语义分流）。
async function dispatchMockInput(text, addUser) {
  if (modeSelect && modeSelect.value !== "mock") return sendPrompt(text, { mockAction: "side", addUser });
  return sendPrompt(text, { mockAction: "auto", addUser });
}

// 顶层输入路由：mock 进行中，意图（含退出/确认）交后端语义判定。
async function routeUserText(text) {
  if (!state.mock.active) {
    await sendPrompt(text);
    return;
  }
  if (state.mock.pendingExit) {
    // 等待退出确认：这条回复交后端语义判 confirm/continue。
    await sendPrompt(text, { mockAction: "confirm_exit", addUser: true });
    return;
  }
  await dispatchMockInput(text, true);
}

if (mockToggle) {
  mockToggle.addEventListener("click", async () => {
    if (state.mock.active) {
      // 已在等待确认 → 再点即确认结束；否则发起确认。
      if (state.mock.pendingExit) {
        state.mock.pendingExit = false;
        persistMock();
        await sendPrompt("结束面试", { mockAction: "stop" });
      } else {
        requestExitConfirm();
      }
    } else {
      const topic = promptInput.value.trim();
      promptInput.value = "";
      setMode("mock");
      await sendPrompt(topic || "开始模拟面试");
    }
  });
}

function addMessage(role, text, meta = "") {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const safeMeta = escapeHtml(meta || (role === "user" ? "You" : "LearnForge"));
  const avatarClass = role === "user" ? "avatar-user" : "avatar-agent";
  article.innerHTML = `
    <div class="pixel-avatar ${avatarClass}" aria-hidden="true"><span></span></div>
    <div class="message-bubble">
      <div class="message-meta">${safeMeta}</div>
      ${renderRichText(text)}
    </div>
  `;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
  pushHistory({ k: "msg", role, text, meta });
}

function addSystem(text) {
  const article = document.createElement("article");
  article.className = "message system";
  article.textContent = text;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
  pushHistory({ k: "system", text });
}

function addActions(actions) {
  if (!Array.isArray(actions) || !actions.length) return;
  const article = document.createElement("article");
  article.className = "message agent";
  article.innerHTML = `
    <div class="pixel-avatar avatar-agent" aria-hidden="true"><span></span></div>
    <div class="message-bubble">
      <div class="message-meta">LearnForge · Artifacts</div>
      <ul class="action-list">
        ${actions.map((action) => `<li>${escapeHtml(action)}</li>`).join("")}
      </ul>
    </div>
  `;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
  pushHistory({ k: "actions", actions });
}

function addImage(url, caption) {
  if (!url) return;
  const article = document.createElement("article");
  article.className = "message agent";
  article.innerHTML = `
    <div class="pixel-avatar avatar-agent" aria-hidden="true"><span></span></div>
    <div class="message-bubble">
      <div class="message-meta">LearnForge · ${escapeHtml(caption || "Infographic")}</div>
      <a href="${escapeHtml(url)}" target="_blank" rel="noopener">
        <img class="result-image" src="${escapeHtml(url)}" alt="${escapeHtml(caption || "infographic")}" loading="lazy" />
      </a>
    </div>
  `;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
  pushHistory({ k: "image", url, caption });
}

const RISK_LABELS = { overclaim: "夸大无证据", no_evidence: "缺证据链", vague: "含糊/过短" };

function addScore(score) {
  if (!score) return;
  const dims = score.dims || {};
  const risks = (score.risk_flags || []).map((r) => RISK_LABELS[r] || r);
  const riskHtml = risks.length ? `<span class="score-risk">⚠ ${escapeHtml(risks.join("、"))}</span>` : "";
  const article = document.createElement("article");
  article.className = "message system score-line";
  article.innerHTML = `评分 ${escapeHtml(String(score.overall ?? "—"))}/5 ·
    正确性 ${escapeHtml(String(dims.correctness ?? "-"))}
    深度 ${escapeHtml(String(dims.depth ?? "-"))}
    表达 ${escapeHtml(String(dims.clarity ?? "-"))} ${riskHtml}`;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
  pushHistory({ k: "score", score });
}

// 按需出图：渲染一个"生成信息图"按钮，点击才调 /ui/image（出图慢/费钱，不自动跑）。
function addImageButton(kind, spec, caption) {
  if (!spec) return;
  const article = document.createElement("article");
  article.className = "message agent";
  article.innerHTML = `
    <div class="pixel-avatar avatar-agent" aria-hidden="true"><span></span></div>
    <div class="message-bubble">
      <div class="message-meta">LearnForge · ${escapeHtml(caption || "Infographic")}</div>
      <button type="button" class="gen-image-btn">🖼️ 生成${escapeHtml(caption || "信息图")}</button>
    </div>
  `;
  const btn = article.querySelector(".gen-image-btn");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "出图中…（约 10-30s）";
    try {
      const resp = await fetch("/ui/image", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, spec }),
      });
      const data = await resp.json();
      if (data.ok && data.image_url) {
        addImage(data.image_url, caption);
        btn.textContent = "✓ 已生成";
      } else {
        btn.disabled = false;
        btn.textContent = `重试生成（${data.error || "失败"}）`;
      }
    } catch (e) {
      btn.disabled = false;
      btn.textContent = `重试生成（${e.message}）`;
    }
  });
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
}

function logLine(kind, content) {
  const row = document.createElement("p");
  row.className = "log-line";
  row.innerHTML = `<span>${escapeHtml(kind)}</span>${escapeHtml(content)}`;
  activityLog.prepend(row);
}

function setChain(items, activeIndex = -1) {
  agentChain.innerHTML = "";
  items.forEach((item, index) => {
    const key = item.agent || item;
    const [title, desc] = agentLabels[key] || [key, "执行任务"];
    const li = document.createElement("li");
    li.className = index === activeIndex ? "active" : "";
    li.innerHTML = `
      <span class="node ${escapeHtml(key)}"></span>
      <div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(item.detail || desc)}</small></div>
    `;
    agentChain.appendChild(li);
  });
}

function summarizeResponse(data) {
  if (data.reply_text && !data.reply_text.startsWith("[stub aggregate]")) {
    return data.reply_text;
  }
  const last = Array.isArray(data.responses) ? data.responses[data.responses.length - 1] : null;
  const result = last && last.result ? last.result : {};
  if (result.answer) return result.answer;
  if (Array.isArray(result.recommendations) && result.recommendations.length) {
    return result.recommendations[0];
  }
  if (result.diff && result.diff.rationale) return result.diff.rationale;
  if (data.mock && data.mock.question) return data.mock.question;
  if (Array.isArray(result.clusters) && result.clusters.length) {
    return `诊断完成：需要优先照看的薄弱点是 ${result.clusters.map((x) => x.topic).join("、")}。`;
  }
  return "任务完成。右侧已经更新本轮 agent 调用链路。";
}

function chainFromData(data) {
  const plan = Array.isArray(data.plan) ? data.plan : [];
  if (plan.length) {
    return [{ agent: "manager", detail: "生成计划" }].concat(
      plan.map((task) => ({ agent: task.agent || task.target_agent || "agent" }))
    );
  }
  if (data.mock) return [{ agent: "manager" }, { agent: "mock" }];
  return [{ agent: "manager" }];
}

function renderActivity(data) {
  if (data.trace_id) {
    traceId.textContent = data.trace_id;
    logLine("Trace", data.trace_id);
  } else if (traceId) {
    traceId.textContent = data.mock ? "trace mock-local" : "trace unavailable";
  }
  const responses = Array.isArray(data.responses) ? data.responses : [];
  responses.forEach((response, index) => {
    const agent = data.plan && data.plan[index] ? data.plan[index].agent : "agent";
    const result = response.result || {};
    let detail = `confidence=${response.confidence ?? "n/a"}`;
    if (agent === "diagnosis") detail = `clusters=${(result.clusters || []).length}`;
    if (agent === "qa") detail = `citations=${(result.citations || []).length}`;
    if (agent === "planning") {
      const diff = result.diff || {};
      detail = result.skipped ? "modify skipped" : `add=${(diff.add || []).length}, remove=${(diff.remove || []).length}`;
    }
    logLine(agent || "Agent", detail);
  });
  if (Array.isArray(data.next_actions)) {
    data.next_actions.slice(0, 2).forEach((action) => logLine("Next", action));
  }
}

async function loadFiles() {
  if (!fileList || !filePreview) return;
  fileList.innerHTML = `<button class="file-row active">Loading files...</button>`;
  try {
    const response = await fetch("/ui/files");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const files = Array.isArray(data.files) ? data.files : [];
    if (!files.length) {
      fileList.innerHTML = `<button class="file-row active">No markdown files</button>`;
      return;
    }
    fileList.innerHTML = "";
    files.forEach((file, index) => {
      const button = document.createElement("button");
      button.className = `file-row${index === 0 ? " active" : ""}`;
      button.innerHTML = `<strong>${escapeHtml(file.name)}</strong><small>${escapeHtml(file.kind)} · ${Math.ceil((file.size || 0) / 1024)} KB</small>`;
      button.addEventListener("click", () => {
        Array.from(fileList.querySelectorAll(".file-row")).forEach((row) => row.classList.remove("active"));
        button.classList.add("active");
        readFile(file.path);
      });
      fileList.appendChild(button);
    });
    readFile(files[0].path);
  } catch (error) {
    fileList.innerHTML = `<button class="file-row active">Failed to load files</button>`;
    filePreview.innerHTML = `<h3>Files Error</h3><p>${escapeHtml(error.message)}</p>`;
  }
}

async function readFile(path) {
  if (!filePreview) return;
  filePreview.innerHTML = `<h3>Loading...</h3>`;
  const response = await fetch("/ui/files/read", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await response.json();
  if (data.error) {
    filePreview.innerHTML = `<h3>File Error</h3><p>${escapeHtml(data.error)}</p>`;
    return;
  }
  filePreview.innerHTML = `<h3>${escapeHtml(path.split("/").pop())}</h3><pre>${escapeHtml(data.content || "")}</pre>`;
}

// 实时进度提示：复合链路要串行跑多次 LLM，没有进度会让人以为“卡死”。
// 这里显示经过秒数 + 分步提示（非真流式，但让等待“看着在动”）。
function startThinking() {
  const article = document.createElement("article");
  article.className = "message system thinking";
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
  const hints = [
    "Manager 规划调用链路…",
    "调度子 agent（诊断 / 检索 / 规划）…",
    "检索知识库并推理中…",
    "合成 / 校验回复…（模型较慢请稍候）",
  ];
  const t0 = Date.now();
  let step = 0;
  const render = () => {
    const s = ((Date.now() - t0) / 1000).toFixed(1);
    article.textContent = `${hints[Math.min(step, hints.length - 1)]} · ${s}s`;
    conversation.scrollTop = conversation.scrollHeight;
  };
  render();
  const hintTimer = setInterval(() => { step += 1; }, 4000);
  const tickTimer = setInterval(render, 200);
  return {
    stop() { clearInterval(hintTimer); clearInterval(tickTimer); article.remove(); },
  };
}

async function sendPrompt(text, opts = {}) {
  const mockAction = opts.mockAction || null;
  // 抓取并清空待发附件（mock 进行中不带附件）。
  const attachments = state.mock.active ? [] : pendingAttachments.splice(0);
  renderAttachChips();
  if (opts.addUser !== false) {
    const tag = mockAction === "side" ? "插问 · You"
      : state.mock.active ? "MOCK · You" : `${state.mode.toUpperCase()} · You`;
    const shown = attachments.length
      ? `${text || ""}\n附件：${attachments.map((a) => a.filename).join("、")}`.trim()
      : text;
    addMessage("user", shown, tag);
    touchConversation(text || "（附件）");  // 刷新当前对话标题/时间并置顶
  }
  const thinking = startThinking();
  chainStatus.textContent = "running";
  setChain([{ agent: "manager", detail: "分析意图并规划下一步" }], 0);
  logLine("Input", text);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000); // 120s 客户端超时，避免“永久卡住”
  try {
    const response = await fetch("/ui/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        mode: state.mode,
        session_id: state.sessionId,
        mock_session_id: state.mock.sessionId,
        mock_action: mockAction,
        mock_question: state.mock.lastQuestion || null,
        attachments: attachments.length ? attachments : null,
      }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    const chain = chainFromData(data);
    setChain(chain, chain.length - 1);
    renderActivity(data);
    renderMemoryLog(data.memory);
    // mock：上一轮作答的判分先于下一题展示。
    if (data.mock_score) addScore(data.mock_score);
    const speaker = data.mock ? "LearnForge · Interviewer" : "LearnForge · Manager";
    addMessage("agent", summarizeResponse(data), speaker);
    addActions(data.next_actions);
    // 信息图：自动模式已出图(有 URL)→直接渲染；否则给"生成信息图"按钮(按需出图)。
    if (data.image_url) {
      addImage(data.image_url, "学习计划信息图");
    } else if (data.image_spec) {
      addImageButton("plan", data.image_spec, "学习计划信息图");
    }
    const settle = data.settlement;
    if (settle && settle.diagnosis_image_url) {
      addImage(settle.diagnosis_image_url, "诊断信息图");
    } else if (settle && settle.diagnosis && (settle.diagnosis.clusters || []).length) {
      addImageButton("diagnosis", {
        clusters: settle.diagnosis.clusters,
        weak_atoms: settle.diagnosis.weak_atoms,
        recommendations: settle.diagnosis.recommendations,
      }, "诊断信息图");
    }
    // 记住当前题目与轮次（插问/刷新后用来提醒，长会话不丢状态）。
    if (data.mock && data.mock.status === "active" && data.reply_text) {
      state.mock.lastQuestion = data.reply_text;
    }
    if (data.mock && typeof data.mock.turn_index === "number") {
      state.mock.turns = data.mock.turn_index;
    }
    // 同步 mock 进行状态（语义触发/退出/确认都走这里，前后端一致）。
    if (typeof data.mock_active !== "undefined") {
      if (data.mock_active) {
        setMockActive(true, data.mock_session_id);
        // 退出意图待确认 → 置 pendingExit；已解决 → 清除（后端是唯一裁决者）。
        state.mock.pendingExit = !!data.needs_exit_confirm;
      } else if (state.mock.active) {
        setMockActive(false);
      }
    }
    if (data.exit_cancelled) addSystem("已取消结束，面试继续。");
    if (data.escalated) addSystem("已带着面试上下文转入常规流程（诊断 / 计划 / 问答）。");
    persistMock();
    // 插问回答后，提醒仍在面试、当前待答题目。
    if (data.mock_side && state.mock.active && state.mock.lastQuestion) {
      addSystem(`（面试继续）当前待答问题：${state.mock.lastQuestion}`);
    }
    chainStatus.textContent = data.status || "ok";
  } catch (err) {
    if (err.name === "AbortError") {
      addMessage("agent", "请求超时（>120s）。复合链路或模型较慢，可重试或换更简单的问题。", "LearnForge · timeout");
      logLine("Timeout", "120s");
      chainStatus.textContent = "timeout";
    } else {
      throw err; // 交给 composer 的 catch 统一提示
    }
  } finally {
    clearTimeout(timer);
    thinking.stop();
  }
}

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = promptInput.value.trim();
  if (!text && pendingAttachments.length === 0) return;  // 允许"仅附件"发送
  promptInput.value = "";
  promptInput.disabled = true;
  try {
    await routeUserText(text);
  } catch (error) {
    addMessage("agent", `请求失败：${error.message}`, "LearnForge · error");
    logLine("Error", error.message);
    chainStatus.textContent = "error";
  } finally {
    promptInput.disabled = false;
    promptInput.focus();
  }
});

setMode("qa");
setView("chat");
loadFiles();
initConversations();
// mock 横幅是瞬时提示，不写回 history（避免刷新后重复堆积）。
state.replaying = true;
restoreMock();
state.replaying = false;
