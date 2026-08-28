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
    const shared = [["__price", "price"], ["__barcode", "barcode"], ["__models", "model_ids"]];
    for (const entry of shared) {
      if (rows.some(row => !sameValue(row[entry[1]], first[entry[1]]))) changed.add(entry[0]);
    }
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

  window.VariantBatchLogic = {
    expandAxes,
    formulaText,
    expandRows,
    duplicateRow,
    diffFieldNames,
    partitionPrecheck,
    dupRefText,
  };
})();
