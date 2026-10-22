window.PosPages = window.PosPages || {};
window.PosLabelQueue = Vue.reactive({
  rows: [], selectedKey: null, previewUrl: "", printing: false, notice: "",
});
let previewSeq = 0;
// 列印或清空後,先前尚未完成的加入／搜尋不再改動清單。
let queueSeq = 0;

// 加入列印清單的共用核心(標籤頁與進貨頁共用),不處理畫面選取與訊息。
// 回傳 result:added／merged(已有同列,張數累加)／blocked(同列已失效)／
// invalid(條碼不屬於此款式)／printing(列印中不可加入)／cancelled(期間已列印或清空)。
window.PosLabelQueueAdd = async function (variant_id, barcode, copies) {
  const q = window.PosLabelQueue;
  if (q.printing) return {result: "printing"};
  const key = `${variant_id}|${barcode}`, seq = queueSeq;
  const merge = () => {
    const existing = q.rows.find(r => r.key === key);
    if (!existing) return null;
    if (existing.status === "invalid") return {result: "blocked", key};
    existing.copies += copies;
    return {result: "merged", key};
  };
  const hit = merge();
  if (hit) return hit;
  const data = await API.checkLabels([{variant_id, barcode}]);
  if (q.printing || seq !== queueSeq) return {result: "cancelled"};
  if (!data.items[0].valid) return {result: "invalid"};
  // 同 key 的另一個加入請求可能已先完成,再查一次以免產生重複列。
  const again = merge();
  if (again) return again;
  q.rows.push({key, variant_id, barcode, copies, label: data.items[0].label, status: "ready"});
  return {result: "added", key};
};

