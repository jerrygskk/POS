window.PosPages = window.PosPages || {};
window.PosPages["page-returns"] = {
  template: "#tpl-returns",
  inject: ["showError"],
  data() {
    return { sessions: [], current: null, detail: null, scanCode: "",
             employee: window.PosEmployee,
             kindBusy: false, busy: false, loadSeq: 0, saleInput: "", memberQuery: "", memberEdit: null, refundEdits: {},
             dispositions: [["restock", "加回庫存"], ["discard", "退貨不入庫"], ["reject", "不同意退貨"]] };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
    canEdit() { return this.detail?.status === "open"; },
    canReview() { return this.detail?.status === "pending" && this.isAdmin; },
    refundTotal() { return (this.detail?.items || []).reduce((sum,i) => sum + (i.disposition === "reject" ? 0 : (this.canEdit ? i.refund_request : i.refund_approved) || 0), 0); },
    saleRows() {
      return (this.detail?.sale_lines || []).map(line => {
        const item = this.detail.items.find(i => i.sale_item_id === line.item_id);
        return {...line, ...(item || {}), picked: !!item, original_item_id: line.item_id,
                qty: item?.qty ?? line.returnable_qty, refund_request: item?.refund_request ?? null};
      });
    },
    shownItems() { return this.detail?.sale_id && this.canEdit ? this.saleRows : this.detail?.items || []; },
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
      const seq = ++this.loadSeq;
      const detail = await API.returnDetail(rid);
      if (this.current === rid && seq === this.loadSeq) this.detail = detail;
    },
    async openNew() {
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      if (this.busy) return;
      this.busy = true;
      try {
      await this.guard(async () => {
        const choice = await PosConfirm.choose({title:"退貨種類",message:"請選擇本張退貨單的種類",items:[{label:"一般退貨",detail:"現金退款"},{label:"返修",detail:"不退款，等待返新或結案"}]});
        if (choice === null || choice === undefined) return;
        const r = await API.createReturn({ employee: this.employee.id, kind: choice === 0 ? "normal" : "repair" });
        await this.enter(r.return_id);
      });
      } finally { this.busy = false; }
    },
    async enter(rid) {
      const seq = ++this.loadSeq;
      // 資料載入完成才切入詳細畫面，避免 await 期間渲染到空的 detail。
      const detail = await API.returnDetail(rid);
      if (seq !== this.loadSeq) return;
      this.detail = detail;
      this.current = rid;
      this.refundEdits = {};
      this.saleInput = detail.sale_id ? String(detail.sale_id) : "";
      this.memberQuery = "";
      this.$nextTick(() => this.$refs.scan?.focus());
    },
    // 寫入失敗一律重新載入，還原畫面上未儲存的輸入值
    async write(fn) {
      if (this.busy) return;
      const rid = this.current;
      this.busy = true;
      let ok = false;
      try { await fn(); ok = true; }
      catch (e) { this.showError(e.message); }
      finally {
        if (this.current === rid) await this.guard(() => this.refreshDetail());
        this.busy = false;
      }
      return ok;
    },
    // 查詢與寫入都要等待，期間可能換到別張單：寫入用開始時的單號，回來後換單就不動新畫面
    // 先取出條碼並清空輸入框，再排隊依序處理：處理完才清空會把掃描槍接著打入的下一筆黏在一起
    onScan() {
      if (!this.canEdit || this.detail.sale_id || this.busy || this.kindBusy) return;
      const rid = this.current, code = this.scanCode;
      this.scanCode = "";
      if (!String(code || "").trim()) return;
      this._scanChain = (this._scanChain || Promise.resolve()).then(() => this.scanOne(rid, code));
      return this._scanChain;
    },
    async scanOne(rid, code) {
      if (this.current !== rid) return;
      try {
        const query = await window.PosScan(code);
        if (this.current !== rid || !query || !this.canEdit) return;
        await API.returnScan(rid, query.data.variant_id, 1);
        if (this.current !== rid) return;
        await this.refreshDetail();
      } catch (e) {
        // 失敗的條碼只顯示在訊息:放回輸入框會和掃描槍的下一筆黏在一起
        this.showError(`條碼 ${code}：${e.message}`);
      }
    },
    digits(event) { event.target.value = event.target.value.replace(/\D/g, ""); },
    refundValue(it) { return this.refundEdits[it.id] ?? (this.canEdit ? it.refund_request : it.refund_approved) ?? ""; },
    refundInvalid(it) {
      if (it.disposition === "reject") return false;
      const text = String(this.refundValue(it));
      const limit = this.canReview ? it.refund_request : it.refund_limit;
      return text !== "" && (limit !== null && Number(text) > limit);
    },
    editRefund(it,event) { this.digits(event); this.refundEdits[it.id] = event.target.value; },
    async setQty(it, event) {
      if (this.busy || this.kindBusy) return;
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法修改數量"); return; }
      const rid = this.current, qty = event ? Number(event.target.value) : it.qty;
      await this.write(() => API.setReturnQty(rid, it.id, qty));
    },
    async setRefund(it, event) {
      if (this.busy || this.kindBusy) return;
      if (!this.canEdit && !this.canReview) { this.showError("需要管理者權限"); return; }
      if (this.detail.kind !== "normal") return;
      const text = event.target.value.trim();
      if (!/^\d+$/.test(text)) { this.showError("請填寫退貨價格（整列金額）"); await this.guard(() => this.refreshDetail()); return; }
      const limit = this.canReview ? it.refund_request : it.refund_limit;
      if (limit !== null && limit !== undefined && Number(text) > limit) { this.refundEdits[it.id] = text; this.showError(`退貨價格超過上限 ${limit} 元`); return; }
      const rid = this.current;
      if (await this.write(() => API.setReturnRefund(rid, it.id, Number(text)))) delete this.refundEdits[it.id];
    },
    async loadSale() {
      if (!this.canEdit || this.busy || this.detail.sale_id) return;
      if (!/^\d+$/.test(this.saleInput)) { this.showError("銷售單號必須是數字"); return; }
      const rid = this.current, sid = Number(this.saleInput);
      await this.write(() => API.loadReturnSale(rid, sid));
    },
    async clearSale() {
      if (!this.canEdit || this.busy) return;
      const rid = this.current;
      if (this.detail.items.length && !await PosConfirm.ask("清空原銷售單、品項與會員後重新填寫？")) return;
      if (this.current !== rid || !this.canEdit) return;
      if (await this.write(() => API.clearReturn(rid))) this.saleInput = "";
    },
    async pick(it, event) {
      if (!this.canEdit || this.busy || !this.detail.sale_id) return;
      const rid = this.current;
      if (event.target.checked) {
        if (!it.returnable_qty) return;
        await this.write(() => API.pickReturn(rid, it.original_item_id, it.returnable_qty));
      } else await this.write(() => API.removeReturnItem(rid, it.id));
    },
    async lookupMember() {
      if (!this.canEdit || this.detail.sale_id || this.busy) return;
      const rid = this.current, query = this.memberQuery;
      if (!/^(\d{3}|\d{10})$/.test(query)) { this.showError("請輸入電話末三碼或完整 10 碼"); return; }
      await this.guard(async () => {
        const matches = await API.lookupMembers(query);
        if (this.current !== rid || !this.canEdit || this.detail.sale_id) return;
        if (!matches.length) { this.showError("查無會員"); return; }
        const index = await PosConfirm.choose({title:"請選擇會員",message:"請與客人確認會員身分：",detailLarge:true,items:matches.map(m => ({label:m.name,detail:m.phones.join("、"),actionLabel:"修改"}))});
        if (this.current !== rid || !this.canEdit || this.detail.sale_id || index === null || index === undefined) return;
        if (typeof index === "object") { this.memberEdit = matches[index.action]; return; }
        await this.write(() => API.setReturnMember(rid, matches[index].member_id));
      });
    },
    async memberEditClosed() { this.memberEdit = null; await this.lookupMember(); },
    async clearMember() {
      if (!this.canEdit || this.detail.sale_id || this.busy) return;
      const rid = this.current;
      await this.write(() => API.setReturnMember(rid, null));
    },
    async setReason(it) {
      if (this.busy || this.kindBusy) return;
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法修改原因"); return; }
      await this.write(() => API.setReturnReason(this.current, it.id, it.reason || null));
    },
    async removeItem(it) {
      if (this.busy || this.kindBusy) return;
      if (!this.canEdit) { this.showError("退貨單不是進行中，無法移除品項"); return; }
      await this.write(() => API.removeReturnItem(this.current, it.id));
    },
    // 返修是整張單的模式:切換一次改全部品項;切換中不可重按,成敗都重讀單據
    async setKind(kind) {
      if (!this.canEdit) { this.showError("退貨單已送出，無法變更退貨種類"); await this.guard(() => this.refreshDetail()); return; }
      if (this.kindBusy || this.busy || this.detail.kind === kind) return;
      const rid = this.current;
      this.kindBusy = true;
      try {
        if (this.detail.items.some(i => i.refund_request !== null) && !await PosConfirm.ask("變更退貨種類時，已填寫的退貨價格將全部清除，需要重新填寫。")) return;
        if (this.current !== rid || !this.canEdit) return;
        await this.write(() => API.setReturnKind(rid, kind));
        this.refundEdits = {};
        await this.guard(() => window.PosAuthOps.refresh());
      } finally { this.kindBusy = false; }
    },
    async setDisposition(it) {
      if (this.busy || this.kindBusy) return;
      if (!this.canReview) { this.showError("需要管理者權限"); await this.guard(() => this.refreshDetail()); return; }
      await this.write(() => API.setReturnDisposition(this.current, it.id, it.disposition));
    },
    async close() {
      if (!this.canEdit || this.busy || this.kindBusy) return;
      const rid = this.current;
      if (this.detail.items.some(i => this.refundInvalid(i))) { this.showError("請修正超過上限的退貨價格"); return; }
      if (this.detail.kind === "normal" && this.detail.items.some(i => i.disposition !== "reject" && i.refund_request === null)) { this.showError("請填寫退貨價格"); return; }
      const message = this.detail.kind === "normal" ? `共 ${this.detail.items.length} 項，退款合計 ${this.refundTotal} 元。請確認已退現金給客人，送出後無法修改，由管理者核准。` : `共 ${this.detail.items.length} 項。返修不退款，送出後無法修改，由管理者核准。`;
      if (!await PosConfirm.ask(message)) return;
      if (this.current !== rid || !this.canEdit) return;
      if (this.busy) return;
      this.busy = true;
      try {
      await this.guard(async () => {
        await API.closeReturn(rid);
        this.current = null; this.detail = null;
        await this.reload();
        await window.PosAuthOps.refresh();
        await PosConfirm.notify("已送交管理者核准。");
      });
      } finally { this.busy = false; }
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
      if (this.kindBusy || this.busy) return;
      if (this.detail.items.some(i => this.refundInvalid(i))) { this.showError("請修正超過上限的退貨價格"); return; }
      const rid = this.current;
      // 確認框顯示的內容一併送後端核對,讀回前若已變更則後端拒絕
      const kind = this.detail.kind;
      const items = this.detail.items.map(i => ({item_id: i.id, qty: i.qty, disposition: i.disposition ?? null, refund: i.refund_approved ?? null}));
      const businessDate = this.detail.approval_business_date;
      const count = key => this.detail.items.filter(i => i.disposition === key).length;
      const message = this.detail.kind === "repair"
        ? `返修不入庫 ${this.detail.items.length} 項。核准後此單轉為返修中，等待返新進貨或結案，不會調整庫存。確定核准?`
        : `加回庫存 ${count("restock")} 項、退貨不入庫 ${count("discard")} 項、不同意 ${count("reject")} 項。實際退款 ${this.refundTotal} 元，計入 ${businessDate} 的帳。確定核准？`;
      if (!await PosConfirm.ask(message)) return;
      // 確認期間換到別張單就不動;仍在原單才檢查權限
      if (this.current !== rid) return;
      if (!this.canReview) { this.showError("需要管理者權限"); return; }
      // 失敗留在原單並重新載入,成功才清空回清單
      let ok = false;
      await this.write(async () => { await API.approveReturn(rid, kind, items, businessDate); ok = true; });
      if (!ok) return;
      this.current = null; this.detail = null;
      await this.guard(() => this.reload());
      await this.guard(() => window.PosAuthOps.refresh());
    },
  },
};
