// 新增款式子視窗的外殼:取脈絡 → 掛載建檔頁 → 關窗時通知主視窗是否要刷新。
document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp({
    data() {
      return {error: "", ready: false, initCategoryId: null,
        initProductId: null, saved: false};
    },
    async mounted() {
      try {
        const result = await API.invoke("desktop.child_window.context", {});
        const context = (result && result.context) || {};
        this.initCategoryId = context.category_id ?? null;
        this.initProductId = context.product_id ?? null;
      } catch (error) {
        this.showError(error.message);
      }
      this.ready = true;
      this._keydown = (event) => {
        if (event.key !== "Escape") return;
        if (event.repeat) return;
        const page = this.$refs.batchPage;
        if (page && page.fixedEditor) {
          event.preventDefault();
          page.closeFixedEditor();
          return;
        }
        this.closeWindow();
      };
      document.addEventListener("keydown", this._keydown);
    },
    unmounted() { document.removeEventListener("keydown", this._keydown); },
    methods: {
      showError(message) {
        this.error = message;
        setTimeout(() => this.error = "", 5000);
      },
      // 建檔頁每成功送出一批就呼叫,關窗時主視窗才知道要刷新。
      markSaved() { this.saved = true; },
      async closeWindow() {
        try {
          await API.invoke("desktop.child_window.close", {saved: this.saved});
        } catch (error) {
          this.showError(error.message);
        }
      },
    },
    provide() {
      return {
        showError: (message) => this.showError(message),
        goPage: () => this.closeWindow(),
        markSaved: () => this.markSaved(),
      };
    },
  });
  app.mixin(window.PosMixin);
  for (const [name, component] of Object.entries(window.PosPages || {}))
    app.component(name, component);
  for (const [name, component] of Object.entries(window.PosComponents || {}))
    app.component(name, component);
  app.mount("#variant-batch-window");
});
