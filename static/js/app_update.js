// 線上更新:開啟後查 GitHub 有無新版,閒置時才詢問;按「更新」後整個畫面鎖住、
// 顯示下載百分比,完成自動重開。流程只透過 deps 動作,DOM 只在 createOverlay 內,
// 讓 Node 測試可注入假物件。後端只在打包後的 exe 回報有新版。
window.PosAppUpdate = {
  FAILED: "新版本下載失敗，請確認網路連線後重新開啟程式再試一次。",

  // 收銀購物車、進貨清單、標籤列印清單只存在記憶體,重開會清掉,有內容就不問
  async isIdle(deps) {
    if (deps.hasModal() || deps.lock._locked) return false;
    const c = deps.checkout, l = deps.labels, r = deps.receive;
    if (c && (c.cart.length || c.submitting)) return false;
    if (l && (l.rows.length || l.printing)) return false;
    if (r && (r.rows.length || r.locked)) return false;
    try {
      return !(await deps.api.invoke("desktop.update.busy")).busy;  // 規格設定子視窗開著
    } catch (e) {
      return false;
    }
  },

  defaults() {
    return {
      api: API, confirm: PosConfirm, lock: PosDesktopLock, overlay: () => this.createOverlay(),
      wait: ms => new Promise(r => setTimeout(r, ms)),
      checkout: window.PosCheckoutDraft, labels: window.PosLabelQueue, receive: window.PosReceiveDraft,
      hasModal: () => !!document.querySelector(".modal-overlay"),
    };
  },

  async run(deps) {
    deps = deps || this.defaults();
    let info;
    try {
      info = await deps.api.invoke("desktop.update.check");
    } catch (e) {
      return;
    }
    if (!info || !info.available) return;
    while (!(await this.isIdle(deps))) await deps.wait(1000);
    const ok = await deps.confirm.ask(
      `已有新版本 v${info.version}（目前 v${info.current}），是否立即更新？更新完成後程式會自動重新啟動。`,
      { title: "程式更新", confirmText: "更新", cancelText: "稍後" });
    if (!ok) return;
    try {
      await deps.api.invoke("desktop.update.start");
    } catch (e) {
      await deps.confirm.notify(e.message);
      return;
    }
    deps.lock.lock();
    const overlay = deps.overlay();
    const fail = async (message) => {
      overlay.close();
      deps.lock.unlock();
      await deps.confirm.notify(message);
    };
    let errors = 0;
    for (;;) {
      let s;
      try {
        s = await deps.api.invoke("desktop.update.status");
        errors = 0;
      } catch (e) {
        if (++errors >= 3) return fail(this.FAILED);
        await deps.wait(500);
        continue;
      }
      if (s.state === "failed") return fail(this.FAILED);
      if (s.state === "done") {
        overlay.setText("下載完成，正在重新啟動…");
        try {
          await deps.api.invoke("desktop.update.restart");
        } catch (e) {
          return fail(e.message);
        }
        return;
      }
      const percent = s.total ? Math.floor(s.got * 100 / s.total) : 0;
      overlay.setText(`下載新版本中（${s.state === "swapping" ? 100 : percent}%）`);
      await deps.wait(500);
    }
  },

  // 沿用確認框公版、無按鈕;攔下所有按鍵,條碼槍輸入不會進到收銀畫面
  createOverlay() {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay modal-centered";
    const box = document.createElement("div");
    box.className = "modal confirm-box";
    const title = document.createElement("div");
    title.className = "confirm-title";
    title.textContent = "程式更新";
    const text = document.createElement("div");
    text.className = "confirm-text";
    text.textContent = "下載新版本中（0%）";
    box.append(title, text);
    overlay.appendChild(box);
    const block = (event) => { event.preventDefault(); event.stopPropagation(); };
    // 收銀掃描框聽的是 keyup.enter,三種按鍵事件都要攔
    const keys = ["keydown", "keypress", "keyup"];
    for (const name of keys) document.addEventListener(name, block, true);
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    document.body.appendChild(overlay);
    return {
      setText(value) { text.textContent = value; },
      close() {
        for (const name of keys) document.removeEventListener(name, block, true);
        overlay.remove();
      },
    };
  },
};
