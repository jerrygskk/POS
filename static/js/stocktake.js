window.PosPages = window.PosPages || {};
window.PosPages["page-stocktake"] = {
  template: "#tpl-stocktake",
  inject: ["showError"],
  data() {
    return { sessions: [], current: null, detail: null, scanCode: "", operator: "" };
  },
  async mounted() { await this.reload(); },
  methods: {
    async reload() { this.sessions = await API.listStocktakes(); },
    async openNew() {
      const r = await API.createStocktake({ operator: this.operator || null });
      await this.enter(r.session_id);
    },
    async enter(sid) {
      this.current = sid;
      this.detail = await API.stocktakeDetail(sid);
      this.$nextTick(() => this.$refs.scan?.focus());
    },
    async onScan() {
      await this.guard(async () => {
        const query = await API.barcodeQuery(this.scanCode);
        if (!query) return;
        const hit = query.data;
        await API.stocktakeScan({session_id: this.current, variant_id: hit.variant_id, qty: 1});
        this.detail = await API.stocktakeDetail(this.current);
        this.scanCode = "";
      });
    },
    async setCounted(it) {
      try {
        await API.setStocktakeCounted({session_id: this.current,
          variant_id: it.variant_id, counted_qty: it.counted_qty});
        this.detail = await API.stocktakeDetail(this.current);
      } catch (e) {
        this.showError(e.message);
        // 寫入失敗:重新載入以還原畫面上未儲存的輸入值
        this.detail = await API.stocktakeDetail(this.current);
      }
    },
    async close() {
      const diffs = this.detail.items.filter(i => i.diff !== 0);
      if (!confirm(`共 ${diffs.length} 項有差異,結案後將調整庫存。確定結案?`)) return;
      await API.closeStocktake(this.current);
      this.current = null; this.detail = null;
      await this.reload();
    },
  },
};
