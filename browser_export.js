/* Paste into DevTools Console on an already open PUBLIC Tender invitation list.
 * Uses ordinary same-origin GETs in this browser. Does not read/export cookies,
 * authentication headers, account pages, attachments or challenge resources.
 * A browser security challenge stops the export. No stealth or challenge solver.
 * Writes JSONL directly to a file, so the full crawl is never held in memory.
 */
(() => {
  "use strict";
  if (location.hostname !== "www.tender.gov.mn" || !/\/invitation\/?$/.test(location.pathname)) {
    throw new Error("Open https://www.tender.gov.mn/mn/invitation first.");
  }
  if (document.getElementById("tender-public-export")) return;
  const box = document.createElement("aside");
  box.id = "tender-public-export";
  Object.assign(box.style, { position: "fixed", bottom: "20px", right: "20px", zIndex: "2147483647",
    background: "#101827", color: "white", padding: "18px", border: "1px solid #64748b",
    borderRadius: "12px", width: "340px", font: "14px/1.5 sans-serif" });
  const status = document.createElement("p");
  status.textContent = "Нээлттэй жагсаалт, дэлгэрэнгүй мэдээлэл. Хавсралт татахгүй. Файл хадгалах цонх гарна.";
  const start = document.createElement("button");
  start.textContent = "Тендерийн өгөгдөл экспортлох";
  const stop = document.createElement("button");
  stop.textContent = "Хадгалаад зогсоох";
  stop.disabled = true;
  box.append(status, start, stop);
  document.body.append(box);
  let stopped = false;
  stop.onclick = () => { stopped = true; status.textContent = "Одоогийн хуудсыг хадгалаад зогсоож байна…"; };
  const origin = location.origin;
  const seed = new URL(location.href);
  const delay = () => new Promise((resolve) => setTimeout(resolve, 2000));
  const safeURL = (href, base) => {
    try {
      const u = new URL(href, base);
      if (u.origin !== origin || /\/cdn-cgi\/|\/uploads\/|\/download\//.test(u.pathname)) return null;
      for (const k of [...u.searchParams.keys()]) if (/token|secret|auth|session|__cf|_rsc/i.test(k)) u.searchParams.delete(k);
      u.hash = "";
      return u;
    } catch { return null; }
  };
  const detailURL = (u) => /\/invitation\/(?:detail\/)?[\w-]*\d[\w-]*\/?$/.test(u.pathname);
  const attachment = (u) => /\/download\/|\/uploads\/|\.(pdf|docx?|xlsx?|zip|rar|7z)(?:$|\?)/i.test(u);
  const parse = (doc, url) => {
    const main = doc.querySelector("main") || doc.body;
    if (!main) throw new Error("No document body");
    const text = (main.innerText || main.textContent || "").replace(/\s+/g, " ").trim();
    if (/Just a moment|Performing security verification|Verify you are human/i.test(doc.title + " " + text)) {
      throw new Error("Browser security verification appeared; export stopped");
    }
    const links = [...main.querySelectorAll("a[href]")].map((a) => {
      const u = new URL(a.getAttribute("href"), url);
      if (u.origin !== origin) return null;
      if (attachment(u.href)) return { url: u.origin + u.pathname, name: a.textContent.trim(), attachment: true };
      const clean = safeURL(u.href, url);
      return clean ? { url: clean.href, name: a.textContent.trim(), attachment: false } : null;
    }).filter(Boolean);
    const tables = [...main.querySelectorAll("table")].map((t) => [...t.querySelectorAll("tr")]
      .map((r) => [...r.querySelectorAll("th,td")].map((c) => c.textContent.replace(/\s+/g, " ").trim())));
    const totalMatch = text.match(/Нийт\s*[:：]?\s*\(?\s*([\d, ]+)\s*\)?/i);
    return { title: doc.title, text, links, tables,
      reported_total: totalMatch ? Number(totalMatch[1].replace(/\D/g, "")) : null };
  };
  start.onclick = async () => {
    if (!window.showSaveFilePicker) {
      status.textContent = "Chrome эсвэл Edge дээр нээгээрэй: файл руу шууд бичих боломж шаардлагатай.";
      return;
    }
    let writer;
    const errors = [];
    const visited = new Set();
    const details = new Set();
    const coverage = new Map();
    let records = 0;
    const write = async (record) => { await writer.write(JSON.stringify(record) + "\n"); records++; };
    try {
      const handle = await window.showSaveFilePicker({ suggestedName: "tender-public-export.jsonl",
        types: [{ description: "Tender public data (JSONL)", accept: { "application/x-ndjson": [".jsonl"] } }] });
      writer = await handle.createWritable();
      start.disabled = true;
      stop.disabled = false;
      await write({ type: "metadata", source: origin, created: new Date().toISOString(),
        scope: "Public invitation pages linked by the site; no attachments", complete: false });
      const first = parse(document, seed.href);
      await write({ type: "page", kind: "invitation_list", url: safeURL(seed.href, origin).href, ...first });
      const queue = [seed.href];
      const initialYear = Number(seed.searchParams.get("years"));
      const years = Number.isInteger(initialYear) && initialYear >= 2000 ? [initialYear] : [];
      // Only years actually exposed in this page's links or native filter options.
      for (const a of document.querySelectorAll("a[href]")) {
        const u = safeURL(a.getAttribute("href"), seed.href);
        if (u?.pathname === seed.pathname && /^\d{4}$/.test(u.searchParams.get("years") || "")) years.push(Number(u.searchParams.get("years")));
      }
      for (const o of document.querySelectorAll('select[name="years"] option')) if (/^\d{4}$/.test(o.value)) years.push(Number(o.value));
      for (const year of new Set(years)) {
        const u = new URL(seed); u.searchParams.set("years", year); queue.push(u.href);
      }
      while (queue.length && !stopped) {
        const url = safeURL(queue.shift(), seed.href);
        if (!url || visited.has(url.href)) continue;
        visited.add(url.href);
        let page = first;
        if (url.href !== seed.href) {
          await delay();
          const r = await fetch(url.href, { credentials: "same-origin" });
          if (!r.ok) throw new Error(`HTTP ${r.status}: ${url.pathname}`);
          page = parse(new DOMParser().parseFromString(await r.text(), "text/html"), url.href);
          await write({ type: "page", kind: "invitation_list", url: url.href, ...page });
        }
        const year = url.searchParams.get("years") || "default";
        if (!coverage.has(year)) coverage.set(year, { reported_total: page.reported_total, ids: new Set() });
        const group = coverage.get(year);
        if (group.reported_total !== page.reported_total) errors.push(`List total changed for year ${year}`);
        for (const link of page.links) {
          if (link.attachment) continue;
          const target = safeURL(link.url, url.href);
          if (!target) continue;
          if (detailURL(target)) { details.add(target.href); group.ids.add(target.href); }
          if (target.pathname === seed.pathname && ["page", "current_page", "offset"].some((p) => target.searchParams.has(p))
              && (target.searchParams.get("years") || "default") === year) queue.push(target.href);
        }
        status.textContent = `${visited.size} жагсаалтын хуудас · ${details.size} урилга олдсон`;
      }
      for (const url of details) {
        if (stopped) break;
        await delay();
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) throw new Error(`HTTP ${r.status}: invitation detail`);
        await write({ type: "page", kind: "invitation_detail", url,
          ...parse(new DOMParser().parseFromString(await r.text(), "text/html"), url) });
        status.textContent = `${records} бичлэг файлд хадгалсан · ${details.size} урилга`;
      }
      const counts = Object.fromEntries([...coverage].map(([y, v]) => [y, { reported_total: v.reported_total,
        unique_invitations: v.ids.size, verified: v.reported_total !== null && v.ids.size === v.reported_total }]));
      await write({ type: "manifest", complete: !stopped && errors.length === 0 && Object.values(counts).length > 0
        && Object.values(counts).every((v) => v.verified), stopped, records, coverage: counts, errors });
      status.textContent = "Экспорт хадгалагдлаа. manifest нь татсан хүрээ, бүрэн эсэхийг тэмдэглэсэн. Бүх жилийн хамралтыг тусад нь шалгана.";
    } catch (e) {
      if (writer) {
        await write({ type: "manifest", complete: false, stopped: true, records, errors: [...errors, String(e.message)] });
      }
      status.textContent = `Зогссон: ${e.message}. Хадгалсан хэсэг файлд үлдэнэ.`;
    } finally {
      if (writer) await writer.close();
      start.disabled = false;
      stop.disabled = true;
    }
  };
})();
