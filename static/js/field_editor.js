// 規格設定子視窗:設定頁「新增規格／✎ 修改」開此視窗(原本是網頁遮罩對話框)。
// 內容量與款式修改同級(名稱、型態、排序、必填、預設值、選項清單),留在網頁對話框
// 會被迫巢狀捲動,故比照款式修改改開 pywebview 子視窗(可拖、可縮)。
// 脈絡由主視窗傳入:{ category_id, field_id, cat_has_variant }。field_id 為 null＝新增。
window.FieldEditorApp = {
  data() {
    return {
      loading: true,
      saving: false,
      committed: false,
      error: "",
      categoryId: null,
      fieldId: null,
      catHasVariant: false,
      name: "",
      fieldType: "select",
      sort: 1,
      required: false,
      active: true,
      defaultOptionId: null,
      options: [],
      newOption: "",
    };
  },
  async mounted() {
    this._keydown = (event) => this.handleKeydown(event);
    document.addEventListener("keydown", this._keydown);
    await this.load();
  },
  unmounted() {
    document.removeEventListener("keydown", this._keydown);
  },
  computed: {
    isNew() { return this.fieldId == null; },
    hasOptions() { return ["select", "multi", "tags"].includes(this.fieldType); },
    activeOptions() { return this.options.filter(o => o.active); },
  },
  methods: {
    async load() {
      this.loading = true;
      this.error = "";
      try {
        const envelope = await API.invoke("desktop.child_window.context", {});
        const context = envelope.context || {};
        this.categoryId = context.category_id ?? null;
        this.fieldId = context.field_id ?? null;
        this.catHasVariant = !!context.cat_has_variant;
        const fields = await API.listFields({ category_id: this.categoryId });
        if (this.fieldId == null) {
          this.sort = fields.length ? Math.max(...fields.map(f => f.sort)) + 1 : 1;
        } else {
          const field = fields.find(f => f.field_id === this.fieldId);
          if (!field) throw new Error("查無此規格欄");
          this.name = field.name;
          this.fieldType = field.field_type;
          this.sort = field.sort;
          this.required = !!field.required;
          this.active = !!field.cf_active;
          this.defaultOptionId = field.default_option_id ?? null;
          await this.reloadOptions();
        }
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    async reloadOptions() {
      if (this.fieldId == null) { this.options = []; return; }
      this.options = await API.listOptions({ field_id: this.fieldId, all: 1 });
    },
    async addOption() {
      const value = (this.newOption || "").trim();
      if (!value || this.fieldId == null) return;
      await this.withError(async () => {
        await API.createOption({ field_id: this.fieldId, value, reactivate: true });
        this.newOption = "";
        await this.reloadOptions();
      });
    },
    handleOptionEnter(event) {
      event.preventDefault();
      event.stopPropagation();
      this.addOption();
    },
    async deleteOption(option) {
      if (!confirm(`刪除選項「${option.value}」?已有商品使用時會改為停用。`)) return;
      await this.withError(async () => {
        await API.deleteOption(option.option_id);
        if (this.defaultOptionId === option.option_id) this.defaultOptionId = null;
        await this.reloadOptions();
      });
    },
    async cleanupOptions() {
      if (this.fieldId == null) return;
      if (!confirm("將永久刪除此規格欄中未使用且非預設值的選項,無法復原。確定繼續?")) return;
      await this.withError(async () => {
        const result = await API.cleanupOptions(this.fieldId);
        await this.reloadOptions();
        alert(`已清理 ${result.deleted} 個未使用選項。`);
      });
    },
    // 子視窗自己的錯誤呈現:失敗留在視窗上讓店員修正,不關窗
    async withError(operation) {
      this.error = "";
      try { await operation(); }
      catch (error) { this.error = error.message; }
    },
    async save() {
      if (this.saving || this.loading) return;
      const name = (this.name || "").trim();
      if (!name) { this.error = "請輸入規格欄名稱"; return; }
      this.saving = true;
      this.error = "";
      try {
        let fieldId = this.fieldId;
        if (fieldId == null) {
          const created = await API.createField({
            name, category_id: this.categoryId, field_type: this.fieldType });
          fieldId = created.field_id;
        } else {
          const patch = {};
          const fields = await API.listFields({ category_id: this.categoryId });
          const current = fields.find(f => f.field_id === fieldId);
          if (current) {
            if (name !== current.name) patch.name = name;
            if (this.fieldType !== current.field_type) patch.field_type = this.fieldType;
          }
          if (Object.keys(patch).length) await API.updateField(fieldId, patch);
        }
        const setFields = {
          sort: parseInt(this.sort, 10) || 0,
          active: this.active ? 1 : 0,
          default_option_id: this.fieldType === "select" ? (this.defaultOptionId ?? null) : null,
        };
        if (!this.catHasVariant) setFields.required = this.required ? 1 : 0;
        await API.setCategoryField(this.categoryId, fieldId, setFields);
        this.committed = true;
        await API.invoke("desktop.child_window.close", { saved: true });
      } catch (error) {
        this.error = error.message;
      } finally {
        this.saving = false;
      }
    },
    async cancel() {
      if (this.saving) return;
      await API.invoke("desktop.child_window.close", { saved: this.committed });
    },
    async handleKeydown(event) {
      if (event.key === "Escape") await this.cancel();
    },
  },
};

document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp(window.FieldEditorApp);
  for (const [name, component] of Object.entries(window.PosComponents || {}))
    app.component(name, component);
  app.mount("#field-editor");
});
