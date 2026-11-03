// Every trip is its own thread. The page no longer reuses an old thread id.
let currentThreadId = null;
let latestAnswerMarkdown = "";
let waitingForApproval = false;

// Older versions stored the thread id in the browser. We do not use it any more.
localStorage.removeItem("travel_thread_id");

const AGENT_LABELS = {
  flight_agent: "✈️ Flight Agent",
  hotel_agent: "🏨 Hotel Agent",
  weather_agent: "🌦️ Weather Agent",
  budget_agent: "💰 Budget Agent",
  itinerary_agent: "🗓️ Itinerary Agent"
};

function setPrompt(text) {
  document.getElementById("userInput").value = text;
}

function setLoading(isLoading, mode = "draft") {
  const sendBtn = document.getElementById("sendBtn");
  const btnText = document.getElementById("btnText");
  const btnLoader = document.getElementById("btnLoader");
  const approveBtn = document.getElementById("approveBtn");
  const reviseBtn = document.getElementById("reviseBtn");

  sendBtn.disabled = isLoading;
  approveBtn.disabled = isLoading;
  reviseBtn.disabled = isLoading;

  if (isLoading && mode === "draft") {
    btnText.classList.add("hidden");
    btnLoader.classList.remove("hidden");
  } else {
    btnText.classList.remove("hidden");
    btnLoader.classList.add("hidden");
  }
}

function showError(message) {
  const errorBox = document.getElementById("errorBox");
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
  errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
}

function hideError() {
  const errorBox = document.getElementById("errorBox");
  errorBox.classList.add("hidden");
  errorBox.textContent = "";
}

// If the login has expired, the server answers 401. Go back to the login page.
function goToLoginIfExpired(response) {
  if (response.status === 401) {
    window.location.href = "/login";
    return true;
  }
  return false;
}

function renderMarkdown(element, markdown) {
  if (typeof marked !== "undefined") {
    element.innerHTML = marked.parse(markdown || "");
  } else {
    element.innerText = markdown || "";
  }
}

function showWorkflow(data) {
  const section = document.getElementById("workflowSection");
  const reasoning = document.getElementById("supervisorReasoning");
  const chips = document.getElementById("agentChips");
  const guardrailBadge = document.getElementById("guardrailBadge");

  reasoning.textContent = data.supervisor_reasoning || "Supervisor routing completed.";
  chips.innerHTML = "";

  (data.selected_agents || []).forEach((agent) => {
    const chip = document.createElement("span");
    chip.className = "agent-chip";
    chip.textContent = AGENT_LABELS[agent] || agent;
    chips.appendChild(chip);
  });

  if (data.guardrail_allowed === false) {
    guardrailBadge.textContent = "Guardrail blocked";
    guardrailBadge.classList.add("blocked");
  } else {
    guardrailBadge.textContent = "Guardrail passed";
    guardrailBadge.classList.remove("blocked");
  }

  section.classList.remove("hidden");
}

function showResult(answer, threadId, isDraft = false) {
  latestAnswerMarkdown = answer || "";

  const resultSection = document.getElementById("resultSection");
  const resultBox = document.getElementById("resultBox");
  const threadInfo = document.getElementById("threadInfo");
  const resultTitle = document.getElementById("resultTitle");

  renderMarkdown(resultBox, latestAnswerMarkdown);
  threadInfo.textContent = `Thread ID: ${threadId}`;
  resultTitle.textContent = isDraft ? "Draft Travel Plan" : "Your Final AI Travel Plan";
  resultSection.classList.remove("hidden");

  resultSection.scrollIntoView({
    behavior: "smooth",
    block: "start"
  });
}

function showApproval(data) {
  waitingForApproval = true;
  const section = document.getElementById("approvalSection");
  const approvalRequest = document.getElementById("approvalRequest");
  approvalRequest.textContent = data.approval_request ||
    "Approve the draft or provide feedback before the final plan is generated.";
  section.classList.remove("hidden");
}