window.PosPages["page-labels"] = {
  template: "#tpl-labels",
  inject: ["showError", "takePageContext"],
  data() { return { query: "", results: [], searched: false, q: window.PosLabelQueue }; },
  async mounted() {
    const ctx = this.takePageContext?.();
    if (ctx?.variant) await this.addFromContext(ctx.variant);
    // 從進貨頁加入的列沒有選取;進頁時沒有選取就選第一列,預覽才不會空白。
    if (!this.q.rows.some(r => r.key === this.q.selectedKey)) this.q.selectedKey = this.q.rows[0]?.key || null;
    await this.loadPreview();
  },
  watch: {
    query() { this.searched = false; },
  },
  methods: {
    rowKey(variant_id, barcode) { return `${variant_id}|${barcode}`; },
    defaultBarcode(row) {
      for (const source of ["store", "factory"]) {
        const codes = (row.barcodes || []).filter(b => b.source === source)
          .map(b => b.barcode).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
        if (codes.length) return codes[0];
      }
      return "";
    },
    sameLabel(a, b) {
      return a.name === b.name && a.specification === b.specification && a.price === b.price
        && a.models.length === b.models.length && a.models.every((m, i) => m === b.models[i]);
    },
    totalCopies() { return this.q.rows.reduce((sum, row) => sum + row.copies, 0); },
    priceText(row) { return row.price == null ? "" : `$${row.price}`; },
    modelsTitle(row) { return (row.models || []).join("、") || null; },
    rowText(row) {
      return [row.name, this.attrText(row), (row.models || []).join("、"),
        this.priceText(row), this.defaultBarcode(row)]
        .filter(part => part !== "" && part != null).join("｜");
    },
    statusText(row) {
      return {ready: "", changed: "資料已更新，請確認預覽", verify: "請核對已印張數",
        invalid: "條碼已失效，請移除後重新加入"}[row.status];
    },
    async addRow(variant_id, barcode) {
      if (this.q.printing) return;
      await this.guard(async () => {
        const added = await window.PosLabelQueueAdd(variant_id, barcode, 1);
        if (added.result === "blocked") this.q.notice = "此條碼已失效，請先移除該列後重新加入。";
        else if (added.result === "invalid") this.showError("此條碼已不屬於任何可列印的子產品。");
        else if (added.result === "added" || added.result === "merged") await this.selectRow(added.key);
      });
    },
    async submitInput() {
      if (this.q.printing) return;
      const query = this.query, code = query.trim(), seq = queueSeq;
      await this.guard(async () => {
        let data;
        // 掃描一律印店內碼，沒有店內碼才印原廠碼。
        try {
          const scanned = await window.PosScan(code);
          if (scanned === null) {
            this.query = ""; this.results = []; this.searched = false;
            return;
          }
          data = scanned.data;
        }
        catch (err) {
          if (err.status !== 404 && err.status !== 422) throw err;
        }
        if (this.q.printing || seq !== queueSeq) return;
        if (data) {
          await this.addRow(data.variant_id, data.label_barcode);
          if (this.q.printing || seq !== queueSeq || this.query !== query) return;
          this.query = ""; this.results = []; this.searched = false;
          return;
        }
        const products = await API.listCatalog({q: code});
        if (this.q.printing || seq !== queueSeq || this.query !== query) return;
        this.results = products.flatMap(p => (p.variants || []).map(v => ({...v, name: p.name})))
          .filter(row => this.defaultBarcode(row) !== "");
        this.searched = true;
      });
    },
    async pick(row) {
      if (this.q.printing) return;
      const seq = queueSeq;
      await this.addRow(row.variant_id, this.defaultBarcode(row));
      if (this.q.printing || seq !== queueSeq) return;
      this.results = []; this.searched = false; this.query = "";
    },
    async addFromContext(variant) {
      if (this.q.printing) {
        this.q.notice = "標籤列印中，請稍候再加入。";
        return;
      }
      const barcode = this.defaultBarcode(variant);
      if (!barcode) { this.showError("此子產品沒有條碼，無法列印標籤。"); return; }
      await this.addRow(variant.variant_id, barcode);
    },
    async selectRow(key) {
      this.q.selectedKey = key;
      await this.loadPreview();
    },
    async loadPreview() {
      const seq = ++previewSeq;
      const row = this.q.rows.find(r => r.key === this.q.selectedKey);
      this.q.previewUrl = "";
      if (!row) return;
      await this.guard(async () => {
        try {
          const data = await API.previewLabel(row.variant_id, row.barcode);
          if (seq !== previewSeq || this.q.selectedKey !== row.key) return;
          this.q.previewUrl = data.image;
        } catch (err) {
          if (seq !== previewSeq || this.q.selectedKey !== row.key) return;
          this.q.previewUrl = "";
          throw err;
        }
      });
    },
    setCopies(row, value) {
      if (this.q.printing) return;
      const n = Math.floor(Number(value));
      row.copies = Number.isFinite(n) && n >= 1 ? n : 1;
    },
    confirmCopies(row) {
      if (this.q.printing) return;
      if (row.status !== "verify") return;
      row.status = "ready";
      if (!this.q.rows.some(r => r.status === "verify")) this.q.notice = "";
    },
    removeRow(row) {
      if (this.q.printing) return;
      this.q.rows = this.q.rows.filter(r => r.key !== row.key);
      if (this.q.selectedKey === row.key) {
        this.q.selectedKey = this.q.rows[0]?.key || null;
        this.loadPreview();
      }
    },
    async clearAll() {
      if (this.q.printing) return;
      if (!this.q.rows.length || !await PosConfirm.ask("確定清空列印清單？")) return;
      if (this.q.printing) return;
      ++queueSeq; ++previewSeq;
      this.q.rows = []; this.q.selectedKey = null; this.q.previewUrl = ""; this.q.notice = "";
    },
    async printAll() {
      if (this.q.printing) return;
      if (!this.q.rows.length) return;
      if (this.q.rows.some(r => r.status === "invalid")) {
        this.q.notice = "有條碼已失效的項目，請移除後重新加入。"; return;
      }
      if (this.q.rows.some(r => r.status === "verify")) {
        this.q.notice = "有待核對張數的項目，請先按「確認張數」。"; return;
      }
      const total = this.totalCopies();
      if (!await PosConfirm.ask(`確定列印 ${total} 張標籤？`)) return;
      if (this.q.printing || !this.q.rows.length) return;
      // 確認期間可能由另一個待完成工作改動狀態,送印前仍須檢查。
      if (this.q.rows.some(r => r.status === "invalid" || r.status === "verify")) return;
      this.q.printing = true; this.q.notice = ""; ++queueSeq;
      try {
        const rows = [...this.q.rows];
        const res = await API.checkLabels(rows.map(r => ({variant_id: r.variant_id, barcode: r.barcode})));
        const changed = [];
        rows.forEach((row, i) => {
          const current = res.items[i];
          if (!current.valid) { row.status = "invalid"; changed.push(row.key); }
          else if (!this.sameLabel(row.label, current.label)) {
            row.label = current.label; row.status = "changed"; changed.push(row.key);
          }
        });
        if (changed.length) {
          this.q.notice = "部分商品資料已變更或條碼失效，請確認後再列印。";
          if (changed.includes(this.q.selectedKey)) this.loadPreview();
          return;
        }
        for (const row of rows) if (row.status === "changed") row.status = "ready";
        for (const row of rows) {
          try { await API.printBarcode(row.variant_id, row.barcode, row.copies); }
          catch (err) {
            let invalid = false;
            try {
              const again = await API.checkLabels([{variant_id: row.variant_id, barcode: row.barcode}]);
              invalid = !again.items[0].valid;
            } catch (_) { /* 無法重查時保留待核對,不自動重送。 */ }
            row.status = invalid ? "invalid" : "verify";
            this.q.notice = invalid ? "條碼已失效，列印已停止。"
              : `列印失敗：${err.message}。請核對已印張數後按「確認張數」。`;
            this.selectRow(row.key);
            return;
          }
          this.q.rows = this.q.rows.filter(r => r.key !== row.key);
          if (this.q.selectedKey === row.key) {
            this.q.selectedKey = this.q.rows[0]?.key || null;
            this.loadPreview();
          }
        }
        PosConfirm.notify(`已列印 ${total} 張標籤。`);
      } catch (err) { this.showError(err.message); }
      finally { this.q.printing = false; }
    },
  },
};
