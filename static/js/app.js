window.PosPages = window.PosPages || {};
document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp({
    data() {
      return { page: "checkout", error: "", pages: [
        ["checkout", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"],
        ["records", "銷售紀錄"], ["catalog", "商品資料庫"], ["settings", "設定"]] };
    },
    methods: {
      showError(msg) { this.error = msg; setTimeout(() => this.error = "", 5000); },
    },
    provide() { return { showError: (m) => this.showError(m) }; },
  });
  for (const [name, comp] of Object.entries(window.PosPages))
    app.component(name, comp);
  for (const [name, comp] of Object.entries(window.PosComponents || {}))
    app.component(name, comp);
  app.mount("#app");
});
