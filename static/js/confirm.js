// 確認／通知視窗公版:取代瀏覽器內建的 confirm()／alert()。
// pywebview 內建視窗是深色系統樣式、又貼在視窗上緣,與程式外觀不一致,
// 故一律改用本檔的白底對話框(置中、可用 Enter／Esc)。
// 用法:
//   if (!await PosConfirm.ask("確定刪除?")) return;
//   await PosConfirm.notify("已完成。");
// 主視窗與子視窗共用(各自的 html 都要載入本檔)。
window.PosConfirm = {
  // options:{ title, confirmText, cancelText, danger }
  ask(message, options) {
    return this._open(message, Object.assign({ cancel: true }, options || {}));
  },
  notify(message, options) {
    return this._open(message, Object.assign(
      { cancel: false, confirmText: "確定" }, options || {}));
  },
  _open(message, opts) {
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay modal-centered";
      const box = document.createElement("div");
      box.className = "modal confirm-box";
      if (opts.title) {
        const title = document.createElement("div");
        title.className = "confirm-title";
        title.textContent = opts.title;
        box.appendChild(title);
      }
      // 訊息可能多行(例如刪除影響筆數),逐行保留斷行
      const text = document.createElement("div");
      text.className = "confirm-text";
      text.textContent = String(message == null ? "" : message);
      box.appendChild(text);

      const actions = document.createElement("div");
      actions.className = "modal-actions";
      let cancelBtn = null;
      if (opts.cancel) {
        cancelBtn = document.createElement("button");
        cancelBtn.type = "button";
        cancelBtn.textContent = opts.cancelText || "取消";
        actions.appendChild(cancelBtn);
      }
      const okBtn = document.createElement("button");
      okBtn.type = "button";
      okBtn.className = opts.danger ? "primary danger" : "primary";
      okBtn.textContent = opts.confirmText || "確定";
      actions.appendChild(okBtn);
      box.appendChild(actions);
      overlay.appendChild(box);

      const close = (result) => {
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        resolve(result);
      };
      const onKey = (event) => {
        if (event.key === "Escape") { event.preventDefault(); close(false); }
        else if (event.key === "Enter") { event.preventDefault(); close(true); }
      };
      okBtn.addEventListener("click", () => close(true));
      if (cancelBtn) cancelBtn.addEventListener("click", () => close(false));
      // 點遮罩＝取消(等同 Esc);點對話框本身不關閉
      overlay.addEventListener("mousedown", (event) => {
        if (event.target === overlay) close(false);
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(overlay);
      okBtn.focus();
    });
  },
};
