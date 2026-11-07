window.PosPages = window.PosPages || {};
// 用本機日期;toISOString 是 UTC,台灣早上 8 點前會變成昨天
function localDateText(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
// 收銀預設當天,其他類別預設含今天的最近 7 天
function defaultRange(kind) {
  const now = new Date();
  const from = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (kind === "sales" ? 0 : 6));
  return { dateFrom: localDateText(from), dateTo: localDateText(now) };
}
window.PosPages["page-records"] = {
  template: "#tpl-records",
  inject: ["showError"],
  data() {
    return { kinds: [["sales", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"], ["returns", "退貨"], ["members", "資料設定"]], kind: "sales",
             ...defaultRange("sales"), payment: "", payments: [],
             sales: [], summary: null, receipts: [], stocktakes: [], returns: [], expanded: new Set(),
             memberPhone: "", memberQuery: "", members: [], memberPopup: null };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
    // 目前類別每一列的開合鍵,與範本的 isOpen／toggle 共用
    rowKeys() {
      if (this.kind === "sales") return this.sales.map(s => s.sale_id);
      if (this.kind === "receive") return this.receipts.map((_, i) => i);
      if (this.kind === "returns") return this.returns.map(t => t.return_id);
      if (this.kind === "members") return [];
      return this.stocktakes.map(t => t.session_id);
    },
  },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.listPayments();
      await this.reload();
    });
  },
  methods: {
    async selectKind(kind) {
      if (this.kind === kind) return;
      this.kind = kind;
      Object.assign(this, defaultRange(kind));
      await this.reload();
    },
    isOpen(key) { return this.expanded.has(key); },
    toggle(key) {
      const next = new Set(this.expanded);
      if (!next.delete(key)) next.add(key);
      this.expanded = next;
    },
    expandAll() { this.expanded = new Set(this.rowKeys); },
    collapseAll() { this.expanded = new Set(); },
    // 主列時間只顯示到分;資料本身不變
    minuteText(ts) { return String(ts || "").slice(0, 16); },
    // 折抵金額帶減號,一眼看出是扣掉的錢;0 顯示「—」
    amountText(value) { return value ? "−" + value : "—"; },
    salePrice(it) { return it.unit_price - Math.round(it.discount / it.qty); },
    // 明細底下的算式:商品小計 − 各項折抵 = 應收;沒有任何折抵只寫應收
    formulaText(s) {
      if (!s.order_discount && !s.member_discount) return `應收 ${s.total}`;
      const sub = s.items.reduce((sum, it) => sum + it.qty * it.unit_price - it.discount, 0);
      let text = `商品小計 ${sub}`;
      if (s.order_discount) text += ` − 整單折抵 ${s.order_discount}`;
      if (s.member_discount) text += ` − 會員折扣 ${s.member_discount}`;
      return `${text} = 應收 ${s.total}`;
    },
    specText(row) { return this.attrText(row).replaceAll("｜", "・"); },
    async reload() {
      // 查詢後資料可能換了;進貨舊紀錄只能用列序當鍵,保留展開會開到別筆
      this.collapseAll();
      await this.guard(async () => {
        const range = {date_from: this.dateFrom, date_to: this.dateTo};
        if (this.kind === "sales") {
          const filters = {...range, payment: this.payment || "", member_phone: this.memberPhone.trim()};
          this.sales = await API.listSales(filters);
          this.summary = await API.salesSummary(filters);
        } else if (this.kind === "members") {
          this.members = await API.listMembers(this.memberQuery.trim());
        } else if (this.kind === "receive") {
          this.receipts = await API.listReceipts(range);
        } else if (this.kind === "returns") {
          this.returns = await API.returnHistory(range);
        } else {
          this.stocktakes = await API.stocktakeHistory(range);
        }
      });
    },
    phonesText(m) { return m.phones.join("、"); },
    newMember() { this.memberPopup = { member_id: null, name: "", phones: [""] }; },
    editMember(m) { this.memberPopup = { member_id: m.member_id, name: m.name, phones: [...m.phones] }; },
    async memberEditorClosed(saved) {
      this.memberPopup = null;
      if (saved) await this.guard(async () => { this.members = await API.listMembers(this.memberQuery.trim()); });
    },
    async removeMember(m) {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      if (!await PosConfirm.ask(`確定刪除會員「${m.name}」？舊的銷售紀錄仍會顯示此會員。`, { danger: true })) return;
      // 確認框期間可能已登出:送出前再檢查一次
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      await this.guard(async () => {
        await API.deleteMember(m.member_id);
        this.members = await API.listMembers(this.memberQuery.trim());
      });
    },
    async exportCsv() {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      await this.guard(async () => {
        await API.exportSales({ date_from: this.dateFrom, date_to: this.dateTo,
          payment: this.payment || "", member_phone: this.memberPhone.trim() });
      });
    },
  },
};
