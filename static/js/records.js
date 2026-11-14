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
// 立帳頁的畫面資料;管理者登出或逾時時整份換掉,金額不留在畫面上
function emptyDayClose() {
  return { date: "", today: "", counted: "", reason: "", floatInput: "", preview: null, records: [], busy: false, copied: false };
}
window.PosPages["page-records"] = {
  template: "#tpl-records",
  inject: ["showError"],
  data() {
    return { kinds: [["sales", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"], ["returns", "退貨"], ["dayclose", "立帳"], ["members", "資料設定"]], kind: "sales",
             dc: emptyDayClose(), correct: null,
             ...defaultRange("sales"), payment: "", payments: [],
             sales: [], summary: null, receipts: [], stocktakes: [], returns: [], returnKind: "normal", expanded: new Set(),
             memberPhone: "", memberQuery: "", members: [], memberPopup: null, salesQuery: null };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
    // 立帳只有管理者看得到
    shownKinds() { return this.kinds.filter(([key]) => key !== "dayclose" || this.isAdmin); },
    // 短溢＝錢櫃現金總額 − 預備金 − 現金銷售;還沒輸入數字時為 null
    dcDiff() {
      const p = this.dc.preview, text = this.dc.counted.trim();
      return p && /^\d+$/.test(text) ? Number(text) - p.float_amount - p.cash_sales : null;
    },
    // 立帳紀錄的付款方式欄:每一列順序相同,取第一列;沒有紀錄時用設定清單
    dcPayments() {
      const first = this.dc.records[0];
      return first ? first.by_payment.map(b => b.payment) : this.payments;
    },
    // 查詢區間的合計列
    dcTotals() {
      const sum = pick => this.dc.records.reduce((total, r) => total + pick(r), 0);
      const by_payment = this.dcPayments.map((_, i) => sum(r => r.by_payment[i].total));
      const sales_total = sum(r => r.sales_total);
      // 手續費:金流業者在提領時才扣,所以拿查詢區間的合計算一次(四捨五入到元),每天的金額不扣。
      // 費率是百分比,先乘 100 取整再算,避免小數誤差
      const first = this.dc.records[0], rates = first ? first.by_payment.map(b => b.fee_rate || 0) : [];
      const fees = by_payment.map((total, i) => Math.round(total * Math.round((rates[i] || 0) * 100) / 10000));
      const fee_total = fees.reduce((a, b) => a + b, 0);
      // rates:各付款方式的費率文字,寫在自己那一欄底下;0 的留空
      return { by_payment, sales_total, diff: sum(r => r.diff), fees, fee_total,
               rates: by_payment.map((_, i) => rates[i] ? rates[i] + "%" : ""),
               net: by_payment.map((total, i) => total - fees[i]), net_total: sales_total - fee_total };
    },
    // 目前類別每一列的開合鍵,與範本的 isOpen／toggle 共用
    rowKeys() {
      if (this.kind === "sales") return this.sales.map(s => s.sale_id);
      if (this.kind === "receive") return this.receipts.map(r => r.receipt_id);
      if (this.kind === "returns") return this.returns.map(t => t.return_id);
      if (this.kind === "members" || this.kind === "dayclose") return [];
      return this.stocktakes.map(t => t.session_id);
    },
  },
  watch: {
    // 只隱藏選單不夠:管理者登出或閒置逾時時若停在立帳,切回收銀紀錄並清掉已載入的立帳資料
    isAdmin(now) {
      if (!now) this.correct = null;
      if (now || this.kind !== "dayclose") return;
      this.clearDayClose();
      this.enterKind("sales");
    },
  },
  async mounted() {
    Object.assign(this, await this.salesRange());
    await this.guard(async () => {
      this.payments = await API.listPayments();
      await this.reload();
    });
  },
  methods: {
    async selectKind(kind) {
      if (this.kind === kind) return;
      if (kind === "dayclose" && !this.isAdmin) { this.showError("需要管理者權限"); return; }
      await this.enterKind(kind);
    },
    async enterKind(kind) {
      this.kind = kind;
      Object.assign(this, defaultRange(kind));
      if (kind === "sales") Object.assign(this, await this.salesRange());
      if (this.kind !== kind) return;
      await this.reload();
      if (kind === "dayclose") await this.dcLoad(localDateText(new Date()));
    },
    // 收銀預設:起＝今天,迄＝目前帳日(今天已立帳時是明天,才看得到立帳後結的單)
    async salesRange() {
      const range = defaultRange("sales");
      try {
        const current = await API.dayCloseCurrent();
        if (current.business_date > range.dateTo) range.dateTo = current.business_date;
      } catch (e) { /* 查不到就用今天;查詢本身失敗會由 reload 顯示 */ }
      return range;
    },
    // 帳日與結帳日期不同:當天立帳後才結的單
    nextDayBook(s) { return !!s.business_date && s.business_date !== String(s.ts || "").slice(0, 10); },
    // 作廢:限管理者;框內送出前會再查一次身分
    openVoid(s) { this.openCorrect("void", "sale", s); },
    // 作廢、更正付款方式、更正員工共用一個框;type:sale／return／receipt／stocktake
    openCorrect(mode, type, row) {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      const doc = {
        sale: () => ({ label: "銷售單", id: row.sale_id, ts: row.ts, payment: row.payment, total: row.total, count: row.items.length }),
        return: () => ({ label: "退貨單", id: row.return_id, ts: row.started_at }),
        receipt: () => ({ label: "進貨單", id: row.receipt_id, ts: row.ts }),
        stocktake: () => ({ label: "盤點單", id: row.session_id, ts: row.started_at }),
      }[type]();
      this.correct = { mode, doc: { type, employee: row.employee, ...doc } };
    },
    correctionText(c) {
      const field = c.field === "payment" ? "付款方式" : "操作者";
      return `${this.minuteText(c.ts)}　${field}：${c.old_value ?? "—"} → ${c.new_value}`;
    },
    // 成功或單據已被改過都重查,並保留展開狀態
    async correctClosed(result) {
      this.correct = null;
      if (!result) return;
      const keep = new Set(this.expanded);
      await this.reload();
      this.expanded = keep;
    },
    signedText(n) { return n > 0 ? "+" + n : n < 0 ? "−" + (-n) : "0"; },
    shortDate(d) { return d.slice(5).replace("-", "/"); },
    clearDayClose() {
      this._dcSeq = (this._dcSeq || 0) + 1;
      this.dc = emptyDayClose();
    },
    // 讀某個帳日的立帳預覽;晚到的回應若已換日期、已離開或已不是管理者就丟棄
    async dcLoad(date) {
      if (date) this.dc.date = date;
      const seq = this._dcSeq = (this._dcSeq || 0) + 1;
      this.dc.preview = null; this.dc.counted = ""; this.dc.reason = "";
      if (!this.dc.date) return;
      await this.guard(async () => {
        const preview = await API.dayClosePreview(this.dc.date);
        if (seq !== this._dcSeq || !this.isAdmin || this.kind !== "dayclose") return;
        this.dc.preview = preview; this.dc.today = preview.today;
        this.dc.floatInput = String(preview.float_setting);
        // 初始焦點放在錢櫃現金總額,不可停在日期欄
        this.$nextTick(() => { if (this.$refs.dcCounted) this.$refs.dcCounted.focus(); });
      });
    },
    async dcSaveFloat() {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      const text = this.dc.floatInput.trim();
      if (!/^\d+$/.test(text)) { this.showError("預備金請輸入 0 以上的整數"); return; }
      await this.guard(async () => { await API.dayCloseSetFloat(Number(text)); });
      await this.dcLoad();
    },
    async dcClose() {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      const p = this.dc.preview;
      if (!p || p.closed || this.dc.busy) return;
      if (this.dcDiff === null) { this.showError("請輸入錢櫃現金總額"); return; }
      const employee = window.PosEmployee.id;
      if (!employee) { this.showError("請選擇員工代號（1～6）"); return; }
      const reason = this.dc.reason.trim();
      if (this.dcDiff !== 0 && !reason) { this.showError("有短溢時請填寫原因"); return; }
      // 按下當下把內容固定成一份:確認框回來後送出的是同一份,不讀輸入框當下的值
      const body = { business_date: p.business_date, counted_total: Number(this.dc.counted.trim()), reason, employee,
                     expected: { float_amount: p.float_amount, cash_sales: p.cash_sales } };
      // 確認框掛在頁面最外層,管理者逾時後清不掉:只放帳日,不放實點與短溢
      if (!await PosConfirm.ask(`確定完成 ${p.business_date} 的立帳？立帳後此帳日的銷售單不可再作廢或更正付款方式。`)) return;
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      if (this.dc.busy) return;
      this.dc.busy = true;
      try {
        await this.guard(async () => { await API.dayCloseClose(body); });
      } finally {
        this.dc.busy = false;
        // 成敗都重讀:失敗可能是金額已變動或已被立帳,不可留舊數字
        if (this.isAdmin && this.kind === "dayclose") { await this.dcLoad(body.business_date); await this.reload(); }
      }
    },
    // 把查到的立帳紀錄畫成圖片放進剪貼簿,供貼到公司群組。
    // 圖片是給公司看的報表,欄位與畫面上的表格不同:只列帳日、各付款方式、銷售合計、短溢與原因,
    // 不列員工、立帳時間、預備金、錢櫃總額;日期由小到大(畫面是新的在上)
    async dcCopyImage() {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      const records = this.dc.records.slice().reverse(), totals = this.dcTotals;
      if (!records.length) { this.showError("沒有可複製的立帳紀錄"); return; }
      // 一列:第一格與最後一格(原因)靠左,中間的金額靠右;bold 的列用粗體
      const line = (bold, ...texts) => texts.map((text, i, all) => ({ text: String(text), num: i > 0 && i < all.length - 1, bold }));
      const minus = n => n ? "−" + n : "";
      const rows = [line(true, "帳日", ...this.dcPayments, "銷售合計", "短溢", "原因"),
                    ...records.map(r => line(false, r.business_date, ...r.by_payment.map(b => b.total), r.sales_total,
                                             this.signedText(r.diff), r.reason || "")),
                    line(true, "合計", ...totals.by_payment, totals.sales_total, this.signedText(totals.diff), "")];
      // 有手續費才多三列:抽成比例、抽成金額、實收
      if (totals.fee_total) {
        rows.push(line(false, "抽成比例", ...totals.rates, "", "", ""),
                  line(false, "抽成金額", ...totals.fees.map(minus), minus(totals.fee_total), "", ""),
                  line(true, "實收", ...totals.net, totals.net_total, "", ""));
      }
      const title = `立帳紀錄　${records[0].business_date} ～ ${records[records.length - 1].business_date}`;
      const font = '15px "Microsoft JhengHei", sans-serif', pad = 10, rowH = 30, titleH = 40, scale = 2;
      const canvas = document.createElement("canvas"), g = canvas.getContext("2d");
      // 表頭與合計列是粗體,用粗體量字寬才不會切到
      g.font = "bold " + font;
      const widths = rows[0].map((_, i) => Math.ceil(Math.max(...rows.map(r => g.measureText(r[i].text).width))) + pad * 2);
      const width = Math.max(widths.reduce((a, b) => a + b, 0), Math.ceil(g.measureText(title).width) + pad * 2);
      const height = titleH + rowH * rows.length;
      canvas.width = width * scale; canvas.height = height * scale;
      g.scale(scale, scale);
      g.fillStyle = "#fff"; g.fillRect(0, 0, width, height);
      g.fillStyle = "#f2f2f7"; g.fillRect(0, titleH, width, rowH);
      g.textBaseline = "middle"; g.fillStyle = "#1c1c1e"; g.font = "bold " + font;
      g.fillText(title, pad, titleH / 2);
      rows.forEach((row, n) => {
        const y = titleH + rowH * n;
        g.font = (row[0].bold ? "bold " : "") + font;
        let x = 0;
        row.forEach((cell, i) => {
          g.textAlign = cell.num ? "right" : "left";
          g.fillText(cell.text, cell.num ? x + widths[i] - pad : x + pad, y + rowH / 2);
          x += widths[i];
        });
        g.fillStyle = "#c6c6c8"; g.fillRect(0, y + rowH - 1, width, 1); g.fillStyle = "#1c1c1e";
      });
      try {
        const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
        await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      } catch (e) { this.showError("複製圖片失敗，請再試一次"); return; }
      this.dc.copied = true;
      setTimeout(() => { this.dc.copied = false; }, 2000);
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
      // 查詢後資料可能換了,一律收合;更正後要保留展開的由呼叫端自己貼回
      this.collapseAll();
      await this.guard(async () => {
        const range = {date_from: this.dateFrom, date_to: this.dateTo};
        if (this.kind === "sales") {
          const filters = {...range, payment: this.payment || "", member_phone: this.memberPhone.trim()};
          // 查詢失敗先清空,不留上一次結果;成功才記住條件給匯出用
          // 連續查詢時只讓最新一次更新畫面:列表、合計、匯出條件必須屬於同一次查詢
          const seq = this._salesSeq = (this._salesSeq || 0) + 1;
          this.sales = []; this.summary = null; this.salesQuery = null;
          const sales = await API.listSales(filters);
          const summary = await API.salesSummary(filters);
          if (seq !== this._salesSeq) return;
          this.sales = sales; this.summary = summary; this.salesQuery = filters;
        } else if (this.kind === "members") {
          this.members = await API.listMembers(this.memberQuery.trim());
        } else if (this.kind === "dayclose") {
          const records = await API.dayCloseList(range);
          if (!this.isAdmin || this.kind !== "dayclose") return;
          this.dc.records = records;
        } else if (this.kind === "receive") {
          this.receipts = await API.listReceipts(range);
        } else if (this.kind === "returns") {
          this.returns = await API.returnHistory({...range, kind: this.returnKind});
        } else {
          this.stocktakes = await API.stocktakeHistory(range);
        }
      });
    },
    // 返修品項:未處理件數、狀態(待返新/部分返新/已全部返新/已結案)與副標
    repairLeft(it) { return it.qty - it.closed_qty - it.repaired_qty; },
    repairState(it) {
      if (this.repairLeft(it) > 0) return it.repaired_qty > 0 ? "partial" : "waiting";
      return it.closed_qty > 0 ? "closed" : "done";
    },
    repairStateText(it) {
      return {waiting: "待返新", partial: "部分返新", done: "已全部返新", closed: "已結案"}[this.repairState(it)];
    },
    repairSpecText(it) {
      return [this.specText(it), (it.models || []).join("、"), it.sale_id ? `銷售單 ${it.sale_id}` : ""]
        .filter(Boolean).join(" ・ ") || "—";
    },
    returnSheetStatus(t) {
      if (this.returnKind !== "repair") return this.statusText(t.status);
      if (t.status === "closed") return "已完成";
      const n = t.items.filter(it => this.repairLeft(it) > 0).length;
      return `返修中（待處理 ${n} 項）`;
    },
    async selectReturnKind(kind) {
      if (this.returnKind === kind) return;
      this.returnKind = kind;
      await this.reload();
    },
    // 返修結案／撤回只限管理者;確認視窗期間可能已登出,送出前再檢查一次。完成後保持展開該單
    async closeRepairItem(t, it) {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      const reason = await PosConfirm.input({ title: "返修品項結案",
        message: `「${it.name}」尚未返新的 ${this.repairLeft(it)} 件將結案，結案後不再等待返新。請輸入結案理由：`,
        validate: v => {
          const text = String(v || "").trim();
          if (!text) return "請輸入結案理由";
          return text.length > 30 ? "結案理由最多 30 字" : "";
        } });
      if (reason === null) return;
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      await this.repairWrite(t, () => API.closeReturnItem(t.return_id, it.id, String(reason).trim()));
    },
    async reopenRepairItem(t, it) {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      if (!await PosConfirm.ask(`撤回「${it.name}」的結案？此品項回到等待返新。`)) return;
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      await this.repairWrite(t, () => API.reopenReturnItem(t.return_id, it.id));
    },
    async repairWrite(t, fn) {
      await this.guard(async () => {
        try { await fn(); } finally {
          // 失敗也重載:結案狀態可能已被別人改過,不可留舊畫面
          const keep = new Set(this.expanded);
          this.returns = await API.returnHistory({date_from: this.dateFrom, date_to: this.dateTo, kind: this.returnKind});
          this.expanded = keep;
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
      if (!this.salesQuery) { this.showError("請先查詢成功再匯出"); return; }
      await this.guard(async () => { await API.exportSales(this.salesQuery); });
    },
  },
};