function hideApproval() {
  waitingForApproval = false;
  document.getElementById("approvalSection").classList.add("hidden");
  document.getElementById("approvalFeedback").value = "";
}

async function sendMessage() {
  hideError();

  if (waitingForApproval) {
    showError("Please approve or revise the current draft before starting another plan. Or click + New trip.");
    return;
  }

  const input = document.getElementById("userInput");
  const message = input.value.trim();

  if (!message) {
    showError("Please enter your travel request first.");
    return;
  }

  setLoading(true, "draft");

  try {
    // thread_id is null: every new request becomes its own new trip (thread)
    const response = await fetch("/api/travel", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        message: message,
        thread_id: null
      })
    });

    if (goToLoginIfExpired(response)) {
      return;
    }

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Something went wrong.");
    }

    currentThreadId = data.thread_id;

    showWorkflow(data);

    if (data.requires_approval) {
      showResult(data.itinerary || data.answer, data.thread_id, true);
      showApproval(data);
    } else {
      hideApproval();
      showResult(data.answer, data.thread_id, false);
    }

    loadThreads();
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false, "draft");
  }
}

async function submitApproval(approved) {
  hideError();

  if (!currentThreadId || !waitingForApproval) {
    showError("There is no draft waiting for approval.");
    return;
  }

  const feedbackInput = document.getElementById("approvalFeedback");
  const feedback = feedbackInput.value.trim();

  if (!approved && !feedback) {
    showError("Please enter revision feedback before requesting changes.");
    feedbackInput.focus();
    return;
  }

  setLoading(true, "approval");

  try {
    const response = await fetch("/api/travel/approve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        thread_id: currentThreadId,
        approved: approved,
        feedback: feedback
      })
    });

    if (goToLoginIfExpired(response)) {
      return;
    }

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not resume the travel workflow.");
    }

    showWorkflow(data);
    hideApproval();
    showResult(data.answer, data.thread_id, false);
    loadThreads();
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false, "approval");
  }
}

function copyResult() {
  const resultBox = document.getElementById("resultBox");
  const text = resultBox.innerText;

  if (!text) {
    return;
  }

  navigator.clipboard.writeText(text)
    .then(() => {
      const copyBtn = document.querySelector(".copy-btn");
      const oldText = copyBtn.textContent;
      copyBtn.textContent = "Copied!";

      setTimeout(() => {
        copyBtn.textContent = oldText;
      }, 1400);
    })
    .catch(() => {
      showError("Could not copy result.");
    });
}

function downloadPDF() {
  const pdfContent = document.getElementById("pdfContent");

  if (!latestAnswerMarkdown || !pdfContent) {
    showError("No travel plan available to download.");
    return;
  }

  const downloadBtn = document.querySelector(".download-btn");
  const oldText = downloadBtn.textContent;
  downloadBtn.textContent = "Preparing PDF...";
  downloadBtn.disabled = true;

  const options = {
    margin: 0.5,
    filename: "ai-travel-plan.pdf",
    image: {
      type: "jpeg",
      quality: 0.98
    },
    html2canvas: {
      scale: 2,
      useCORS: true,
      backgroundColor: "#ffffff"
    },
    jsPDF: {
      unit: "in",
      format: "a4",
      orientation: "portrait"
    },
    pagebreak: {
      mode: ["avoid-all", "css", "legacy"]
    }
  };

  html2pdf()
    .set(options)
    .from(pdfContent)
    .save()
    .then(() => {
      downloadBtn.textContent = oldText;
      downloadBtn.disabled = false;
    })
    .catch(() => {
      downloadBtn.textContent = oldText;
      downloadBtn.disabled = false;
      showError("Could not download PDF.");
    });
}

// ---------------------------------------------------------------------------
// Recent trips sidebar
// ---------------------------------------------------------------------------
function isMobileView() {
  return window.innerWidth <= 900;
}

function toggleSidebar() {
  document.body.classList.toggle("sidebar-open");
}

