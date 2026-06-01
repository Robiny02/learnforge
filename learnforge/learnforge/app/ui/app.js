const conversation = document.querySelector("#conversation");
const composer = document.querySelector("#composer");
const promptInput = document.querySelector("#promptInput");
const modeSelect = document.querySelector("#modeSelect");
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

const state = {
  mode: "qa",
  sessionId: `ui-${Math.random().toString(16).slice(2, 8)}`,
};

sessionId.textContent = state.sessionId;

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
}

modeSelect.addEventListener("change", () => setMode(modeSelect.value));

function addMessage(role, text, meta = "") {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const safeMeta = escapeHtml(meta || (role === "user" ? "You" : "LearnForge"));
  const avatarClass = role === "user" ? "avatar-user" : "avatar-agent";
  article.innerHTML = `
    <div class="pixel-avatar ${avatarClass}" aria-hidden="true"><span></span></div>
    <div class="message-bubble">
      <div class="message-meta">${safeMeta}</div>
      <p>${escapeHtml(text)}</p>
    </div>
  `;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
}

function addSystem(text) {
  const article = document.createElement("article");
  article.className = "message system";
  article.textContent = text;
  conversation.appendChild(article);
  conversation.scrollTop = conversation.scrollHeight;
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

async function sendPrompt(text) {
  addMessage("user", text, `${state.mode.toUpperCase()} · You`);
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
      body: JSON.stringify({ text, mode: state.mode, session_id: state.sessionId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    const chain = chainFromData(data);
    setChain(chain, chain.length - 1);
    renderActivity(data);
    addMessage("agent", summarizeResponse(data), "LearnForge · Manager");
    addActions(data.next_actions);
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
  if (!text) return;
  promptInput.value = "";
  promptInput.disabled = true;
  try {
    await sendPrompt(text);
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
