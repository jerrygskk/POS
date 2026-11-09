window.PosPages = window.PosPages || {};
window.PosPages["page-returns"] = {
  template: "#tpl-returns",
  inject: ["showError"],
  data() {
    return { sessions: [], current: null, detail: null, scanCode: "",
             employee: window.PosEmployee,
             kindBusy: false,
             dispositions: [["restock", "加回庫存"], ["discard", "退貨不入庫"], ["reject", "不同意退貨"]] };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
    canEdit() { return this.detail?.status === "open"; },
    canReview() { return this.detail?.status === "pending" && this.isAdmin; },
  },
  watch: {
    isAdmin(isAdmin) {
      if (!isAdmin && this.current && this.detail?.status === "pending")
        this.guard(() => this.enter(this.current));
    },
  },
  async mounted() { await this.guard(() => this.reload()); },
  methods: {
    async reload() { this.sessions = await API.listReturns(); },
    // 等待期間若已換到別張單，舊單的明細不得蓋掉新畫面
    async refreshDetail() {
      const rid = this.current;
      const detail = await API.returnDetail(rid);
      if (this.current === rid) this.detail = detail;
    },
    async openNew() {
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      await this.guard(async () => {
        const r = await API.createReturn({ employee: this.employee.id });
        await this.enter(r.return_id);
      });
    },
    async enter(rid) {
      // 資料載入完成才切入詳細畫面，避免 await 期間渲染到空的 detail。
      const detail = await API.returnDetail(rid);
      this.detail = detail;
      this.current = rid;
      this.$nextTick(() => this.$refs.scan?.focus());
    },
    // 寫入失敗一律重新載入，還原畫面上未儲存的輸入值
    async write(fn) {
      try { await fn(); }
      catch (e) { this.showError(e.message); }
      await this.guard(() => this.refreshDetail());
    },
    // 查詢與寫入都要等待，期間可能換到別張單：寫入用開始時的單號，回來後換單就不動新畫面
    async onScan() {
      if (!this.canEdit) return;
      const rid = this.current;
      await this.guard(async () => {
        const query = await window.PosScan(this.scanCode);
        if (this.current !== rid) return;
        if (!query) { this.scanCode = ""; return; }
        if (!this.canEdit) return;
        await API.returnScan(rid, query.data.variant_id, 1);
        if (this.current !== rid) return;
        await this.refreshDetail();
        if (this.current !== rid) return;
        this.scanCode = "";
      });
    },
    async setQty(it) {
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法修改數量"); return; }
      await this.write(() => API.setReturnQty(this.current, it.id, it.qty));
    },
    async setSale(it) {
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法修改銷售單編號"); return; }
      const text = String(it.sale_id ?? "").trim();
      if (text && !/^[0-9]+$/.test(text)) { this.showError("銷售單編號必須是數字"); await this.guard(() => this.refreshDetail()); return; }
      await this.write(() => API.setReturnSale(this.current, it.id, text ? Number(text) : null));
    },
    async setReason(it) {
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法修改原因"); return; }
      await this.write(() => API.setReturnReason(this.current, it.id, it.reason || null));
    },
    async removeItem(it) {
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法移除品項"); return; }
      await this.write(() => API.removeReturnItem(this.current, it.id));
    },
    // 返修是整張單的模式:切換一次改全部品項;切換中不可重按,成敗都重讀單據
    async setKind(kind) {
      if (!this.canReview) { this.showError("需要管理者權限"); await this.guard(() => this.refreshDetail()); return; }
      if (this.kindBusy || this.detail.kind === kind) return;
      const rid = this.current;
      this.kindBusy = true;
      try {
        await this.write(() => API.setReturnKind(rid, kind));
        await this.guard(() => window.PosAuthOps.refresh());
      } finally { this.kindBusy = false; }
    },
    async setDisposition(it) {
      if (!this.canReview) { this.showError("需要管理者權限"); await this.guard(() => this.refreshDetail()); return; }
      await this.write(() => API.setReturnDisposition(this.current, it.id, it.disposition));
    },
    async close() {
      if (!this.canEdit) return;
      const rid = this.current;
      if (!await PosConfirm.ask(`共 ${this.detail.items.length} 項。送出後交由管理者決定處理方式，核准前不會調整庫存。確定送出?`)) return;
      if (this.current !== rid || !this.canEdit) return;
      await this.guard(async () => {
        await API.closeReturn(rid);
        this.current = null; this.detail = null;
        await this.reload();
        await window.PosAuthOps.refresh();
        await PosConfirm.notify("已送交管理者核准。");
      });
    },
    async withdraw(rid) {
      if (!await PosConfirm.ask("抽回後退貨單回到進行中，可修改後再次送出。確定抽回?")) return;
      try { await API.withdrawReturn(rid); } catch (e) { this.showError(e.message); }
      await this.guard(() => this.reload());
      await this.guard(() => window.PosAuthOps.refresh());
    },
    async cancel(s) {
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      if (s.status === "pending" && !this.isAdmin) { this.showError("需要管理者權限"); return; }
      if (!await PosConfirm.ask(`作廢退貨單 ${s.return_id}?作廢後不會調整庫存，紀錄保留於紀錄查詢。`)) return;
      try { await API.cancelReturn(s.return_id, this.employee.id); } catch (e) { this.showError(e.message); }
      await this.guard(() => this.reload());
      await this.guard(() => window.PosAuthOps.refresh());
    },
    async approve() {
      if (!this.canReview) { this.showError("需要管理者權限"); return; }
      const rid = this.current;
      const count = key => this.detail.items.filter(i => i.disposition === key).length;
      const message = this.detail.kind === "repair"
        ? `返修不入庫 ${this.detail.items.length} 項。核准後此單轉為返修中，等待返新進貨或結案，不會調整庫存。確定核准?`
        : `加回庫存 ${count("restock")} 項、退貨不入庫 ${count("discard")} 項、不同意 ${count("reject")} 項。核准後依此調整庫存。確定核准?`;
      if (!await PosConfirm.ask(message)) return;
      // 確認期間換到別張單就不動;仍在原單才檢查權限
      if (this.current !== rid) return;
      if (!this.canReview) { this.showError("需要管理者權限"); return; }
      // 失敗留在原單並重新載入,成功才清空回清單
      let ok = false;
      await this.write(async () => { await API.approveReturn(rid); ok = true; });
      if (!ok) return;
      this.current = null; this.detail = null;
      await this.guard(() => this.reload());
      await this.guard(() => window.PosAuthOps.refresh());
    },
  },
};
