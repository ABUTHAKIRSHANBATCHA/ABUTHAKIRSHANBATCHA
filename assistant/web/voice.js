// Voice lock client: records short samples and asks the PC who is speaking.
(() => {
  const token = () => { try { return localStorage.getItem("token") || ""; } catch { return ""; } };
  const headers = () => ({ "X-Lumi-Token": token() });

  // Record `seconds` of microphone audio as 16 kHz mono 16-bit PCM.
  async function record(seconds) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
    const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
    const source = ctx.createMediaStreamSource(stream);
    const tap = ctx.createScriptProcessor(4096, 1, 1);
    const chunks = [];
    tap.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(tap); tap.connect(ctx.destination);
    await new Promise((done) => setTimeout(done, seconds * 1000));
    tap.disconnect(); source.disconnect(); stream.getTracks().forEach((t) => t.stop());
    const rate = ctx.sampleRate; await ctx.close();
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const step = rate / 16000, out = new Int16Array(Math.floor(total / step));
    const flat = new Float32Array(total); let at = 0;
    for (const c of chunks) { flat.set(c, at); at += c.length; }
    for (let i = 0; i < out.length; i++) out[i] = Math.max(-1, Math.min(1, flat[Math.floor(i * step)])) * 32767;
    return out.buffer;
  }
  async function call(path, options) {
    const res = await fetch(path, { ...options, headers: { ...headers(), ...(options && options.headers) } });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || "Request failed");
    return data;
  }
  window.Voice = {
    record,
    info: () => call("api/voice"),
    verify: async (seconds = 3) => call("api/voice/verify", { method: "POST", body: await record(seconds) }),
    enroll: async (name, seconds = 4) =>
      call(`api/voice/enroll?name=${encodeURIComponent(name)}`, { method: "POST", body: await record(seconds) }),
    remove: (name) => call(`api/voice?name=${encodeURIComponent(name)}`, { method: "DELETE" }),
  };
})();
