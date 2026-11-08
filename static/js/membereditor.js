window.PosComponents = window.PosComponents || {};
// 會員新增／修改框(單層):紀錄查詢與收銀共用。
// member:{ member_id(新增為 null), name, phones[] };關閉時送出 close(saved)。
window.PosComponents["member-editor"] = {
  inject: ["showError"],
  props: { member: { type: Object, required: true } },
  emits: ["close"],
  data() {
    return { form: { member_id: this.member.member_id, name: this.member.name,
                     phones: this.member.phones.length ? [...this.member.phones] : [""] },
             saving: false };
  },
  template: `<div class="modal-overlay" @click.self="cancel">
    <div class="modal">
      <h3>{{ form.member_id === null ? '新增會員' : '修改會員' }}</h3>
      <label>名稱 <input v-model="form.name" :disabled="saving"></label>
      <!-- 第二支起標籤留白:要放空 span 佔住標籤格,空字串不算格子,輸入框會被擠進標籤格 -->
      <label v-for="(phone, i) in form.phones" :key="i"><template v-if="i === 0">電話</template><span v-else></span>
        <span class="member-phone-row"><input v-model="form.phones[i]" :disabled="saving" inputmode="numeric" placeholder="0912345678">
          <button v-if="form.phones.length > 1" class="btn-x" title="移除電話" :disabled="saving" @click="removePhoneRow(i)">✕</button></span>
      </label>
      <div class="modal-actions">
        <button class="btn-sm" :disabled="saving" @click="addPhoneRow">新增電話</button>
        <span class="modal-warn"></span>
        <button class="primary" :disabled="saving" @click="save">{{ saving ? '儲存中…' : '儲存' }}</button>
        <button :disabled="saving" @click="cancel">取消</button>
      </div>
    </div>
  </div>`,
  methods: {
    // 儲存中一律不動：關窗重開再送會與前一次請求重複建立
    cancel() { if (!this.saving) this.$emit("close", false); },
    addPhoneRow() { if (!this.saving) this.form.phones.push(""); },
    removePhoneRow(i) { if (!this.saving) this.form.phones.splice(i, 1); },
    // 前端先擋格式;同一支電話已屬別人時允許,但先請店員確認
    async save() {
      if (this.saving) return;
      const form = this.form;
      const name = form.name.trim();
      if (!name) { this.showError("請輸入會員名稱"); return; }
      const phones = [];
      for (const raw of form.phones.filter(p => p.trim())) {
        const phone = raw.replace(/[\s\-－–]/g, "").replace(/[０-９]/g, d => String.fromCharCode(d.charCodeAt(0) - 0xFEE0));
        if (!/^09\d{8}$/.test(phone)) { this.showError(`電話格式不正確：${raw}`); return; }
        if (!phones.includes(phone)) phones.push(phone);
      }
      if (!phones.length) { this.showError("請至少輸入一支電話"); return; }
      this.saving = true;
      try {
        await this.guard(async () => {
          const owners = await API.memberPhoneOwners({ phones, exclude_member_id: form.member_id });
          if (owners.length) {
            const lines = owners.map(o => `${o.phone} ${o.names.join("、")}`).join("\n");
            if (!await PosConfirm.ask(`此電話已屬於：${lines}\n仍要儲存嗎？`)) return;
          }
          if (form.member_id === null) await API.createMember({ name, phones });
          else await API.updateMember({ member_id: form.member_id, name, phones });
          this.$emit("close", true);
        });
      } finally { this.saving = false; }
    },
  },
};
