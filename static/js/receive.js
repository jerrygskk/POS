window.PosPages = window.PosPages || {};
// 切頁保留本次開啟程式的進貨清單；關閉程式後清空。
window.PosReceiveDraft = Vue.reactive({
  rows: [], notFound: "", highlightKey: null, doneMsg: "", locked: false, generation: 0,
});
window.PosPages["page-receive"] = {
  template: "#tpl-receive",
  inject: ["showError", "goPage"],
  data() { return { scanCode: "", draft: window.PosReceiveDraft, employee: window.PosEmployee }; },
  computed: {
    totalQty() { return this.draft.rows.reduce((sum, row) => sum + row.qty, 0); },
  },
  mounted() { this.$refs.scan?.focus(); },
  methods: {
    async onScan() {
      if (this.draft.locked) {
        this.showError("進貨清單處理中，請稍候再掃描。");
        this.scanCode = "";
        this.$refs.scan?.focus();
        return;
      }
      const code = String(this.scanCode || "").trim(), gen = this.draft.generation;
      try {
        const query = await window.PosScan(code);
        if (query === null) { this.scanCode = ""; return; }
        if (this.draft.locked || this.draft.generation !== gen) {
          this.showError("掃描期間進貨清單已變動，請重新掃描。");
          this.scanCode = "";
          return;
        }
        const data = query.data;
        this.draft.notFound = "";
        this.draft.doneMsg = "";
        this.scanCode = "";
        // 查找必須在掃描回來之後，兩次快掃才不會產生重複列。
        const index = this.draft.rows.findIndex(r => r.variant_id === data.variant_id);
        let row;
        if (index >= 0) {
          [row] = this.draft.rows.splice(index, 1);
          row.qty += 1;
          row.stock = data.stock;
        } else {
          row = { variant_id: data.variant_id, name: data.name, attributes: data.attributes,
            attr_display: data.attr_display, label_barcode: data.label_barcode, stock: data.stock,
            qty: 1, labeled: 0, labelBusy: false };
        }
        this.draft.rows.unshift(row);
        this.draft.highlightKey = row.variant_id;
        setTimeout(() => {
          if (this.draft.highlightKey === row.variant_id) this.draft.highlightKey = null;
        }, 1500);
      } catch (e) {
        if (e.status === 404) { this.draft.notFound = code; this.scanCode = ""; }
        else this.showError(e.message);
      } finally { this.$refs.scan?.focus(); }
    },
    setQty(row, event) {
      if (this.draft.locked) { event.target.value = row.qty; return; }
      row.qty = Math.max(1, Math.floor(Number(event.target.value)) || 1);
      event.target.value = row.qty;
    },
    async addToLabels(row) {
      if (this.draft.locked || row.labelBusy) return;
      const k = row.qty - row.labeled;
      if (k <= 0) return;
      row.labelBusy = true;
      try {
        const added = await window.PosLabelQueueAdd(row.variant_id, row.label_barcode, k);
        if (added.result === "added" || added.result === "merged") {
          if (this.draft.rows.includes(row)) row.labeled += k;
        } else if (added.result === "printing") {
          this.showError("標籤列印中，請稍候再加入列印清單。");
        } else if (added.result === "cancelled") {
          this.showError("加入已取消，請重新加入列印清單。");
        } else if (added.result === "blocked") {
          this.showError("此條碼在列印清單中已失效，請先至標籤列印頁移除該列。");
        } else {
          this.showError("此條碼已不屬於任何可列印的子產品。");
        }
      } catch (e) { this.showError(e.message); }
      finally { row.labelBusy = false; this.$refs.scan?.focus(); }
    },
    removeRow(row) {
      if (this.draft.locked) return;
      const index = this.draft.rows.indexOf(row);
      if (index >= 0) this.draft.rows.splice(index, 1);
    },
    async clearAll() {
      if (this.draft.locked || !this.draft.rows.length) return;
      this.draft.locked = true;
      this.draft.generation += 1;
      const n = this.draft.rows.length;
      try {
        if (!await PosConfirm.ask(`確定清空進貨清單？共 ${n} 項。`,
                                  { title: "清空進貨清單", danger: true })) return;
        this.draft.rows = [];
        this.draft.notFound = "";
        this.draft.highlightKey = null;
      } catch (e) { this.showError(e.message); }
      finally { this.draft.locked = false; this.$refs.scan?.focus(); }
    },
    async confirmReceive() {
      if (this.draft.locked || !this.draft.rows.length) return;
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      if (this.draft.rows.some(row => !Number.isInteger(row.qty) || row.qty <= 0)) {
        this.showError("進貨數量必須為正整數"); return;
      }
      this.draft.locked = true;
      this.draft.generation += 1;
      const snapshot = this.draft.rows.map(r => ({ variant_id: r.variant_id, qty: r.qty }));
      const employee = this.employee.id, n = snapshot.length;
      const m = snapshot.reduce((sum, r) => sum + r.qty, 0);
      try {
        if (!await PosConfirm.ask(`確認進貨？共 ${n} 項、${m} 件，將寫入庫存。`,
                                  { title: "確認進貨" })) return;
        if (this.employee.id !== employee) { this.showError("請先選擇員工代號"); return; }
        if (this.draft.rows.length !== snapshot.length || this.draft.rows.some((r, i) =>
          r.variant_id !== snapshot[i].variant_id || r.qty !== snapshot[i].qty)) {
          this.showError("進貨清單已變動，請重新確認進貨。"); return;
        }
        await API.receiveStock({ employee, items: snapshot });
        const message = `已入庫 ${n} 項、共 ${m} 件。`;
        this.draft.doneMsg = message;
        setTimeout(() => { if (this.draft.doneMsg === message) this.draft.doneMsg = ""; }, 5000);
        this.draft.rows = [];
        this.draft.highlightKey = null;
      } catch (e) { this.showError(e.message); }
      finally { this.draft.locked = false; this.$refs.scan?.focus(); }
    },
    goSettings() { this.goPage("settings"); },
  },
};
