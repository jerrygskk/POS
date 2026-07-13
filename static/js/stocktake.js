window.PosPages = window.PosPages || {};
window.PosPages["page-stocktake"] = {
  template: "#tpl-stocktake",
  inject: ["showError"],
  data() {
    return { sessions: [], current: null, detail: null, scanCode: "", operator: "" };
  },
  async mounted() { await this.reload(); },
  methods: {
    async reload() { this.sessions = await API.get("/api/stocktake"); },
    async openNew() {
      const r = await API.post("/api/stocktake", { operator: this.operator || null });
      await this.enter(r.session_id);
    },
    async enter(sid) {
      this.current = sid;
      this.detail = await API.get("/api/stocktake/" + sid);
      this.$nextTick(() => this.$refs.scan?.focus());
    },
    async onScan() {
      const code = this.scanCode.trim();
      if (!code) return;
      await this.guard(async () => {
        const hit = await API.get("/api/barcode/" + encodeURIComponent(code));
        await API.post(`/api/stocktake/${this.current}/scan`,
                       { variant_id: hit.variant_id });
        this.detail = await API.get("/api/stocktake/" + this.current);
        this.scanCode = "";
      });
    },
    async setCounted(it) {
      try {
        await API.put(`/api/stocktake/${this.current}/items/${it.variant_id}`,
                      { counted_qty: it.counted_qty });
        this.detail = await API.get("/api/stocktake/" + this.current);
      } catch (e) {
        this.showError(e.message);
        // 寫入失敗:重新載入以還原畫面上未儲存的輸入值
        this.detail = await API.get("/api/stocktake/" + this.current);
      }
    },
    async close() {
      const diffs = this.detail.items.filter(i => i.diff !== 0);
      if (!confirm(`共 ${diffs.length} 項有差異,結案後將調整庫存。確定結案?`)) return;
      await API.post(`/api/stocktake/${this.current}/close`);
      this.current = null; this.detail = null;
      await this.reload();
    },
  },
};
