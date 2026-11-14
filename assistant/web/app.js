// HUD client: websocket link, speech recognition (English / Tamil), spoken
// replies, approvals and device pairing.
(() => {
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
  };
  const LABELS = { idle: "STANDBY", listening: "LISTENING", thinking: "WORKING", speaking: "SPEAKING" };
  const YES = /^(yes|yeah|approve|approved|confirm|do it|go ahead|ok|okay|proceed|sari|seri|சரி|ஆம்|ஆமா|ஆமாம்)\b/i;
  const NO = /^(no|nope|deny|cancel|stop|don't|do not|vendam|வேண்டாம்|இல்லை|வேணாம்)/i;
  const TAMIL = /[஀-௿]/;

  let ws = null, retry = 1000;
  let lang = store.get("lang") || "en-IN";
  let handsFree = false, muted = store.get("muted") === "1";
  let assistantName = "", tapped = false, followUntil = 0;   // wake-word state
  let voiceLocked = false, voicePass = "", voicePassUntil = 0, checkingVoice = false;
  const FOLLOW_UP_MS = 20000;
  let pendingSend = false, iAsked = false, replyText = "", speakBuffer = "";
  let recognition = null, listening = false, busy = false, speaking = 0;
  let pendingConfirm = null;

  // ---------------------------------------------------------------- display

  function setState(state) {
    document.body.dataset.state = state;
    $("state-label").textContent = LABELS[state] || state.toUpperCase();
  }
  function settle() {           // choose the resting state once nothing is happening
    if (listening) return setState("listening");
    if (speaking > 0) return setState("speaking");
    if (busy) return setState("thinking");
    setState("idle");
    if (handsFree) setTimeout(() => { if (handsFree && !busy) startListening(); }, 300);
  }
  function setLink(text, bad) { $("link").textContent = text; $("link").classList.toggle("bad", !!bad); }
  function setLang(next) {
    lang = next; store.set("lang", lang);
    $("lang").textContent = lang === "ta-IN" ? "தமிழ்" : "EN";
    if (listening) { stopListening(); startListening(); }
  }
  function tick() {
    const now = new Date();
    $("clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    $("date").textContent = now.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }).toUpperCase();
  }
  setInterval(tick, 10000); tick();

  function gauge(key, value, hot) {
    const el = document.querySelector(`.gauge[data-key="${key}"]`);
    if (value == null) { el.querySelector("b").textContent = "--"; return; }
    el.querySelector("b").textContent = value;
    el.querySelector(".value").style.strokeDasharray = `${(value / 100) * 100.5} 101`;
    el.classList.toggle("hot", hot);
  }
  function logTool(e) {
    let li = document.querySelector(`#log li[data-id="${CSS.escape(e.id)}"]`);
    if (!li) {
      li = document.createElement("li"); li.dataset.id = e.id;
      li.append(document.createElement("b"), document.createElement("span"));
      $("log").prepend(li);
      while ($("log").children.length > 5) $("log").lastChild.remove();
    }
    li.className = e.status;
    li.querySelector("b").textContent = e.name.replaceAll("_", " ").toUpperCase() +
      (e.status === "denied" ? " - DENIED" : e.status === "error" ? " - FAILED" : "");
    li.querySelector("span").textContent = e.input || "";
  }
  function card(e) {
    const el = document.createElement("div"); el.className = "card";
    const h = document.createElement("h4"); h.textContent = e.title; el.append(h);
    if (e.text) { const p = document.createElement("p"); p.textContent = e.text; el.append(p); }
    if (e.url && /^https?:\/\//i.test(e.url)) {
      const a = document.createElement("a"); a.href = e.url; a.textContent = e.url;
      a.target = "_blank"; a.rel = "noopener"; el.append(a);
    }
    const x = document.createElement("button"); x.textContent = "×"; x.setAttribute("aria-label", "Dismiss");
    x.onclick = () => el.remove(); el.append(x);
    $("cards").prepend(el);
    while ($("cards").children.length > 3) $("cards").lastChild.remove();
  }

  // -------------------------------------------------------------- dashboard

  function el(tag, props, ...kids) {
    const node = Object.assign(document.createElement(tag), props || {});
    node.append(...kids); return node;
  }
  function tipOn(node, text) {          // hover / tap detail for a chart mark
    node.dataset.tip = text; node.tabIndex = 0;
    return node;
  }
  function showTip(ev) {
    const target = ev.target.closest("[data-tip]");
    if (!target) return;
    const tip = $("tip"); tip.textContent = target.dataset.tip; tip.hidden = false;
    const r = target.getBoundingClientRect();
    const left = Math.min(Math.max(8, r.left + r.width / 2 - tip.offsetWidth / 2), innerWidth - tip.offsetWidth - 8);
    tip.style.left = `${left}px`; tip.style.top = `${Math.max(8, r.top - tip.offsetHeight - 8)}px`;
  }
  function hideTip() { $("tip").hidden = true; }
  const prettyDay = (iso) => new Date(iso + "T00:00").toLocaleDateString([], { day: "numeric", month: "short" });
  const prettyTool = (name) => name.replaceAll("_", " ");

  function drawDashboard(d) {
    const projects = d.projects.map((p) => el("div", { className: "project" },
      el("header", null, el("span", { textContent: p.name }), el("b", { textContent: `${p.percent}%` })),
      el("div", { className: "stages" }, ...p.stages.map((s) => {
        const fill = el("u"); fill.style.width = `${s.percent}%`;
        return tipOn(el("i", null, fill), `Stage ${s.stage}: ${s.title} - ${s.done} of ${s.total} done`);
      }))));
    if (projects.length) $("projects").replaceChildren(...projects);

    const total = d.requests.reduce((sum, r) => sum + r.count, 0);
    const peak = Math.max(1, ...d.requests.map((r) => r.count));
    $("req-total").textContent = total;
    $("req-note").textContent = total === 1 ? "request" : "requests";
    $("req-from").textContent = prettyDay(d.requests[0].day);
    $("req-chart").replaceChildren(...d.requests.map((r) => {
      const bar = el("u"); bar.style.height = `${(r.count / peak) * 100}%`;
      return tipOn(el("i", null, bar), `${prettyDay(r.day)}: ${r.count} request${r.count === 1 ? "" : "s"}`);
    }));

    if (d.tools.length) {
      const most = d.tools[0].count;
      $("tools").replaceChildren(...d.tools.map((t) => {
        const fill = el("u"); fill.style.width = `${(t.count / most) * 100}%`;
        return tipOn(el("div", { className: "tool" }, el("span", { textContent: prettyTool(t.name) }),
          el("b", { textContent: t.count }), el("div", null, fill)), `${prettyTool(t.name)}: used ${t.count} times in 14 days`);
      }));
    }
    const money = d.money.months, now = money[money.length - 1];
    const cash = (n) => `${Math.round(n).toLocaleString()} ${d.money.currency}`;
    const top = Math.max(1, ...money.map((m) => m.income));
    const monthName = (key) => new Date(key + "-01T00:00").toLocaleDateString([], { month: "short", year: "2-digit" });
    $("money-total").textContent = cash(now.income);
    $("money-note").textContent = `this month, ${cash(now.expense)} spent`;
    $("money-from").textContent = monthName(money[0].month);
    $("money-chart").replaceChildren(...money.map((m) => {
      const bar = el("u"); bar.style.height = `${(m.income / top) * 100}%`;
      return tipOn(el("i", null, bar), `${monthName(m.month)}: income ${cash(m.income)}, spent ${cash(m.expense)}, net ${cash(m.net)}`);
    }));
    $("watch-section").hidden = !d.watch.sources.length;
    $("watch").replaceChildren(...(d.watch.events.length ? d.watch.events : [{ at: "", source: "Watching", kind: "", detail:
      d.watch.sources.map((s) => s.name).join(", ") + " - no changes yet" }]).map((e) =>
      el("li", null, el("b", { textContent: `${e.source}${e.kind ? " - " + e.kind.toUpperCase() : ""}${e.at ? "  " + e.at.slice(5) : ""}` }),
        el("span", { textContent: e.detail }))));
    $("tasks-section").hidden = !d.tasks.length;
    $("tasks").replaceChildren(...d.tasks.map((t) =>
      el("li", null, el("b", { textContent: `${t.run_at}${t.repeat !== "none" ? "  " + t.repeat.toUpperCase() : ""}` }),
        el("span", { textContent: t.instruction }))));
    $("idea-count").textContent = d.idea_count || "";
    if (d.ideas.length) $("ideas").replaceChildren(...d.ideas.map((n) =>
      el("li", null, el("span", { textContent: n.title }), el("small", { textContent: prettyDay(n.updated) }))));
  }

  // ------------------------------------------------------------------ voice

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const synth = window.speechSynthesis;

  function pickVoice(text) {
    const voices = synth.getVoices();
    const want = TAMIL.test(text) ? ["ta"] : ["en-IN", "en-GB", "en"];
    for (const prefix of want) {
      const v = voices.find((v) => v.lang.replace("_", "-").startsWith(prefix));
      if (v) return v;
    }
    return null;
  }
  function speak(text) {
    text = text.trim();
    if (!text || muted || !synth || !iAsked) return;
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice(text);
    if (v) u.voice = v;
    u.lang = v ? v.lang : (TAMIL.test(text) ? "ta-IN" : "en-IN");
    u.rate = 1.04;
    speaking++;
    u.onend = u.onerror = () => { speaking = Math.max(0, speaking - 1); settle(); };
    synth.speak(u);
    settle();
  }
  function speakStreamed(delta) {     // speak sentence by sentence as text arrives
    speakBuffer += delta;
    let m;
    while ((m = speakBuffer.match(/^[\s\S]*?[.!?।\n]+(?=\s|$)/)) && m[0].trim().length > 1) {
      speak(m[0]); speakBuffer = speakBuffer.slice(m[0].length);
    }
  }
  function hush() { if (synth) synth.cancel(); speaking = 0; speakBuffer = ""; }

  // With voice lock on, spoken commands need a recent voice check on this device.
  async function voiceCheck() {
    if (!voiceLocked || Date.now() < voicePassUntil) return true;
    if (checkingVoice) return false;
    checkingVoice = true; hush(); setState("listening");
    showReply(lang === "ta-IN" ? "குரல் சரிபார்ப்பு: இப்போது பேசுங்கள்." : `Voice check: say "Hey ${assistantName}, it's me" now.`);
    try {
      const r = await Voice.verify(3);
      if (r.ok) {
        voicePass = r.token; voicePassUntil = Date.now() + (r.seconds - 30) * 1000;
        showReply(`Voice recognised: ${r.name}.`); return true;
      }
      showReply("I did not recognise that voice. Spoken commands stay locked.", true);
    } catch (e) { showReply(`Voice check failed: ${e.message}`, true); }
    finally { checkingVoice = false; }
    handsFree = false; $("hands").classList.remove("on"); setState("idle");
    return false;
  }

  async function startListening() {
    if (listening || speaking > 0 || checkingVoice) return;
    if (!(await voiceCheck())) return;
    if (!SR) { showReply("Voice input needs Chrome or Edge. You can still type.", true); return; }
    if (listening || speaking > 0) return;
    recognition = new SR();
    recognition.lang = lang; recognition.interimResults = true; recognition.continuous = false;
    let finalText = "";
    recognition.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        if (ev.results[i].isFinal) finalText += ev.results[i][0].transcript;
        else interim += ev.results[i][0].transcript;
      }
      $("heard").textContent = finalText || interim;
    };
    recognition.onerror = (ev) => {
      if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
        handsFree = false; $("hands").classList.remove("on");
        showReply("Microphone access is blocked. Allow the microphone for this page and try again.", true);
      }
    };
    recognition.onend = () => {
      listening = false; recognition = null;
      const said = finalText.trim();
      if (said) heard(said);
      settle();
    };
    try { recognition.start(); listening = true; setState("listening"); } catch { listening = false; }
  }
  function stopListening() { if (recognition) recognition.stop(); }

  function heard(text) {
    const viaTap = tapped; tapped = false;
    if (handsFree && !viaTap) {
      // Wake-word mode: act only on "Hey <name> ...", or on a follow-up right after a reply.
      const command = Wake.strip(text, assistantName);
      if (command === null && Date.now() > followUntil) {
        $("heard").textContent = `Say "Hey ${assistantName}" first`;
        return;
      }
      if (command === "") {                    // just the wake phrase: acknowledge and wait
        followUntil = Date.now() + FOLLOW_UP_MS; iAsked = true;
        showReply(lang === "ta-IN" ? "சொல்லுங்கள்." : "Yes?");
        speak(lang === "ta-IN" ? "சொல்லுங்கள்." : "Yes?");
        return;
      }
      if (command !== null) text = command;
    }
    if (pendingConfirm) {
      if (YES.test(text)) return answerConfirm(true);
      if (NO.test(text)) return answerConfirm(false);
    }
    say(text, true);
  }

  // ------------------------------------------------------------------- link

  function say(text, spoken) {
    if (!ws || ws.readyState !== 1) { showReply("Not connected to the PC yet.", true); return; }
    hush(); pendingSend = true;
    ws.send(JSON.stringify(spoken ? { type: "say", text, via: "voice", voice_pass: voicePass } : { type: "say", text }));
  }
  function showReply(text, isError) {
    $("reply").textContent = text; $("reply").classList.toggle("error", !!isError);
    $("reply").scrollTop = $("reply").scrollHeight;
  }
  function answerConfirm(approve) {
    if (!pendingConfirm) return;
    ws.send(JSON.stringify({ type: "confirm", id: pendingConfirm, approve }));
  }

  const handlers = {
    hello(e) {
      assistantName = e.name; voiceLocked = e.voice.locked;
      $("open-voice").hidden = !e.voice.available;
      document.title = e.name; $("name").textContent = e.name.toUpperCase();
      $("hands").textContent = `"HEY ${e.name.toUpperCase()}"`;
      setLink(e.online ? "ONLINE" : "BRAIN OFFLINE - RUN SETUP", !e.online);
      if (!store.get("lang")) setLang(e.language);
      $("pair-info").hidden = !e.pairing;
      if (e.pairing) {
        $("pair-pin").textContent = e.pairing.pin;
        $("pair-urls").textContent = e.pairing.urls.join("\n") || "No Wi-Fi / LAN address found.";
      }
    },
    auth_failed() { store.set("token", ""); },
    user(e) {
      iAsked = pendingSend; pendingSend = false; busy = true;
      $("heard").textContent = e.text; replyText = ""; speakBuffer = ""; showReply("");
    },
    state() { busy = true; settle(); },
    delta(e) { replyText += e.text; showReply(replyText); speakStreamed(e.text); },
    error(e) { replyText = e.text; showReply(e.text, true); speak(e.text); },
    reply_done() { busy = false; followUntil = Date.now() + FOLLOW_UP_MS; speak(speakBuffer); speakBuffer = ""; settle(); },
    tool: logTool,
    dashboard: drawDashboard,
    holo(e) { Holo.act(e.action); },
    brain(e) { setLink(`ONLINE - ${e.name.toUpperCase()}`); },
    voice_rejected() {
      pendingSend = false; voicePassUntil = 0;
      showReply("Voice lock: I need to recognise your voice first. Tap the mic and try again.", true);
    },
    card,
    stats(e) {
      gauge("cpu", e.cpu, e.cpu > 85); gauge("memory", e.memory, e.memory > 88);
      gauge("disk", e.disk, e.disk > 90);
      gauge("battery", e.battery ? e.battery.percent : null, !!e.battery && e.battery.percent < 20 && !e.battery.charging);
    },
    language(e) { setLang(e.language); },
    cleared() { $("heard").textContent = ""; showReply(""); $("log").replaceChildren(); },
    confirm_request(e) {
      pendingConfirm = e.id;
      const titles = {
        run_command: "Run this command on the PC?", write_file: `Write this file?  ${e.input.path || ""}`,
        email_send: `Send this email to ${e.input.to || ""}?`, email_inbox: "Let me look at your inbox?",
        email_read: "Let me open this email?", use_connector: `Send this to ${e.input.name || "the app"}?`,
        add_voice: `Allow the voice "${e.input.voice || ""}" to give spoken commands?`,
        remove_voice: `Remove the voice "${e.input.voice || ""}"?`,
      };
      $("confirm-title").textContent = titles[e.name] || `Allow ${e.name.replaceAll("_", " ")}?`;
      $("confirm-detail").textContent = e.name === "run_command" ? e.input.command
        : e.name === "write_file" ? String(e.input.contents || "").slice(0, 4000)
        : e.name === "email_send" ? `Subject: ${e.input.subject}\n\n${e.input.body}`
        : e.name === "add_voice" ? e.input.detail
        : JSON.stringify(e.input, null, 2);
      if (!$("confirm").open) $("confirm").showModal();
      speak(lang === "ta-IN" ? "உங்கள் அனுமதி தேவை." : "I need your approval for this.");
    },
    confirm_closed(e) {
      if (pendingConfirm === e.id) { pendingConfirm = null; if ($("confirm").open) $("confirm").close(); }
    },
  };

  async function connect() {
    let info;
    try { info = await (await fetch("api/info")).json(); }
    catch { setLink("PC UNREACHABLE", true); return setTimeout(connect, 3000); }
    if (!info.local && !store.get("token")) { if (!$("pair").open) $("pair").showModal(); return; }
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => {
      retry = 1000;
      if (!info.local) ws.send(JSON.stringify({ type: "auth", token: store.get("token") }));
    };
    ws.onmessage = (m) => { const e = JSON.parse(m.data); (handlers[e.type] || (() => {}))(e); };
    ws.onclose = () => {
      setLink("RECONNECTING", true); busy = false; settle();
      setTimeout(connect, retry); retry = Math.min(retry * 2, 15000);
    };
  }

  // --------------------------------------------------------------- controls

  $("ask").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const text = $("text").value.trim();
    if (text) { say(text); $("text").value = ""; }
  });
  $("mic").onclick = () => {
    if (speaking > 0) { hush(); settle(); return; }      // tap to interrupt
    if (listening) stopListening(); else { tapped = true; startListening(); }
  };
  $("lang").onclick = () => setLang(lang === "ta-IN" ? "en-IN" : "ta-IN");
  $("hands").onclick = () => {
    handsFree = !handsFree; $("hands").classList.toggle("on", handsFree);
    if (handsFree) startListening(); else stopListening();
  };
  $("more").onclick = () => {
    if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "refresh_pairing" }));
    $("menu").showModal();
  };
  $("close-menu").onclick = () => $("menu").close();
  async function showVoices() {
    const info = await Voice.info();
    voiceLocked = info.locked;
    $("voice-status").textContent = info.voices.length
      ? "Spoken commands are accepted only from these voices. Adding another voice needs your approval."
      : "No voice is enrolled yet, so anyone can speak to me. Add your own voice first.";
    $("voice-list").replaceChildren(...info.voices.map((v) => {
      const remove = el("button", { className: "ghost", textContent: "REMOVE", type: "button" });
      remove.onclick = async () => { await Voice.remove(v.name).catch(() => {}); showVoices(); };
      return el("li", null, el("span", { textContent: `${v.name} (${v.samples} samples)` }), remove);
    }));
  }
  $("open-voice").onclick = () => { $("menu").close(); $("voice-step").textContent = ""; $("voice").showModal(); showVoices(); };
  $("close-voice").onclick = () => $("voice").close();
  $("voice-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("voice-name").value.trim(), step = $("voice-step");
    const lines = [`Hey ${assistantName}, it's me. Open my project and show the status.`,
      "This is my voice. Only I may give you spoken commands.",
      "வணக்கம், இது என் குரல். என் வேலையைத் தொடங்கலாம்."];
    try {
      for (let i = 0; i < lines.length; i++) {
        step.textContent = `Sample ${i + 1} of 3 - read aloud now: "${lines[i]}"`;
        await Voice.enroll(name, 5);
      }
      step.textContent = "Voice added.";
    } catch (e) { step.textContent = `Could not add the voice: ${e.message}`; }
    showVoices();
  });
  $("open-holo").onclick = () => { $("menu").close(); Holo.open(); };
  $("open-data").onclick = () => { $("menu").close(); document.body.classList.add("show-data"); $("close-data").hidden = false; };
  $("close-data").onclick = () => { document.body.classList.remove("show-data"); $("close-data").hidden = true; };
  $("activity").addEventListener("pointerover", showTip);
  $("activity").addEventListener("focusin", showTip);
  $("activity").addEventListener("pointerout", hideTip);
  $("activity").addEventListener("focusout", hideTip);
  $("reset").onclick = () => { ws.send(JSON.stringify({ type: "reset" })); hush(); $("menu").close(); };
  function showMute() { $("mute").textContent = `VOICE REPLIES: ${muted ? "OFF" : "ON"}`; }
  $("mute").onclick = () => { muted = !muted; store.set("muted", muted ? "1" : "0"); if (muted) hush(); showMute(); };
  $("approve").onclick = () => answerConfirm(true);
  $("deny").onclick = () => answerConfirm(false);
  $("confirm").addEventListener("cancel", (ev) => { ev.preventDefault(); answerConfirm(false); });
  $("pair").addEventListener("cancel", (ev) => ev.preventDefault());
  $("pair-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    $("pair-error").textContent = "";
    const device = /Android|iPhone|iPad/i.test(navigator.userAgent) ? "Phone" : "Computer";
    const res = await fetch("api/pair", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: $("pin").value, device_name: device }) }).catch(() => null);
    if (res && res.ok) { store.set("token", (await res.json()).token); $("pair").close(); connect(); }
    else { $("pair-error").textContent = "That PIN was not accepted. Check the PC and try again."; $("pin").value = ""; }
  });
  addEventListener("keydown", (ev) => {     // hold-free shortcut: Space toggles the mic
    if (ev.code === "Space" && document.activeElement === document.body) { ev.preventDefault(); $("mic").click(); }
  });
  if (synth) synth.onvoiceschanged = () => {};   // make Chrome load its voice list early

  setLang(lang); showMute(); setState("idle"); connect();
})();
