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
    async openChildWindow(options) {
      const {page, context, title} = options;
      window.PosDesktopLock.lock();
      try {
        const payload = {page, context};
        if (title !== undefined) payload.title = title;
        await API.invoke("desktop.child_window.open", payload);
      } catch (error) {
        window.PosDesktopLock.unlock();
        this.showError(error.message);
      }
    },
    attrText(row, fallback) {
      const s = window.fmtAttr(row);
      return (s === "" && fallback !== undefined) ? fallback : s;
    },
  },
};

// 掃描解析：同一條碼對應多個子產品時跳「選擇品項」(PosConfirm.choose)。
// 回傳 {code, data}；空白或使用者取消回 null；404／422 由頁面處理。
// sellableOnly：收銀用，停用／待處理的品項不可選。
window.PosScan = async function (value, options) {
  const code = String(value || "").trim();
  if (!code) return null;
  const { matches } = await API.scanBarcode(code);
  if (matches.length === 1) return { code, data: matches[0] };
  const index = await PosConfirm.choose({
    title: "請選擇品項",
    message: `條碼「${code}」對應多個品項，請選擇：`,
    items: matches.map(m => ({
      label: m.name,
      detail: `${m.attr_display || "（無規格）"}｜店內碼 ${m.store_barcode || "—"}｜${m.price === null ? "未定價" : m.price + " 元"}｜庫存 ${m.stock}`,
      note: m.status === "inactive" ? "已停用" : m.status === "issue" ? "待處理" : "",
      disabled: !!(options?.sellableOnly && m.status !== "active"),
    })),
  });
  return index === null ? null : { code, data: matches[index] };
};
