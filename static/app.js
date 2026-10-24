(function () {
  "use strict";

  const panel = document.getElementById("ai-panel");
  if (!panel) return;

  // The location the user is currently viewing; sent with every AI request.
  const place = {
    name: panel.dataset.name,
    country: panel.dataset.country,
    lat: parseFloat(panel.dataset.lat),
    lon: parseFloat(panel.dataset.lon),
  };

  const chatLog = document.getElementById("chat-log");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const chatSend = document.getElementById("chat-send");
  const planForm = document.getElementById("plan-form");
  const planSend = document.getElementById("plan-send");
  const planResult = document.getElementById("plan-result");

  let history = []; // plain-text turns sent back so follow-up questions work
  let busy = false;

  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  }

  async function readError(res) {
    try {
      const data = await res.json();
      return data.error || "Something went wrong. Please try again.";
    } catch (e) {
      return "Something went wrong. Please try again.";
    }
  }

  // ---- Daily briefing ------------------------------------------------------

  async function loadBriefing() {
    const el = document.getElementById("briefing-text");
    const btn = document.getElementById("briefing-btn");
    btn.disabled = true;
    el.textContent = "Writing your briefing...";
    try {
      const res = await postJSON("/api/briefing", { location: place });
      if (!res.ok) throw new Error(await readError(res));
      const data = await res.json();
      el.textContent = data.briefing;
      el.classList.remove("muted");
      btn.textContent = "Refresh briefing";
    } catch (err) {
      el.textContent = err.message || "The briefing is unavailable right now.";
    }
    btn.disabled = false;
  }

  // ---- Ask Weather (streamed) ----------------------------------------------

  function addMessage(role, text) {
    const div = document.createElement("div");
    div.className = "msg " + role;
    const span = document.createElement("span");
    span.className = "msg-text";
    span.textContent = text;
    div.appendChild(span);
    chatLog.appendChild(div);
    chatLog.scrollTop = chatLog.scrollHeight;
    return div;
  }

  function setMessageText(bubble, text) {
    bubble.querySelector(".msg-text").textContent = text;
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function renderSources(bubble, sources) {
    const details = document.createElement("details");
    details.className = "sources";
    const summary = document.createElement("summary");
    summary.textContent = "Data used";
    details.appendChild(summary);
    const list = document.createElement("ul");
    sources.forEach(function (s) {
      const li = document.createElement("li");
      li.textContent = s.tool + " for " + s.place + ": " + s.detail;
      list.appendChild(li);
    });
    details.appendChild(list);
    bubble.appendChild(details);
  }

  function setBusy(value) {
    busy = value;
    chatInput.disabled = value;
    chatSend.disabled = value;
    chatSend.textContent = value ? "Thinking..." : "Ask";
  }

  async function ask(question) {
    if (busy) return;
    question = question.trim();
    if (!question) return;

    addMessage("user", question);
    const bubble = addMessage("assistant", "");
    bubble.classList.add("pending");
    chatInput.value = "";
    setBusy(true);

    let answer = "";
    let failed = false;

    try {
      const res = await postJSON("/api/ask", { question: question, history: history, location: place });
      if (!res.ok) throw new Error(await readError(res));

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });

        let end;
        while ((end = buffer.indexOf("\n\n")) !== -1) {
          const line = buffer.slice(0, end);
          buffer = buffer.slice(end + 2);
          if (line.indexOf("data: ") !== 0) continue;

          const event = JSON.parse(line.slice(6));
          if (event.type === "text") {
            answer += event.text;
            bubble.classList.remove("pending");
            setMessageText(bubble, answer);
          } else if (event.type === "sources") {
            renderSources(bubble, event.sources);
          } else if (event.type === "error") {
            failed = true;
            bubble.classList.add("error-msg");
            bubble.classList.remove("pending");
            setMessageText(bubble, answer ? answer + "\n\n" + event.message : event.message);
          }
        }
      }
    } catch (err) {
      failed = true;
      bubble.classList.remove("pending");
      bubble.classList.add("error-msg");
      setMessageText(bubble, err.message || "Something went wrong. Please try again.");
    }

    if (!answer && !failed) {
      bubble.classList.remove("pending");
      setMessageText(bubble, "No answer was returned. Please try again.");
    }

    if (answer && !failed) {
      history.push({ role: "user", content: question }, { role: "assistant", content: answer });
      history = history.slice(-8);
    }

    setBusy(false);
    chatInput.focus();
  }

  chatForm.addEventListener("submit", function (e) {
    e.preventDefault();
    ask(chatInput.value);
  });

  document.querySelectorAll(".chips .chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      ask(chip.dataset.q);
    });
  });

  // ---- Activity planner ----------------------------------------------------

  function renderPlan(plan) {
    planResult.textContent = "";

    if (plan.summary) {
      const p = document.createElement("p");
      p.className = "plan-summary";
      p.textContent = plan.summary;
      planResult.appendChild(p);
    }

    plan.windows.forEach(function (w) {
      const card = document.createElement("div");
      card.className = "plan-card rating-" + w.rating;

      const head = document.createElement("div");
      head.className = "plan-head";
      const badge = document.createElement("span");
      badge.className = "plan-badge";
      badge.textContent = w.rating === "avoid" ? "Avoid" : w.rating === "best" ? "Best" : "Good";
      const time = document.createElement("span");
      time.className = "plan-time";
      time.textContent = w.start + " to " + w.end;
      head.appendChild(badge);
      head.appendChild(time);

      const reason = document.createElement("div");
      reason.className = "plan-reason";
      reason.textContent = w.reason;

      card.appendChild(head);
      card.appendChild(reason);
      planResult.appendChild(card);
    });

    if (plan.tip) {
      const tip = document.createElement("p");
      tip.className = "plan-tip";
      tip.textContent = "Tip: " + plan.tip;
      planResult.appendChild(tip);
    }
  }

  planForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const activity = document.getElementById("plan-activity").value;
    planSend.disabled = true;
    planSend.textContent = "Planning...";
    planResult.textContent = "";

    try {
      const res = await postJSON("/api/plan", { activity: activity, location: place });
      if (!res.ok) throw new Error(await readError(res));
      renderPlan(await res.json());
    } catch (err) {
      const p = document.createElement("p");
      p.className = "plan-error";
      p.textContent = err.message || "The plan is unavailable right now.";
      planResult.appendChild(p);
    }

    planSend.disabled = false;
    planSend.textContent = "Find best time";
  });

  document.getElementById("briefing-btn").addEventListener("click", loadBriefing);
})();
