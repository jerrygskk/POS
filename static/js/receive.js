window.PosPages = window.PosPages || {};
window.PosPages["page-receive"] = {
  template: "#tpl-receive",
  inject: ["showError", "goPage"],
  data() {
    return {
      scanCode: "", hit: null, qty: 1, notFound: null, employee: window.PosEmployee,
      // 掃什麼印什麼:批次＝「款式＋掃到的條碼」。labelQty 為本批次已入庫、尚未加入列印清單的張數;
      // batchId 每換一批就加一,非同步回應回來時用它確認還是同一批,避免張數算到別的商品上。
      scannedCode: "", batchKey: "", batchId: 0, labelQty: 0, labelMsg: "", labelBusy: false,
    };
  },
  async mounted() {
    this.$refs.scan.focus();
  },
  methods: {
    startBatch(key) {
      this.batchId += 1; this.batchKey = key; this.labelQty = 0; this.labelMsg = "";
    },
    async onScan() {
      const code = String(this.scanCode || "").trim();
      if (!code) return;
      try {
        const query = await API.barcodeQuery(code);
        if (!query) return;
        const key = `${query.data.variant_id}|${code}`;
        // 重掃同一個條碼不算新批次,累計張數保留。
        if (key !== this.batchKey) this.startBatch(key);
        this.hit = query.data;
        this.notFound = null;
        this.scannedCode = code;
        this.scanCode = "";
      } catch (e) {
        // 進貨不建立產品(規格 §6.1):查無條碼一律引導至商品設定
        if (e.status === 404) {
          this.hit = null; this.notFound = code; this.scanCode = "";
          this.scannedCode = ""; this.startBatch("");
        }
        else this.showError(e.message);
      }
    },
    goSettings() { this.goPage("settings"); },
    async doReceive() {
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      await this.guard(async () => {
        const qty = this.qty, batchId = this.batchId;
        const r = await API.receiveStock(
          { variant_id: this.hit.variant_id, qty, employee: this.employee.id });
        // 等待期間已換成別的商品:庫存已入帳,但不可把張數算到新商品上。
        if (batchId !== this.batchId) return;
        this.hit.stock = r.stock; this.qty = 1;
        this.labelQty += qty; this.labelMsg = "";
        this.$refs.scan.focus();
      });
    },
    async addToLabels() {
      if (this.labelBusy || !this.labelQty || !this.hit || !this.scannedCode) return;
      this.labelBusy = true;
      const qty = this.labelQty, batchId = this.batchId;
      const variantId = this.hit.variant_id, code = this.scannedCode;
      try {
        await this.guard(async () => {
          const added = await window.PosLabelQueueAdd(variantId, code, qty);
          if (added.result === "added" || added.result === "merged") {
            // 只扣掉這次送出的張數:等待期間同批次又入庫的張數要留著;已換批次則不動目前品項。
            if (batchId !== this.batchId) return;
            this.labelQty -= qty;
            this.labelMsg = `已加入列印清單 ${qty} 張。`;
          } else if (added.result === "printing") {
            this.showError("標籤列印中，請稍候再加入列印清單。");
          } else if (added.result === "cancelled") {
            this.showError("加入已取消，請重新加入列印清單。");
          } else if (added.result === "blocked") {
            this.showError("此條碼在列印清單中已失效，請先至標籤列印頁移除該列。");
          } else {
            this.showError("此條碼已不屬於任何可列印的子產品。");
          }
        });
      } finally {
        this.labelBusy = false;
        this.$refs.scan?.focus();
      }
    },
  },
};
