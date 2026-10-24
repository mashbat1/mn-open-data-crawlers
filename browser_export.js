/* Paste into DevTools Console on an already open PUBLIC Tender procurement page.
 * Uses ordinary same-origin GETs in this browser. Does not read/export cookies,
 * authentication headers, account pages, attachments or challenge resources.
 * A browser security challenge stops the export. No stealth or challenge solver.
 * Writes JSONL directly to a file, so the full crawl is never held in memory.
 */
(() => {
  "use strict";
  const sections = ["plan", "invitation", "result", "contract", "implementation", "performance",
    "complaint", "blacklist", "supplier", "organization", "customer", "statistics"];
  const requiredSections = ["plan", "invitation", "result", "contract", "implementation"];
  const classify = (value) => {
    const parts = new URL(value, location.href).pathname.split("/").filter(Boolean);
    if (["mn", "en"].includes(parts[0])) parts.shift();
    if (!sections.includes(parts[0])) return null;
    if (parts.slice(1).some((p) => /^(create|edit|delete|submit|login|account|profile|register)$/i.test(p))) return null;
    return { section: parts[0], detail: parts.length > 1 && parts[0] !== "statistics" };
  };
  if (location.hostname !== "www.tender.gov.mn" || !classify(location.href)) {
    throw new Error("Open https://www.tender.gov.mn/mn/invitation first.");
  }
  if (document.getElementById("tender-public-export")) return;
  const box = document.createElement("aside");
  box.id = "tender-public-export";
  Object.assign(box.style, { position: "fixed", bottom: "20px", right: "20px", zIndex: "2147483647",
    background: "#101827", color: "white", padding: "18px", border: "1px solid #64748b",
    borderRadius: "12px", width: "340px", font: "14px/1.5 sans-serif" });
  const status = document.createElement("p");
  status.textContent = "Нээлттэй төлөвлөгөө, урилга, үр дүн, гэрээний холбоосыг дагана. Хавсралт татахгүй. Харагдаагүй хэсгийг бүрэн гэж тооцохгүй.";
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
      for (const k of [...u.searchParams.keys()]) {
        if (/token|secret|auth|session|__cf/i.test(k)) return null;
        if (k === "_rsc") u.searchParams.delete(k);
      }
      u.hash = "";
      u.searchParams.sort();
      return u;
    } catch { return null; }
  };
  const attachment = (u) => /\/download\/|\/uploads\/|\.(pdf|docx?|xlsx?|zip|rar|7z)(?:$|\?)/i.test(u);
  const parse = (doc, url) => {
    const main = (doc.querySelector("main") || doc.body)?.cloneNode(true);
    if (!main) throw new Error("No document body");
    main.querySelector("#tender-public-export")?.remove();
    main.querySelectorAll("script,style,noscript").forEach((e) => e.remove());
    const text = (main.innerText || main.textContent || "").replace(/\s+/g, " ").trim();
    if (/Just a moment|Performing security verification|Verify you are human/i.test(doc.title + " " + text)) {
      throw new Error("Browser security verification appeared; export stopped");
    }
    const links = [...doc.querySelectorAll("a[href]")].map((a) => {
      let u;
      try { u = new URL(a.getAttribute("href"), url); } catch { return null; }
      if (u.origin !== origin) return null;
      if (attachment(u.href)) return { url: u.origin + u.pathname, name: a.textContent.trim(), attachment: true };
      const clean = safeURL(u.href, url);
      return clean ? { url: clean.href, name: a.textContent.trim(), attachment: false } : null;
    }).filter(Boolean);
    const tables = [...main.querySelectorAll("table")].map((t) => [...t.querySelectorAll("tr")]
      .map((r) => [...r.querySelectorAll("th,td")].map((c) => c.textContent.replace(/\s+/g, " ").trim())));
    const totalMatch = text.match(/Нийт\s*[:：]?\s*\(?\s*(\d{1,3}(?:[ ,]\d{3})+|\d+)/i);
    return { title: doc.title, text, links, tables,
      reported_total: totalMatch ? Number(totalMatch[1].replace(/\D/g, "")) : null,
      exposed_year_filters: [...doc.querySelectorAll('select[name="years"] option,select[name="year"] option')]
        .filter((o) => /^\d{4}$/.test(o.value)).map((o) => ({ param: o.closest("select").name, value: o.value })) };
  };
  const publicAPI = (value) => {
    const u = safeURL(value, origin);
    if (!u || !/^\/api\/(statistics|invitations?|results?|contracts?|plans?|tenders?)(\/|$)/.test(u.pathname)) return null;
    if (/\/(account|profile|auth|login|create|edit|delete|submit)(\/|$)/i.test(u.pathname)) return null;
    return u;
  };
  const publicPayload = (body) => JSON.parse(JSON.stringify(body, (key, value) =>
    /password|token|secret|authorization|cookie|session/i.test(key) ? undefined : value));
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
    const observed = {};
    let records = 0;
    let handle, bytes = 0;
    const write = async (record) => {
      const line = JSON.stringify(record) + "\n";
      await writer.write(line); bytes += new TextEncoder().encode(line).length; records++;
      // Commit a recoverable partial file during long exports.
      if (records % 50 === 0) {
        await writer.close(); writer = await handle.createWritable({ keepExistingData: true });
        await writer.seek(bytes);
      }
    };
    try {
      handle = await window.showSaveFilePicker({ suggestedName: "tender-public-export.jsonl",
        types: [{ description: "Tender public data (JSONL)", accept: { "application/x-ndjson": [".jsonl"] } }] });
      writer = await handle.createWritable();
      start.disabled = true;
      stop.disabled = false;
      await write({ type: "metadata", source: origin, created: new Date().toISOString(),
        scope: "Public procurement pages linked by the site; no attachments",
        required_sections: requiredSections, complete: false });
      const first = parse(document, seed.href);
      const queue = [safeURL(seed.href, origin).href];
      const queued = new Set(queue);
      const enqueue = (value) => {
        const u = safeURL(value, seed.href);
        if (u && classify(u.href) && !queued.has(u.href)) { queued.add(u.href); queue.push(u.href); }
      };
      while (queue.length && !stopped) {
        const url = safeURL(queue.shift(), seed.href);
        if (!url || visited.has(url.href)) continue;
        visited.add(url.href);
        let page = first;
        if (url.href !== safeURL(seed.href, origin).href) {
          await delay();
          const r = await fetch(url.href, { credentials: "same-origin" });
          if (!r.ok) throw new Error(`HTTP ${r.status}: ${url.pathname}`);
          page = parse(new DOMParser().parseFromString(await r.text(), "text/html"), url.href);
        }
        const kind = classify(url.href);
        observed[kind.section] ||= { list_pages: 0, detail_pages: 0 };
        observed[kind.section][kind.detail ? "detail_pages" : "list_pages"]++;
        await write({ type: "page", kind: kind.section + (kind.detail ? "_detail" : "_list"), url: url.href, ...page });
        const year = url.searchParams.get("years") || url.searchParams.get("year") || "default";
        let group;
        if (kind.section === "invitation" && !kind.detail) {
          if (!coverage.has(year)) coverage.set(year, { reported_total: page.reported_total, ids: new Set() });
          group = coverage.get(year);
          if (group.reported_total !== page.reported_total) errors.push(`List total changed for year ${year}`);
        }
        if (!kind.detail) for (const filter of page.exposed_year_filters) {
          const target = new URL(url); target.searchParams.set(filter.param, filter.value);
          for (const p of ["page", "current_page", "offset"]) target.searchParams.delete(p);
          enqueue(target.href);
        }
        for (const link of page.links) {
          if (link.attachment) continue;
          const target = safeURL(link.url, url.href);
          if (!target) continue;
          const linkedKind = classify(target.href);
          if (linkedKind?.section === "invitation" && linkedKind.detail) {
            details.add(target.href); group?.ids.add(target.href);
          }
          enqueue(target.href);
        }
        status.textContent = `${visited.size} жагсаалтын хуудас · ${details.size} урилга олдсон`;
      }
      // Read only API URLs actually requested by this public page. Never
      // export request headers/cookies or invent endpoints/page parameters.
      const apiQueue = performance.getEntriesByType("resource").map((entry) => publicAPI(entry.name)).filter(Boolean);
      const apiSeen = new Set();
      while (apiQueue.length && !stopped) {
        const u = apiQueue.shift();
        if (apiSeen.has(u.href)) continue;
        apiSeen.add(u.href);
        await delay();
        const response = await fetch(u.href, { credentials: "same-origin", headers: { Accept: "application/json" } });
        if ([401, 403, 429].includes(response.status)) throw new Error(`HTTP ${response.status}: public API; export stopped`);
        if (!response.ok || !/json/i.test(response.headers.get("content-type") || "")) {
          errors.push(`Uncollected public API response: HTTP ${response.status} ${u.pathname}`); continue;
        }
        const body = publicPayload(await response.json());
        await write({ type: "api_response", url: u.href, data: body, secret_fields_removed: true });
        const next = body?.next || body?.links?.next || body?.pagination?.next;
        if (typeof next === "string") {
          const target = publicAPI(new URL(next, u.href).href);
          if (target) apiQueue.push(target);
        }
      }
      const counts = Object.fromEntries([...coverage].map(([y, v]) => [y, { reported_total: v.reported_total,
        unique_invitations: v.ids.size, verified: v.reported_total !== null && v.ids.size === v.reported_total }]));
      const scopeComplete = !stopped && errors.length === 0 && Object.values(counts).length > 0
        && Object.values(counts).every((v) => v.verified);
      await write({ type: "manifest", complete: false, scope_complete: scopeComplete,
        nationwide_completeness_verified: false, stopped, records, coverage: counts, sections: observed,
        missing_sections: requiredSections.filter((s) => !observed[s]), errors });
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