function closeSidebarOnMobile() {
  if (isMobileView()) {
    document.body.classList.remove("sidebar-open");
  }
}

function markActiveThread() {
  document.querySelectorAll(".thread-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.threadId === currentThreadId);
  });
}

function showThreadMessage(text) {
  const list = document.getElementById("threadList");
  list.innerHTML = "";
  const message = document.createElement("div");
  message.className = "thread-empty";
  message.textContent = text;
  list.appendChild(message);
}

function renderThreadList(threads) {
  const list = document.getElementById("threadList");
  list.innerHTML = "";

  if (threads.length === 0) {
    showThreadMessage("No trips yet. Your plans will appear here.");
    return;
  }

  threads.forEach((thread) => {
    const item = document.createElement("div");
    item.className = "thread-item";
    item.dataset.threadId = thread.thread_id;
    if (thread.thread_id === currentThreadId) {
      item.classList.add("active");
    }

    const openBtn = document.createElement("button");
    openBtn.type = "button";
    openBtn.className = "thread-open";
    openBtn.title = thread.title;
    openBtn.addEventListener("click", () => openThread(thread.thread_id));

    const dot = document.createElement("span");
    dot.className = "thread-dot " + (thread.status === "finished" ? "finished" : "pending");

    const title = document.createElement("span");
    title.className = "thread-title";
    title.textContent = thread.title;

    openBtn.appendChild(dot);
    openBtn.appendChild(title);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "thread-delete";
    deleteBtn.title = "Delete this trip";
    deleteBtn.setAttribute("aria-label", "Delete this trip");
    deleteBtn.textContent = "×";
    deleteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteThread(thread.thread_id);
    });

    item.appendChild(openBtn);
    item.appendChild(deleteBtn);
    list.appendChild(item);
  });
}

async function loadThreads() {
  try {
    const response = await fetch("/api/threads");

    if (goToLoginIfExpired(response)) {
      return;
    }

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not load trips.");
    }

    renderThreadList(data.threads || []);
  } catch (error) {
    showThreadMessage("Could not load your trips.");
  }
}

async function openThread(threadId) {
  hideError();

  try {
    const response = await fetch("/api/threads/" + encodeURIComponent(threadId));

    if (goToLoginIfExpired(response)) {
      return;
    }

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not open this trip.");
    }

    const thread = data.thread;
    currentThreadId = thread.thread_id;

    document.getElementById("userInput").value = thread.user_request || "";
    document.getElementById("workflowSection").classList.add("hidden");

    if (thread.status === "finished") {
      hideApproval();
      showResult(thread.final_plan || thread.draft, thread.thread_id, false);
    } else {
      // Still waiting for approval: show the draft and the approve / revise box again
      showResult(thread.draft, thread.thread_id, true);
      showApproval({});
    }

    markActiveThread();
    closeSidebarOnMobile();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteThread(threadId) {
  if (!confirm("Delete this trip from your Recent list?")) {
    return;
  }

  try {
    const response = await fetch("/api/threads/" + encodeURIComponent(threadId), {
      method: "DELETE"
    });

    if (goToLoginIfExpired(response)) {
      return;
    }

    const data = await response.json();

    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not delete this trip.");
    }

    if (threadId === currentThreadId) {
      newTrip();
    }

    loadThreads();
  } catch (error) {
    showError(error.message);
  }
}

function newTrip() {
  currentThreadId = null;
  latestAnswerMarkdown = "";

  hideError();
  hideApproval();

  const input = document.getElementById("userInput");
  input.value = "";

  document.getElementById("workflowSection").classList.add("hidden");
  document.getElementById("resultSection").classList.add("hidden");

  markActiveThread();
  closeSidebarOnMobile();
  window.scrollTo({ top: 0, behavior: "smooth" });
  input.focus();
}

document.addEventListener("keydown", function(event) {
  if (event.ctrlKey && event.key === "Enter") {
    sendMessage();
  }
});

// Load the Recent list when the page opens
loadThreads();