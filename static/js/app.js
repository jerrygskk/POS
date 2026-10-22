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
window.addEventListener("pos-child-window-closed", () => {
  window.PosDesktopLock.unlock();
});
document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp({
    data() {
      return { page: "checkout", pageContext: null, error: "", auth: window.PosAuth };
    },
    computed: {
      pages() { return [
        ["checkout", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"],
        ["records", "銷售紀錄"], ["catalog", "商品資料庫"], ["labels", "標籤列印"], ["settings", "設定"]]
        .filter(([key]) => key !== "settings" || this.auth.isAdmin); },
    },
    watch: {
      "auth.isAdmin"(isAdmin) {
        if (!isAdmin && this.page === "settings") this.goPage("checkout");
      },
    },
    mounted() {
      this.guard(() => window.PosAuthOps.refresh());
      this._authPoll = setInterval(() => {
        if (this.auth.isAdmin) this.guard(() => window.PosAuthOps.refresh());
      }, 30000);
    },
    unmounted() { clearInterval(this._authPoll); },
    methods: {
      showError(msg) { this.error = msg; setTimeout(() => this.error = "", 5000); },
      goPage(name, context = null) {
        if (name === "settings" && !this.auth.isAdmin) {
          this.showError("需要管理者權限"); return;
        }
        this.error = ""; this.pageContext = context; this.page = name;
        this.guard(() => window.PosAuthOps.refresh());
      },
      toggleAdmin() {
        this.guard(() => this.auth.isAdmin ? window.PosAuthOps.logout() : window.PosAuthOps.login());
      },
    },
    provide() { return {
      showError: (m) => this.showError(m), goPage: (n, c) => this.goPage(n, c),
      // context 只供目標頁讀取一次,避免切回同一頁又被套用。
      takePageContext: () => { const c = this.pageContext; this.pageContext = null; return c; },
    }; },
  });
  app.mixin(window.PosMixin);
  for (const [name, comp] of Object.entries(window.PosPages))
    app.component(name, comp);
  for (const [name, comp] of Object.entries(window.PosComponents || {}))
    app.component(name, comp);
  app.mount("#app");
});
