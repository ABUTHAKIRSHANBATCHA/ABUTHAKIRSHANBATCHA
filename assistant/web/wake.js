// Wake phrase matching ("Hey Lumi"). Speech recognition often mishears a short
// invented name, so close spellings and the Tamil forms are accepted too.
(function (root) {
  const VARIANTS = {
    lumi: ["lumi", "loomi", "lumy", "loomy", "lumie", "lumee", "loomie", "lummi", "lumii", "roomie",
           "லூமி", "லுமி", "லூமீ"],
  };
  const GREETING = "(?:hey|hay|hi|hello|ok|okay|ஹே|ஹேய்|ஹாய்)";
  const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  // Returns the command that follows the wake phrase, "" when only the wake
  // phrase was said, or null when the utterance does not start with it.
  function strip(text, name) {
    const key = String(name || "").toLowerCase().trim();
    if (!key) return text.trim();
    const names = (VARIANTS[key] || [key]).map(escape).join("|");
    const pattern = new RegExp(`^[\\s,.!]*(?:${GREETING}[\\s,.!]+)?(?:${names})(?=$|[\\s,.!?])[\\s,.!?]*`, "i");
    const m = text.match(pattern);
    return m ? text.slice(m[0].length).trim() : null;
  }
  root.Wake = { strip };
  if (typeof module !== "undefined") module.exports = { strip };
})(typeof window !== "undefined" ? window : globalThis);
