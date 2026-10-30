// 補售價子視窗:設定頁「補售價」開此視窗,列出啟用中子產品逐筆或批次填售價。
// 選列比照桌面程式:單擊單選、Ctrl 加選／取消、Shift 從上次點的列連選;
// 修改先留在畫面(售價框藍色粗字),按「儲存」才整批寫回(後端同一交易,任一筆不合格整批不寫)。
function parsePrice(text) {
  const value = String(text ?? "").trim().replace(/,/g, "");
  if (!/^\d+$/.test(value)) return null;
  const price = Number(value);
  return price > 0 ? price : null;
}

window.PriceFillApp = {
  data() {
    return {
      loading: true,
      saving: false,
      committed: false,
      error: "",
      rows: [],
      changes: {},      // variant_id → 新售價
      selected: {},     // variant_id → true
      anchorId: null,   // Shift 連選的起點
      category: "",
      brand: "",
      keyword: "",
      onlyEmpty: true,
      batchPrice: "",
    };
  },
  async mounted() {
    this._keydown = (event) => this.handleKeydown(event);
    document.addEventListener("keydown", this._keydown);
    await this.load();
    this.$nextTick(() => this.$refs.keyword && this.$refs.keyword.focus());
  },
  unmounted() {
    document.removeEventListener("keydown", this._keydown);
  },
  computed: {
    categories() {
      return [...new Set(this.rows.map(r => r.category).filter(Boolean))].sort();
    },
    brands() {
      return [...new Set(this.rows.filter(r => !this.category || r.category === this.category)
        .map(r => r.brand).filter(Boolean))].sort();
    },
    visibleRows() {
      const parts = this.keyword.trim().toLowerCase().split(/\s+/).filter(Boolean);
      return this.rows.filter(row => {
        if (this.category && row.category !== this.category) return false;
        if (this.brand && row.brand !== this.brand) return false;
        // 只看未定價時,本輪已填的仍保留顯示,方便核對
        if (this.onlyEmpty && row.price && !(row.variant_id in this.changes)) return false;
        if (!parts.length) return true;
        const text = [row.name, row.spec, row.models, row.barcode].join(" ").toLowerCase();
        return parts.every(part => text.includes(part));
      });
    },
    selectedCount() { return this.visibleRows.filter(r => this.selected[r.variant_id]).length; },
    changeCount() { return Object.keys(this.changes).length; },
  },
  methods: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        this.rows = await API.invoke("variants.price_list", {});
        if (this.category && !this.categories.includes(this.category)) this.category = "";
        if (this.brand && !this.brands.includes(this.brand)) this.brand = "";
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    onCategoryChange() {
      if (this.brand && !this.brands.includes(this.brand)) this.brand = "";
    },
    priceText(row) {
      const price = row.variant_id in this.changes ? this.changes[row.variant_id] : row.price;
      return price == null ? "" : String(price);
    },
    setPrice(row, price) {
      const next = { ...this.changes };
      if (price == null || price === row.price) delete next[row.variant_id];
      else next[row.variant_id] = price;
      this.changes = next;
    },
    setPriceFromInput(row, event) {
      const price = parsePrice(event.target.value);
      this.setPrice(row, price);
      // 不合格或還原時輸入框顯示回目前值
      event.target.value = this.priceText(row);
    },
    moveNext(row, index, event) {
      this.setPriceFromInput(row, event);
      const next = document.querySelector(`.pf-price[data-index="${index + 1}"]`);
      if (!next) return;
      const nextRow = this.visibleRows[index + 1];
      this.selected = { [nextRow.variant_id]: true };
      this.anchorId = nextRow.variant_id;
      next.focus();
      next.select();
    },
    onRowMousedown(event) {
      // Shift 連選時不要讓瀏覽器反白一大段文字
      if (event.shiftKey) event.preventDefault();
    },
    onRowClick(event, row, index) {
      const id = row.variant_id;
      if (event.shiftKey && this.anchorId != null) {
        const from = this.visibleRows.findIndex(r => r.variant_id === this.anchorId);
        const start = from < 0 ? index : Math.min(from, index);
        const end = from < 0 ? index : Math.max(from, index);
        const next = event.ctrlKey ? { ...this.selected } : {};
        for (const r of this.visibleRows.slice(start, end + 1)) next[r.variant_id] = true;
        this.selected = next;
        return;
      }
      if (event.ctrlKey) {
        const next = { ...this.selected };
        if (next[id]) delete next[id]; else next[id] = true;
        this.selected = next;
      } else {
        this.selected = { [id]: true };
      }
      this.anchorId = id;
    },
    async fillSelected() {
      const targets = this.visibleRows.filter(r => this.selected[r.variant_id]);
      if (!targets.length) {
        await PosConfirm.notify("請先在表格中點選要填價的子產品（可按住 Ctrl 或 Shift 多選）。");
        return;
      }
      const price = parsePrice(this.batchPrice);
      if (price == null) {
        await PosConfirm.notify("請先在「售價」欄輸入大於 0 的整數。");
        return;
      }
      for (const row of targets) this.setPrice(row, price);
    },
    async save() {
      if (this.saving || this.loading || !this.changeCount) return;
      if (!await PosConfirm.ask(`確定將 ${this.changeCount} 筆售價寫回資料庫？`)) return;
      this.saving = true;
      this.error = "";
      try {
        const changes = Object.entries(this.changes)
          .map(([vid, price]) => ({ variant_id: Number(vid), price }));
        const result = await API.invoke("variants.price_save", { changes });
        this.changes = {};
        this.committed = true;
        await this.load();
        await PosConfirm.notify(`已寫入 ${result.saved} 筆售價。`);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.saving = false;
      }
    },
    // 視窗 X 不經過 cancel,與其他子視窗相同直接放棄未儲存修改。
    async cancel() {
      if (this.saving) return;
      if (this.changeCount &&
          !await PosConfirm.ask(`尚有 ${this.changeCount} 筆售價未儲存，確定放棄並關閉？`)) return;
      await API.invoke("desktop.child_window.close", { saved: this.committed });
    },
    async handleKeydown(event) {
      if (event.key === "Escape" && !event.defaultPrevented) await this.cancel();
    },
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp(window.PriceFillApp);
  for (const [name, component] of Object.entries(window.PosComponents || {}))
    app.component(name, component);
  app.mount("#price-fill");
});
