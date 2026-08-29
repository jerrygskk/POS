(function () {
  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function expandAxes(formalFields, attrs) {
    const axes = [];
    for (const field of (formalFields || [])) {
      if (field.field_type !== "select") continue;
      const values = Array.isArray((attrs || {})[field.name])
        ? (attrs || {})[field.name].filter(value => value != null && String(value).trim())
        : [];
      if (values.length) axes.push({ name: field.name, values: values.slice() });
    }
    const count = axes.reduce((total, axis) => total * axis.values.length, 1);
    return { axes, count };
  }

  function formulaText(axes) {
    if (!axes || axes.length < 2) return "";
    const count = axes.reduce((total, axis) => total * axis.values.length, 1);
    if (count <= 1) return "";
    return axes.map(axis => axis.values.length + " 個" + axis.name).join(" × ")
      + "＝" + count + " 筆";
  }

  function expandRows(formalFields, input, seqStart) {
    const source = input || {};
    const attrs = source.attrs || {};
    const axes = expandAxes(formalFields, attrs).axes;
    let combinations = [{}];
    for (const axis of axes) {
      const next = [];
      for (const combination of combinations) {
        for (const value of axis.values) {
          const expanded = Object.assign({}, combination);
          expanded[axis.name] = value;
          next.push(expanded);
        }
      }
      combinations = next;
    }
    return combinations.map((combination, index) => {
      const rowAttrs = clone(attrs);
      Object.assign(rowAttrs, combination);
      return {
        draft_id: "d" + (seqStart + index + 1),
        attrs: rowAttrs,
        price: source.price === "" ? null : source.price,
        model_ids: clone(source.model_ids || []),
        barcode: combinations.length === 1 ? (source.barcode || "").trim() : "",
        store: !!source.store,
      };
    });
  }

  function duplicateRow(row, seq) {
    const copy = clone(row);
    copy.draft_id = "d" + seq;
    copy.barcode = "";
    return copy;
  }

  function sameValue(left, right) {
    return JSON.stringify(left) === JSON.stringify(right);
  }

  function diffFieldNames(rows, formalFields) {
    const changed = new Set();
    if (!rows || rows.length < 2) return changed;
    const first = rows[0];
    for (const field of (formalFields || [])) {
      if (rows.some(row => !sameValue(row.attrs && row.attrs[field.name],
        first.attrs && first.attrs[field.name]))) changed.add(field.name);
    }
    const shared = [["__price", "price"], ["__models", "model_ids"]];
    for (const entry of shared) {
      if (rows.some(row => !sameValue(row[entry[1]], first[entry[1]]))) changed.add(entry[0]);
    }
    const firstBarcode = [first.barcode, !!first.store];
    if (rows.some(row => !sameValue([row.barcode, !!row.store], firstBarcode)))
      changed.add("__barcode");
    return changed;
  }

  function partitionPrecheck(rows, precheckResults) {
    const byDraftId = new Map();
    for (const result of (precheckResults || [])) byDraftId.set(result.draft_id, result);
    const kept = [];
    const skipped = [];
    const errorsByDraftId = {};
    for (const row of (rows || [])) {
      const result = byDraftId.get(row.draft_id) || {};
      if (result.existing_duplicate) {
        skipped.push({ row, related_variant_id: result.related_variant_id });
        continue;
      }
      kept.push(row);
      if (result.errors && result.errors.length)
        errorsByDraftId[row.draft_id] = result.errors.slice();
    }
    return { kept, skipped, errorsByDraftId };
  }

  function dupRefText(err, rows) {
    const index = (rows || []).findIndex(row => row.draft_id === err.related_draft_id);
    return index < 0 ? "與已移除的一筆重複" : "與第 " + (index + 1) + " 筆重複";
  }


  // 欄寬計算:以「半形寬」估字寬(中文與全形符號算 2),再依欄位型態補上
  // 內距、下拉箭頭等固定佔位。純函式,可在 Node 單測。
  const HALF_CHAR_PX = 7.5;
  const CELL_PADDING_PX = 24;
  const KIND_EXTRA_PX = { select: 30, input: 18, button: 16, text: 0 };
  const KIND_MAX_PX = { select: 260, input: 260, button: 240, text: 200 };
  const COLUMN_MIN_PX = 70;

  function halfWidth(text) {
    let total = 0;
    for (const ch of String(text == null ? "" : text)) {
      total += ch.charCodeAt(0) > 0x2000 ? 2 : 1;
    }
    return total;
  }

  // measure(text) 回傳實際像素寬;沒給就用半形寬估算(僅測試與退路使用,
  // 實際畫面一律傳入以 canvas 量到的真實字寬,英數字寬與估算差得夠多)。
  function columnWidth(column, measure) {
    const kind = (column || {}).kind || "text";
    const samples = [(column || {}).label || ""].concat((column || {}).samples || []);
    const textWidth = typeof measure === "function"
      ? (text => measure(text)) : (text => halfWidth(text) * HALF_CHAR_PX);
    const widest = samples.reduce((max, text) => Math.max(max, textWidth(text)), 0);
    const wanted = Math.ceil(widest) + CELL_PADDING_PX + (KIND_EXTRA_PX[kind] || 0);
    const floor = Math.max(COLUMN_MIN_PX, (column || {}).min || 0);
    const ceiling = Math.max(floor, (column || {}).max || KIND_MAX_PX[kind] || 260);
    return Math.min(Math.max(wanted, floor), ceiling);
  }

  function columnLayout(columns, measure) {
    const widths = {};
    for (const column of (columns || []))
      widths[column.key] = columnWidth(column, measure);
    return widths;
  }

  function resolveWidths(columns, overrides, measure) {
    const auto = columnLayout(columns, measure);
    const out = {};
    for (const column of (columns || [])) {
      const manual = (overrides || {})[column.key];
      out[column.key] = manual > 0 ? Math.max(COLUMN_MIN_PX, Math.round(manual))
        : auto[column.key];
    }
    return out;
  }

  function totalWidth(columns, overrides, measure) {
    const widths = resolveWidths(columns, overrides, measure);
    return (columns || []).reduce((sum, column) => sum + widths[column.key], 0);
  }

  window.VariantBatchLogic = {
    expandAxes,
    formulaText,
    expandRows,
    duplicateRow,
    diffFieldNames,
    partitionPrecheck,
    dupRefText,
    halfWidth,
    columnWidth,
    columnLayout,
    resolveWidths,
    totalWidth,
    COLUMN_MIN_PX,
  };
})();
