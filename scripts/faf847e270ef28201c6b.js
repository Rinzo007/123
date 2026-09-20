() => {
  const out = [];
  const seenRoots = new WeakSet();
  const scan = (root, path) => {
    if (!root || !root.querySelectorAll || seenRoots.has(root)) return;
    seenRoots.add(root);
    try {
      Array.from(root.querySelectorAll("script")).forEach((s, i) => {
        out.push({
          index: i, src: s.src || null, type: s.type || null,
          inline: !s.src, text: s.src ? null : (s.textContent || ""), domPath: path
        });
      });
      Array.from(root.querySelectorAll("template")).forEach((tpl) => {
        if (tpl.content) scan(tpl.content, path + " > template");
      });
      Array.from(root.querySelectorAll("*")).forEach((el) => {
        if (el.shadowRoot) scan(el.shadowRoot, path + " > shadowRoot");
      });
    } catch (e) {}
  };
  scan(document, "document");
  return out;
}