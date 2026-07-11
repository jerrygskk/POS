const API = {
  async _do(method, url, body) {
    const opt = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    const r = await fetch(url, opt);
    if (!r.ok) {
      let msg = "系統發生錯誤";
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  },
  get(url) { return this._do("GET", url); },
  post(url, body) { return this._do("POST", url, body); },
  put(url, body) { return this._do("PUT", url, body); },
  patch(url, body) { return this._do("PATCH", url, body); },
  del(url) { return this._do("DELETE", url); },
};

// ---- 規格顯示與表單共用工具(multi/tags 皆為清單)----

// 規格顯示字串:優先用後端組好的 attr_display(遵守 spec §2 順位);
// 退而求其次以本地屬性 dict 兜底(陣列以「+」連、各欄以「｜」分隔)。
window.fmtAttr = function (row) {
  if (row && typeof row.attr_display === "string" && row.attr_display)
    return row.attr_display;
  const a = (row && row.attributes) || row || {};
  const parts = [];
  for (const v of Object.values(a)) {
    if (Array.isArray(v)) { if (v.length) parts.push(v.join("+")); }
    else if (v != null && v !== "") parts.push(v);
  }
  return parts.join("｜");
};

// 依欄型初始化表單屬性值:multi=陣列、tags=逗號字串、select 有預設即帶入。
// existing 為既有變體屬性(編輯時傳入),既有值優先於預設。
window.initFormAttrs = function (fields, existing) {
  existing = existing || {};
  const a = {};
  for (const f of (fields || [])) {
    const cur = existing[f.name];
    if (f.field_type === "multi")
      a[f.name] = Array.isArray(cur) ? cur.slice() : [];
    else if (f.field_type === "tags")
      a[f.name] = Array.isArray(cur) ? cur.join(", ") : (cur || "");
    else if (cur != null && cur !== "")
      a[f.name] = cur;
    else if (f.field_type === "select" && f.default_value)
      a[f.name] = f.default_value;
    else
      a[f.name] = "";
  }
  return a;
};

// 表單屬性 → API 送出格式:multi/tags 送清單、select/text 送字串;空值略過。
window.buildAttrPayload = function (fields, attrs) {
  const out = {};
  for (const f of (fields || [])) {
    const v = attrs[f.name];
    if (f.field_type === "multi") {
      const arr = (Array.isArray(v) ? v : []).map(x => String(x).trim())
        .filter(Boolean);
      if (arr.length) out[f.name] = arr;
    } else if (f.field_type === "tags") {
      const arr = String(v || "").split(/[,、，]/).map(x => x.trim())
        .filter(Boolean);
      if (arr.length) out[f.name] = arr;
    } else {
      const s = (v == null ? "" : String(v)).trim();
      if (s) out[f.name] = s;
    }
  }
  return out;
};
