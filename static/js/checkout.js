window.PosPages = window.PosPages || {};
// 切頁會卸載收銀元件;購物車放元件外才撐得過切頁。只活在本次開啟程式期間,
// 不落地——關閉程式後沒結帳的單本來就不該留著。
window.PosCheckoutDraft = Vue.reactive({ cart: [], orderDiscount: 0, memberDiscount: 0, member: null, submitting: false, generation: 0 });
window.PosPages["page-checkout"] = {
  template: "#tpl-checkout",
  inject: ["showError"],
  data() {
    return { scanCode: "", searchQ: "", searchResults: [], draft: window.PosCheckoutDraft,
             payments: [], payment: "現金", paid: null, memberQuery: "", memberEdit: null, doneMsg: "", recent: [], recentOpen: false,
             employee: window.PosEmployee };
  },
  computed: {
    cartQty() { return this.draft.cart.reduce((s, i) => s + (Number(i.qty) || 0), 0); },
    cartDiscount() { return this.draft.cart.reduce((s, i) => s + i.discount, 0); },
    cartSubtotal() { return this.draft.cart.reduce((s, i) => s + i.qty * i.unit_price - i.discount, 0); },
    total() {
      const t = this.cartSubtotal - this.draft.orderDiscount - this.draft.memberDiscount;
      return Math.max(0, t);
    },
    // 目前只記錄賣了什麼、不實際收款:實收留白視為剛好付清
    paidBlank() { return this.paid === null || this.paid === ""; },
    paidAmount() { return this.paidBlank ? this.total : this.paid; },
  },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.listPayments();
      this.payment = this.payments[0];
      this.recent = await API.recentSales();
    });
    this.$refs.scan?.focus();
    // 對話框開著時不搶焦點:否則點「儲存」「查詢會員」後重開的選擇框會被拉走,Enter／掃描打進背後
    this._refocus = () => setTimeout(() => {
      if (document.querySelector(".modal-overlay")) return;
      if (document.activeElement.tagName !== "INPUT" &&
          document.activeElement.tagName !== "SELECT") this.$refs.scan?.focus();
    }, 300);
    document.addEventListener("click", this._refocus);
  },
  unmounted() { document.removeEventListener("click", this._refocus); },
  methods: {
    itemsText(s) { return s.items.map(i => `${i.name}×${i.qty}`).join("、"); },
    async addItem(r, gen = this.draft.generation) {
      if (this.draft.submitting) {
        this.showError("結帳處理中，請稍候。");
        this.scanCode = "";
        this.$refs?.scan?.focus();
        return;
      }
      if (this.draft.generation !== gen) {
        this.showError("結帳期間購物車已變動，請重新加入。");
        return;
      }
      let price = r.price;
      if (price === null) {
        const s = await PosConfirm.input({
          title: "未定價商品", message: `「${r.name}」尚未定價，請輸入成交單價：`,
          inputType: "number",
          validate: value => /^\d+$/.test(value) ? null : "價格必須是非負整數",
        });
        if (this.draft.submitting || this.draft.generation !== gen) {
          this.showError("結帳期間購物車已變動，請重新加入。");
          return;
        }
        if (s === null) return;
        price = Number(s);
      }
      const dup = this.draft.cart.find(i => i.variant_id === r.variant_id);
      if (dup) dup.qty += 1;
      else this.draft.cart.push({ variant_id: r.variant_id, name: r.name,
        attributes: r.attributes, attr_display: r.attr_display, models: r.models,
        label_barcode: r.label_barcode,
        unit_price: price, qty: 1, discount: 0, stock: r.stock });
      this.searchResults = [];
    },
    // 只留數字;清空視為 0,並把濾掉後的結果寫回輸入框
    setDiscount(key, event) {
      const digits = event.target.value.replace(/\D/g, "");
      this.draft[key] = digits === "" ? 0 : Number(digits);
      event.target.value = String(this.draft[key]);
    },
    // 實收留白有意義(視為剛好付清),清空時維持空白不補 0
    setPaid(event) {
      const digits = event.target.value.replace(/\D/g, "");
      this.paid = digits === "" ? null : Number(digits);
      event.target.value = digits === "" ? "" : String(this.paid);
    },
    setItemAmount(item, key, event) {
      const digits = event.target.value.replace(/\D/g, "");
      item[key] = digits === "" ? 0 : Number(digits);
      event.target.value = String(item[key]);
    },
    // 數量打字途中可暫時留白,離開欄位時由 @change 補回至少 1
    setItemQty(item, event) {
      const digits = event.target.value.replace(/\D/g, "");
      item.qty = digits === "" ? "" : Number(digits);
      event.target.value = digits === "" ? "" : String(item.qty);
    },
    toggleRecent() {
      this.recentOpen = !this.recentOpen;
      this.$refs?.scan?.focus();  // 焦點留在掃描框,收合後條碼槍可以直接掃
    },
    removeItem(i) {
      if (this.draft.submitting) return;
      this.draft.cart.splice(i, 1);
    },
    async onScan() {
      if (this.draft.submitting) {
        this.showError("結帳處理中，請稍候。");
        this.scanCode = "";
        this.$refs?.scan?.focus();
        return;
      }
      const gen = this.draft.generation;
      await this.guard(async () => {  // 查無條碼:保留輸入
        const query = await window.PosScan(this.scanCode, { sellableOnly: true });
        // 只看鎖定擋不住結帳完成後才回來的舊掃描，批次編號也必須相同。
        if (this.draft.submitting || this.draft.generation !== gen) {
          this.showError("結帳期間購物車已變動，請重新加入。");
          this.scanCode = "";
          return;
        }
        if (!query) { this.scanCode = ""; return; }
        await this.addItem(query.data, gen);
        this.scanCode = "";
      });
    },
    async onSearch() {
      if (!this.searchQ.trim()) return;
      this.searchResults = await API.listProducts({q: this.searchQ.trim()});
    },
    setMemberQuery(event) {
      // 輸入法全形模式打的數字先轉半形,否則會被當成非數字刪掉
      this.memberQuery = event.target.value
        .replace(/[０-９]/g, d => String.fromCharCode(d.charCodeAt(0) - 0xFEE0)).replace(/\D/g, "");
      event.target.value = this.memberQuery;
    },
    // 查到幾筆都要店員點選:當面跟客人確認是本人,不自動帶入
    async lookupMember() {
      const query = this.memberQuery;
      if (query.length !== 3 && query.length !== 10) { this.showError("請輸入電話末三碼或完整 10 碼"); return; }
      await this.guard(async () => {
        const matches = await API.lookupMembers(query);
        if (!matches.length) { this.showError("查無會員，請照一般客人結帳；新會員請事後至紀錄查詢新增"); return; }
        const index = await PosConfirm.choose({ title: "請選擇會員", message: "請與客人確認會員身分：",
          detailLarge: true, items: matches.map(m => ({ label: m.name, detail: m.phone, actionLabel: "修改" })) });
        if (index === null) return;
        // 修改:先關選擇框再開編輯框(不疊兩層);關閉後重查,讓店員再選一次
        if (typeof index === "object") {
          const m = matches[index.action];
          this.memberEdit = { member_id: m.member_id, name: m.name, phones: [...m.phones] };
          return;
        }
        const { phones, ...member } = matches[index];
        this.draft.member = member;
        this.memberQuery = "";
      });
    },
    async memberEditClosed() {
      this.memberEdit = null;
      await this.lookupMember();
    },
    clearMember() { this.draft.member = null; },
    async checkout() {
      if (this.draft.submitting) return;
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      this.draft.submitting = true;
      this.draft.generation += 1;
      const snapshot = this.draft.cart.map(i => ({ variant_id: i.variant_id, qty: i.qty,
        unit_price: i.unit_price, discount: i.discount }));
      try {
        await this.guard(async () => {
          const r = await API.checkout({
            payment: this.payment, order_discount: this.draft.orderDiscount, member_discount: this.draft.memberDiscount,
            paid: this.paidAmount, employee: this.employee.id, member_id: this.draft.member ? this.draft.member.member_id : null,
            items: snapshot });
          this.doneMsg = this.paidBlank
            ? `結帳完成(交易編號 ${r.sale_id})`
            : `結帳完成，找零 ${r.change} 元(交易編號 ${r.sale_id})`;
          this.draft.cart = []; this.draft.orderDiscount = 0; this.draft.memberDiscount = 0; this.draft.member = null; this.paid = null;
          setTimeout(() => this.doneMsg = "", 5000);
          this.recent = await API.recentSales();
        });
      } finally {
        this.draft.submitting = false;
      }
    },
  },
};
