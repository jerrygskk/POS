window.PosComponents = window.PosComponents || {};
// 商品報損框(限管理者):掃碼帶出商品,填數量與原因後直接扣庫存,不影響立帳。
window.PosComponents["stock-loss"] = {
  emits: ["close"],
  data() { return { code: "", item: null, qty: 1, reason: "", busy: false, error: "" }; },
  template: `<div class="modal-overlay" @click.self="cancel">
    <div class="modal doc-correct">
      <h3>商品報損</h3>
      <div class="doc-field"><span>商品條碼</span><input ref="code" v-model="code" :disabled="busy" placeholder="掃描條碼後按 Enter" @keydown.enter.prevent="scan"></div>
      <div v-if="item" class="doc-locked">
        <span>商品</span><b>{{ item.name }}</b><span>目前庫存</span><b>{{ item.stock }}</b>
        <span>規格</span><b>{{ item.attr_display || '—' }}</b>
      </div>
      <div class="doc-field"><span>報損數量</span><input ref="qty" v-model.number="qty" type="number" min="1" class="w80" :disabled="busy || !item"></div>
      <div class="doc-field"><span>報損原因</span><input v-model="reason" maxlength="60" :disabled="busy || !item" placeholder="最多 60 字"></div>
      <div class="error-bar section-bar doc-warning">送出後立即扣除庫存，且無法修改或作廢。</div>
      <div class="modal-actions">
        <span class="modal-warn">{{ error }}</span>
        <button class="primary danger" :disabled="busy || !item" @click="submit">{{ busy ? '處理中…' : '確認報損' }}</button>
        <button :disabled="busy" @click="cancel">取消</button>
      </div>
    </div>
  </div>`,
  mounted() { this.$nextTick(() => this.$refs.code?.focus()); },
  methods: {
    cancel() { if (!this.busy) this.$emit("close"); },
    async scan() {
      const code = this.code;
      this.code = "";
      if (!code.trim() || this.busy) return;
      // 報損送出後無法更正:重新掃描當下就清掉舊商品,查詢失敗或未完成時才不會扣到上一件
      const seq = this._scanSeq = (this._scanSeq || 0) + 1;
      this.item = null; this.qty = 1; this.reason = ""; this.error = "";
      try {
        const found = await window.PosScan(code);
        if (seq !== this._scanSeq || !found) return;
        this.item = found.data;
        this.$nextTick(() => this.$refs.qty?.focus());
      } catch (e) {
        if (seq === this._scanSeq) this.error = `條碼 ${code}：${e.message}`;
      }
    },
    async submit() {
      if (this.busy || !this.item) return;
      // 開框期間可能已登出或閒置逾時:送出前再查一次
      if (!window.PosAuth.isAdmin) { this.error = "需要管理者權限"; return; }
      if (!Number.isInteger(this.qty) || this.qty <= 0) { this.error = "報損數量必須是正整數"; return; }
      if (!this.reason.trim()) { this.error = "請填寫報損原因"; return; }
      this.busy = true; this.error = "";
      try {
        await API.createStockLoss({ variant_id: this.item.variant_id, qty: this.qty, reason: this.reason.trim() });
        this.busy = false;
        this.$emit("close");
        await PosConfirm.notify("已完成報損，庫存已扣除。");
      } catch (e) { this.error = e.message; }
      finally { this.busy = false; }
    },
  },
};
