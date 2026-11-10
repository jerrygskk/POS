window.PosComponents = window.PosComponents || {};
// 單據更正框(單層):由上而下「鎖定資訊→補填→警示→動作列」,一個框做完,不另跳確認框。
// mode:void 作廢銷售單、payment 更正付款方式、employee 修正操作者(銷售／退貨／進貨／盤點單)。
// 三種動作都不問、也不記是誰做的(維護者 2026-11-10 裁示),只限管理者。
// doc:{ type, label, id, ts, employee, payment, total, count };payment 模式另給 payments 清單。
// 關閉時送出 close(結果):"saved" 成功、"conflict" 單據已被改過(呼叫端重查)、false 取消。
window.PosComponents["doc-correct"] = {
  inject: ["showError"],
  props: { mode: { type: String, required: true }, doc: { type: Object, required: true },
           payments: { type: Array, default: () => [] } },
  emits: ["close"],
  data() {
    return { reasons: [["duplicate", "重複結帳"], ["item", "品項錯誤"], ["qty", "數量錯誤"], ["amount", "金額錯誤"], ["other", "其他"]],
             reasonCode: "", reasonText: "", payment: "", newEmployee: null, busy: false, error: "" };
  },
  computed: {
    title() {
      if (this.mode !== "employee") return { void: "作廢銷售單", payment: "更正付款方式" }[this.mode];
      return { sale: "修正結帳操作者", receipt: "修正進貨操作者", return: "修正退貨操作者", stocktake: "修正盤點操作者" }[this.doc.type];
    },
    otherPayments() { return this.payments.filter(p => p !== this.doc.payment); },
    timeText() { return String(this.doc.ts || "").slice(0, 16); },
  },
  template: `<div class="modal-overlay" @click.self="cancel">
    <div class="modal doc-correct">
      <h3>{{ title }}</h3>
      <div v-if="mode === 'employee'" class="doc-locked">
        <span>單據</span><b>{{ doc.label }}</b><span>單號</span><b>{{ doc.id }}</b>
        <span>時間</span><b>{{ timeText }}</b><span>目前操作者</span><b>{{ doc.employee ?? '—' }}</b>
      </div>
      <div v-else class="doc-locked">
        <span>單號</span><b>{{ doc.id }}</b><span>時間</span><b>{{ timeText }}</b>
        <span>員工</span><b>{{ doc.employee ?? '—' }}</b><span>付款</span><b>{{ doc.payment }}</b>
        <span>應收</span><b>{{ doc.total }}</b><span>項數</span><b>{{ doc.count }}</b>
      </div>
      <label v-if="mode === 'void'" class="doc-first">原因 <select v-model="reasonCode" :disabled="busy">
        <option value="" disabled>請選擇</option>
        <option v-for="[code, text] in reasons" :key="code" :value="code">{{ text }}</option></select></label>
      <!-- 說明欄隨原因出現:位置一直保留,只切換看不看得見,下方版面不跳 -->
      <label v-if="mode === 'void'" :class="{'doc-hidden': reasonCode !== 'other'}">說明 <input v-model="reasonText" maxlength="30" :disabled="busy || reasonCode !== 'other'" placeholder="最多 30 字"></label>
      <label v-if="mode === 'payment'" class="doc-first">新付款方式 <select v-model="payment" :disabled="busy">
        <option value="" disabled>請選擇</option>
        <option v-for="p in otherPayments" :key="p" :value="p">{{ p }}</option></select></label>
      <div v-if="mode === 'employee'" class="doc-field doc-first"><span>正確操作者</span><employee-picker v-model="newEmployee"></employee-picker></div>
      <div v-if="mode === 'void'" class="error-bar section-bar doc-warning">作廢後庫存將加回、此單不計入營業額，而且不可恢復。若今天盤點已調整過這些品項，作廢後請再盤一次。</div>
      <div v-if="mode === 'payment'" class="error-bar section-bar doc-warning">更正後，該帳日各付款方式的金額會跟著變動。</div>
      <div class="modal-actions">
        <span class="modal-warn">{{ error }}</span>
        <button class="primary" :class="{danger: mode === 'void'}" :disabled="busy" @click="submit">{{ busy ? '處理中…' : (mode === 'void' ? '確認作廢' : mode === 'employee' ? '確認修正' : '確認更正') }}</button>
        <button :disabled="busy" @click="cancel">取消</button>
      </div>
    </div>
  </div>`,
  mounted() {
    // 初始焦點在補填區第一個該填的欄位(原因／新付款方式／新員工的第一顆鈕)
    this.$nextTick(() => {
      const el = this.$el.querySelector && this.$el.querySelector(".doc-first select, .doc-first button");
      if (el) el.focus();
    });
  },
  methods: {
    // 送出中不關框:關掉重開再送會與前一次請求重複
    cancel() { if (!this.busy) this.$emit("close", false); },
    // 必填檢查;通過回傳要送出的請求,不通過設定錯誤訊息並回 null
    request() {
      const d = this.doc;
      if (this.mode === "void") {
        if (!this.reasonCode) { this.error = "請選擇作廢原因"; return null; }
        const text = this.reasonCode === "other" ? this.reasonText.trim() : "";
        if (this.reasonCode === "other" && !text) { this.error = "原因選「其他」時請填寫說明"; return null; }
        return () => API.voidSale({ sale_id: d.id, reason_code: this.reasonCode, reason_text: text, expected_total: d.total });
      }
      if (this.mode === "payment") {
        if (!this.payment) { this.error = "請選擇新付款方式"; return null; }
        return () => API.setSalePayment({ sale_id: d.id, payment: this.payment, expected_payment: d.payment });
      }
      if (!this.newEmployee) { this.error = "請選擇正確操作者"; return null; }
      if (this.newEmployee === d.employee) { this.error = "與目前的操作者相同，不需修正"; return null; }
      return () => API.setDocEmployee({ doc_type: d.type, doc_id: d.id, employee: this.newEmployee,
                                        expected_employee: d.employee ?? null });
    },
    async submit() {
      if (this.busy) return;
      // 開框期間可能已登出或閒置逾時:送出前再查一次
      if (!window.PosAuth.isAdmin) { this.error = "需要管理者權限"; return; }
      const send = this.request();
      if (!send) return;
      this.busy = true; this.error = "";
      try {
        await send();
        this.$emit("close", "saved");
      } catch (e) {
        // 衝突＝單據已被改過:關框讓呼叫端重查;其他錯誤留在框內、內容保留
        if (e.code === "conflict") { this.showError(e.message); this.$emit("close", "conflict"); }
        else this.error = e.message;
      } finally { this.busy = false; }
    },
  },
};
