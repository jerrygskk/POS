window.PosPages = window.PosPages || {};
window.PosDesktopLock = {
  _locked: false,
  _scrollX: 0,
  _scrollY: 0,
  _scrollKeys: new Set([
    "PageUp", "PageDown", "Home", "End", " ", "Spacebar",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
  ]),
  _restoreScroll() {
    window.scrollTo(this._scrollX, this._scrollY);
  },
  _preventScroll(event) {
    event.preventDefault();
    this._restoreScroll();
  },
  _preventKeyScroll(event) {
    if (!this._scrollKeys.has(event.key) && event.code !== "Space") return;
    event.preventDefault();
    this._restoreScroll();
  },
  _handleScroll() {
    if (window.scrollX !== this._scrollX || window.scrollY !== this._scrollY)
      this._restoreScroll();
  },
  lock() {
    if (this._locked) return;
    const root = document.querySelector("#app");
    if (root) root.inert = true;
    this._locked = true;
    this._scrollX = window.scrollX;
    this._scrollY = window.scrollY;
    this._wheelHandler = (event) => this._preventScroll(event);
    this._touchHandler = (event) => this._preventScroll(event);
    this._keyHandler = (event) => this._preventKeyScroll(event);
    this._scrollHandler = () => this._handleScroll();
    window.addEventListener("wheel", this._wheelHandler,
      {capture: true, passive: false});
    window.addEventListener("touchmove", this._touchHandler,
      {capture: true, passive: false});
    window.addEventListener("keydown", this._keyHandler, {capture: true});
    window.addEventListener("scroll", this._scrollHandler, {capture: true});
  },
  unlock() {
    if (!this._locked) return;
    window.removeEventListener("wheel", this._wheelHandler, {capture: true});
    window.removeEventListener("touchmove", this._touchHandler, {capture: true});
    window.removeEventListener("keydown", this._keyHandler, {capture: true});
    window.removeEventListener("scroll", this._scrollHandler, {capture: true});
    this._restoreScroll();
    const root = document.querySelector("#app");
    if (root) root.inert = false;
    this._locked = false;
  },
};
window.addEventListener("pos-variant-editor-closed", () => {
  window.PosDesktopLock.unlock();
});
document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp({
    data() {
      return { page: "checkout", error: "", pages: [
        ["checkout", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"],
        ["records", "銷售紀錄"], ["catalog", "商品資料庫"], ["labels", "標籤列印"], ["settings", "設定"]] };
    },
    methods: {
      showError(msg) { this.error = msg; setTimeout(() => this.error = "", 5000); },
      goPage(name) { this.error = ""; this.page = name; },
    },
    provide() { return { showError: (m) => this.showError(m), goPage: (n) => this.goPage(n) }; },
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
