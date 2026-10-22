window.PosAuth = Vue.reactive({ isAdmin: false, pendingStocktakes: 0 });
window.PosAuthOps = {
  _refreshToken: 0,
  async refresh() {
    const token = ++this._refreshToken;
    const status = await API.authStatus();
    if (token !== this._refreshToken) return;
    window.PosAuth.isAdmin = status.is_admin;
    if (!status.is_admin) { window.PosAuth.pendingStocktakes = 0; return; }
    const result = await API.pendingStocktakeCount();
    if (token === this._refreshToken && window.PosAuth.isAdmin)
      window.PosAuth.pendingStocktakes = result.count;
  },
  async login() {
    const password = await PosConfirm.input({
      title: "管理者登入", message: "請輸入管理者密碼", inputType: "password",
      async validate(value) {
        try {
          await API.authLogin(value);
          await window.PosAuthOps.refresh();
          return null;
        }
        catch (error) { return error.message; }
      },
    });
    if (password !== null) await this.refresh();
  },
  async logout() {
    if (!await PosConfirm.ask("確定登出管理者?")) return;
    await API.authLogout();
    await this.refresh();
  },
};

// 每個視窗各自回報活動，閒置是否逾時一律由後端共用身分判斷。
let posAuthLastTouch = -Infinity;
function posAuthReportActivity() {
  const now = Date.now();
  if (now - posAuthLastTouch < 60000) return;
  posAuthLastTouch = now;
  API.authTouch().catch(() => {});
}
document.addEventListener("pointerdown", posAuthReportActivity, true);
document.addEventListener("keydown", posAuthReportActivity, true);
