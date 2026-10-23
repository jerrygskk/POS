window.PosPages = window.PosPages || {};
window.PosPages["page-stocktake"] = {
  template: "#tpl-stocktake",
  inject: ["showError"],
  data() {
    return { sessions: [], current: null, detail: null, scanCode: "", marked: [],
             employee: window.PosEmployee };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
    // 待核准不開放改數量:有誤由管理者退回重盤
    canEditCounted() { return this.detail?.status === "open"; },
    canReview() { return this.detail?.status === "pending" && this.isAdmin; },
    recountCount() { return (this.detail?.items || []).filter(it => it.needs_recount).length; },
  },
  watch: {
    isAdmin(isAdmin) {
      if (!isAdmin && this.current && this.detail?.status === "pending")
        this.guard(() => this.enter(this.current));
    },
  },
  async mounted() { await this.guard(() => this.reload()); },
  methods: {
    async reload() { this.sessions = await API.listStocktakes(); },
    async openNew() {
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      await this.guard(async () => {
        const r = await API.createStocktake({ employee: this.employee.id });
        await this.enter(r.session_id);
      });
    },
    async enter(sid) {
      // 資料載入完成才切入詳細畫面，避免 await 期間渲染到空的 detail。
      const detail = await API.stocktakeDetail(sid);
      this.detail = detail;
      this.current = sid;
      this.marked = [];
      this.$nextTick(() => this.$refs.scan?.focus());
    },
    async onScan() {
      if (this.detail?.status !== "open") return;
      await this.guard(async () => {
        const query = await window.PosScan(this.scanCode);
        if (!query) { this.scanCode = ""; return; }
        if (this.detail?.status !== "open") return;
        const hit = query.data;
        await API.stocktakeScan({session_id: this.current, variant_id: hit.variant_id, qty: 1});
        this.detail = await API.stocktakeDetail(this.current);
        this.scanCode = "";
      });
    },
    async setCounted(it) {
      if (!this.canEditCounted) {
        this.showError(this.detail?.status === "pending" ? "盤點單待核准中，無法修改數量" : "盤點單已結案");
        await this.guard(() => this.enter(this.current));
        return;
      }
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
    async removeItem(it) {
      if (!this.canEditCounted) { this.showError("盤點單不是進行中，無法移除品項"); return; }
      await this.guard(async () => {
        await API.removeStocktakeItem(this.current, it.variant_id);
        this.detail = await API.stocktakeDetail(this.current);
      });
    },
    async close() {
      if (this.detail?.status !== "open") return;
      const diffs = this.detail.items.filter(i => i.diff !== 0);
      const sid = this.current;
      if (!await PosConfirm.ask(`共 ${diffs.length} 項有差異。送出後交由管理者核准，核准前不會調整庫存。確定送出?`)) return;
      if (this.current !== sid || this.detail?.status !== "open") return;
      await this.guard(async () => {
        await API.closeStocktake(sid);
        this.current = null; this.detail = null;
        await this.reload();
        await window.PosAuthOps.refresh();
        await PosConfirm.notify("已送交管理者核准。");
      });
    },
    async withdraw(sid) {
      if (!await PosConfirm.ask("抽回後盤點單回到進行中，系統量會更新為目前庫存，可修改後再次送出。確定抽回?")) return;
      try {
        await API.withdrawStocktake(sid);
        await this.reload();
        await window.PosAuthOps.refresh();
      } catch (e) {
        this.showError(e.message);
        await this.guard(() => this.reload());
      }
    },
    toggleMark(it) {
      if (!this.canReview) { this.showError("需要管理者權限"); return; }
      const i = this.marked.indexOf(it.variant_id);
      if (i >= 0) this.marked.splice(i, 1); else this.marked.push(it.variant_id);
    },
    async returnItems() {
      if (!this.canReview) { this.showError("需要管理者權限"); return; }
      if (!this.marked.length) return;
      const sid = this.current, ids = this.marked.slice();
      if (!await PosConfirm.ask(`將 ${ids.length} 項退回重新清點，盤點單回到進行中。確定退回?`)) return;
      if (!this.canReview || this.current !== sid) { this.showError("需要管理者權限"); return; }
      try {
        await API.returnStocktakeItems(sid, ids);
        this.current = null; this.detail = null;
        await this.reload();
        await window.PosAuthOps.refresh();
      } catch (e) {
        this.showError(e.message);
        await this.guard(() => this.enter(sid));
      }
    },
    async approve() {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      if (this.detail?.status !== "pending" || this.marked.length) return;
      const sid = this.current;
      const diffs = this.detail.items.filter(i => i.diff !== 0);
      if (!await PosConfirm.ask(`共 ${diffs.length} 項有差異，核准後將依實盤量調整庫存。確定核准?`)) return;
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      if (this.current !== sid || this.detail?.status !== "pending") return;
      try {
        await API.approveStocktake(sid);
        this.current = null; this.detail = null;
        await this.reload();
        await window.PosAuthOps.refresh();
      } catch (e) {
        this.showError(e.message);
        this.current = null; this.detail = null;
        await this.guard(() => this.reload());
      }
    },
  },
};
