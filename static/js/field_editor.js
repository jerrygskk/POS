// 規格選項子視窗:設定頁自訂規格列的「✎ 選項」開此視窗。
// 名稱／型態／必填／啟用／排序都在清單列上就地設定,此視窗只管選項清單與
// 建檔預設帶入值;選項數量可觀,留在網頁對話框會被迫巢狀捲動,故走 pywebview 子視窗。
// 脈絡由主視窗傳入:{ category_id, field_id }。
window.FieldEditorApp = {
  data() {
    return {
      loading: true,
      saving: false,
      committed: false,
      error: "",
      categoryId: null,
      fieldId: null,
      name: "",
      fieldType: "select",
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
        const fields = await API.listFields({ category_id: this.categoryId });
        const field = fields.find(f => f.field_id === this.fieldId);
        if (!field) throw new Error("查無此規格欄");
        this.name = field.name;
        this.fieldType = field.field_type;
        this.defaultOptionId = field.default_option_id ?? null;
        await this.reloadOptions();
      } catch (error) {
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },
    async reloadOptions() {
      this.options = await API.listOptions({ field_id: this.fieldId, all: 1 });
    },
    async addOption() {
      const value = (this.newOption || "").trim();
      if (!value) return;
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
      if (!await PosConfirm.ask(`刪除選項「${option.value}」?已有商品使用時會改為停用。`,
                                { danger: true })) return;
      await this.withError(async () => {
        await API.deleteOption(option.option_id);
        if (this.defaultOptionId === option.option_id) this.defaultOptionId = null;
        await this.reloadOptions();
      });
    },
    async cleanupOptions() {
      if (!await PosConfirm.ask("將永久刪除此規格欄中未使用且非建檔預設帶入值的選項，無法復原。確定繼續?",
                                { danger: true })) return;
      await this.withError(async () => {
        const result = await API.cleanupOptions(this.fieldId);
        await this.reloadOptions();
        await PosConfirm.notify(`已清理 ${result.deleted} 個未使用選項。`);
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
      this.saving = true;
      this.error = "";
      try {
        await API.setCategoryField(this.categoryId, this.fieldId, {
          default_option_id: this.fieldType === "select" ? (this.defaultOptionId ?? null) : null,
        });
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
