window.PosPages = window.PosPages || {};

// 主視窗與子視窗共用的全域 mixin。
// guard:try/catch → showError(失敗不 reload);guardReload:成功後呼叫該頁 reload/reloadAll。
// attrText:規格顯示字串,fallback 供資料庫頁的「(無規格)」兜底。
window.PosMixin = {
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
};
