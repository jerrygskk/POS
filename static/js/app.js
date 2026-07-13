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
  // 全域 mixin:各頁共用的錯誤包裝與規格顯示。
  // guard:try/catch → showError(失敗不 reload);guardReload:成功後呼叫該頁 reload/reloadAll。
  // attrText:規格顯示字串,fallback 供資料庫頁的「(無規格)」兜底。
  app.mixin({
    methods: {
      async guard(fn) {
        try { return await fn(); }
        catch (e) { this.showError(e.message); }
      },
      async guardReload(fn) {
        try {
          await fn();
          if (typeof this.reloadAll === "function") await this.reloadAll();
          else if (typeof this.reload === "function") await this.reload();
        } catch (e) { this.showError(e.message); }
      },
      attrText(row, fallback) {
        const s = window.fmtAttr(row);
        return (s === "" && fallback !== undefined) ? fallback : s;
      },
    },
  });
  for (const [name, comp] of Object.entries(window.PosPages))
    app.component(name, comp);
  for (const [name, comp] of Object.entries(window.PosComponents || {}))
    app.component(name, comp);
  app.mount("#app");
});
