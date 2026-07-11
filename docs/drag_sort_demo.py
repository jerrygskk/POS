# -*- coding: utf-8 -*-
"""
拖拉排序表格 —— 可攜出的獨立範例（抽自 project_police）
=====================================================
來源對照（原專案）：
  - tabs/tab_settings.py   ：_NoFocusDelegate / _SeqEditDelegate / _RowDragFilter、
                             表格建置、_moveRow / _onSeqItemChanged、_renderSortTable / _saveSort
  - ui_utils/settings_dialogs.py：_parseSeqMoveTarget（序號輸入驗證，純函式）

功能：
  1. 整列拖拉排序（col0 的 ⠿ 把手，其實整列都可拖）
  2. 序號欄（col1）單擊進行內編輯，打數字＋Enter 直接搬到該位置
  3. 順序改動只動記憶體 rows，亮「儲存排序」鈕；按下才透過 callback 寫回

依賴：只需 PySide6。執行 `python drag_sort_demo.py` 可直接看效果（假資料、不碰 DB）。

接回自己專案的資料端：
  - 資料表加一欄 sort_order INTEGER，讀取時 ORDER BY sort_order
  - on_save callback 收到「目前順序的 rows」，依序重寫 sort_order=1..N：
        for i, row in enumerate(rows, start=1):
            conn.execute("UPDATE 表 SET sort_order=? WHERE id=?", (i, row[0]))
"""

import sys

from PySide6.QtCore import Qt, QObject, QEvent, QRegularExpression
from PySide6.QtGui import QColor, QPen, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QStyledItemDelegate, QStyle, QAbstractItemView,
)

HANDLE_COL = 0   # 拖拉把手欄（⠿）
SEQ_COL    = 1   # 序號欄（顯示目前排序位置，可打數字搬移）


# ────────────────────────────────────────────────────────────────
# 純函式：序號輸入驗證（可單測）
# ────────────────────────────────────────────────────────────────
def parse_seq_move_target(text, row_count):
    """回傳 0-based 目標索引；不合法（非數字／超出 1~row_count）回 None。"""
    text = (text or "").strip()
    if not text.isdigit():
        return None
    n = int(text)
    if not (1 <= n <= row_count):
        return None
    return n - 1


# ────────────────────────────────────────────────────────────────
# Delegate / Event filter
# ────────────────────────────────────────────────────────────────
class NoFocusDelegate(QStyledItemDelegate):
    """移除「目前儲存格」焦點外框（Windows 樣式點擊後會在該格畫框）。
    僅去焦點框，保留列選取底色（拖拉排序需要 currentRow）。"""
    def paint(self, painter, option, index):
        if option.state & QStyle.State_HasFocus:
            option.state &= ~QStyle.State_HasFocus
        super().paint(painter, option, index)


class SeqEditDelegate(NoFocusDelegate):
    """序號欄專用 delegate：editor 限定只能打數字；
    paint 疊一層淺色虛線框，常駐提示「這格可以點來改」。"""

    def createEditor(self, parent, option, index):
        editor = QLineEdit(parent)
        editor.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"[0-9]*"), editor))
        editor.setAlignment(Qt.AlignCenter)
        # 若全域 stylesheet 對 QLineEdit 有 padding，固定列高下可能裁到下緣 → 歸零
        editor.setStyleSheet("padding: 0px; margin: 0px;")
        return editor

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        painter.save()
        pen = QPen(QColor("#9bb0c9"))
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawRect(option.rect.adjusted(2, 2, -3, -3))
        painter.restore()


class RowDragFilter(QObject):
    """攔截 QTableWidget viewport 的 Drop 事件，實作整列拖拉
    （Qt InternalMove 預設只移「格」不移「列」，會錯位）。"""
    def __init__(self, tbl, callback):
        super().__init__(tbl)
        self._tbl = tbl
        self._cb  = callback  # callback(src_row, dst_row)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Drop:
            src = self._tbl.currentRow()
            dst = self._tbl.rowAt(int(event.position().y()))
            if dst < 0:
                dst = self._tbl.rowCount() - 1
            if src >= 0 and src != dst:
                self._cb(src, dst)
            return True   # 阻止 Qt 的預設錯位行為
        return False


# ────────────────────────────────────────────────────────────────
# 可重用元件：拖拉排序表格
# ────────────────────────────────────────────────────────────────
class SortableTable(QWidget):
    """rows：list of [id, name, ...]（id 供存檔識別；顯示欄可自行擴充）。
    on_save(rows)：按「儲存排序」時回呼，由呼叫端寫 DB。
    editable：False 時關閉拖拉與序號編輯（權限 gate 範例）。"""

    def __init__(self, headers=("", "序號", "名稱"), on_save=None, parent=None):
        super().__init__(parent)
        self.rows    = []
        self.dirty   = False
        self.on_save = on_save
        self._editable = True

        tbl = QTableWidget(0, len(headers))
        tbl.setHorizontalHeaderLabels(list(headers))
        tbl.verticalHeader().setVisible(False)
        tbl.setSelectionBehavior(QTableWidget.SelectRows)
        tbl.setSelectionMode(QTableWidget.SingleSelection)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setDefaultSectionSize(36)
        tbl.setItemDelegate(NoFocusDelegate(tbl))

        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(HANDLE_COL, QHeaderView.Fixed)
        tbl.setColumnWidth(HANDLE_COL, 36)
        hdr.setSectionResizeMode(SEQ_COL, QHeaderView.Fixed)
        tbl.setColumnWidth(SEQ_COL, 64)
        for c in range(2, len(headers)):
            hdr.setSectionResizeMode(c, QHeaderView.Stretch)

        # 拖拉排序：event filter 攔 Drop，改成整列記憶體搬移
        tbl.setDragDropMode(QAbstractItemView.InternalMove)
        tbl.setDefaultDropAction(Qt.MoveAction)
        tbl.setAutoScrollMargin(90)
        self._drag_filter = RowDragFilter(tbl, self._move_row)  # 存 self 防 GC
        tbl.viewport().installEventFilter(self._drag_filter)

        # 序號欄：單擊即行內編輯；delegate 限定數字
        self._seq_delegate = SeqEditDelegate(tbl)                # 存 self 防 GC
        tbl.setItemDelegateForColumn(SEQ_COL, self._seq_delegate)
        tbl.cellClicked.connect(self._on_cell_clicked)
        tbl.itemChanged.connect(self._on_seq_item_changed)

        btn_save = QPushButton("儲存排序")
        btn_save.setEnabled(False)
        btn_save.clicked.connect(self._save)

        self.table = tbl
        self.btn_save = btn_save

        lay = QVBoxLayout(self)
        lay.addWidget(tbl)
        bar = QHBoxLayout()
        bar.addStretch()
        bar.addWidget(btn_save)
        lay.addLayout(bar)

    # ── 外部 API ────────────────────────────────────────────────
    def set_rows(self, rows):
        """載入資料（如 SELECT ... ORDER BY sort_order），清 dirty、重繪。"""
        self.rows  = [list(r) for r in rows]
        self.dirty = False
        self.btn_save.setEnabled(False)
        self._render()

    def set_editable(self, editable):
        """權限 gate：按鈕 setEnabled 擋不住點擊/拖拉路徑，須一併切掉。"""
        self._editable = editable
        self.table.setDragDropMode(
            QAbstractItemView.InternalMove if editable
            else QAbstractItemView.NoDragDrop)
        if not editable:
            self.btn_save.setEnabled(False)

    # ── 內部 ────────────────────────────────────────────────────
    def _move_row(self, src, dst):
        """共用搬移邏輯：記憶體 rows 重排＋設 dirty＋亮儲存鈕＋重繪＋選取目標列。
        拖拉與序號編輯共用此路徑。"""
        if not self._editable:
            return
        self.rows.insert(dst, self.rows.pop(src))
        self.dirty = True
        self.btn_save.setEnabled(True)
        self._render()
        self.table.selectRow(dst)

    def _on_cell_clicked(self, row, col):
        """序號欄單擊＝進入行內編輯（虛線框提示「可點改位置」）。"""
        if col != SEQ_COL or not self._editable:
            return
        item = self.table.item(row, SEQ_COL)
        if item:
            self.table.editItem(item)

    def _on_seq_item_changed(self, item):
        """序號欄編輯完成（Enter／離焦）：合法則搬移，不合法安靜跳回原數字。"""
        if item.column() != SEQ_COL or not self._editable:
            return
        row    = item.row()
        target = parse_seq_move_target(item.text(), len(self.rows))
        if target is None:
            self.table.blockSignals(True)
            item.setText(str(row + 1))
            self.table.blockSignals(False)
            return
        if target == row:
            return
        self._move_row(row, target)

    def _render(self):
        """依記憶體 rows 重繪整張表。blockSignals：重建時不讓 itemChanged
        誤判成使用者手動改序號（原專案踩過的雷）。"""
        tbl = self.table
        # 保留捲動位置（重繪不跳回頂端）
        vbar = tbl.verticalScrollBar()
        pos  = vbar.value()
        tbl.blockSignals(True)
        try:
            tbl.setRowCount(0)
            for r, row in enumerate(self.rows):
                tbl.insertRow(r)
                # col0 把手
                handle = QTableWidgetItem("⠿")
                handle.setTextAlignment(Qt.AlignCenter)
                handle.setForeground(QColor("#8e8e93"))
                handle.setToolTip("按住可拖拉整列以調整排序")
                tbl.setItem(r, HANDLE_COL, handle)
                # col1 序號＝目前列位置（r+1），非資料 id
                seq = QTableWidgetItem(str(r + 1))
                seq.setTextAlignment(Qt.AlignCenter)
                seq.setBackground(QColor("#F5F7FA"))
                tbl.setItem(r, SEQ_COL, seq)
                # col2+ 資料欄（row[1:]）
                for c, val in enumerate(row[1:], start=2):
                    it = QTableWidgetItem("" if val is None else str(val))
                    it.setTextAlignment(Qt.AlignCenter)
                    tbl.setItem(r, c, it)
        finally:
            tbl.blockSignals(False)
        vbar.setValue(pos)

    def _save(self):
        """呼叫端在 on_save 內依 rows 目前順序重寫 sort_order=1..N。"""
        if self.on_save:
            self.on_save(self.rows)
        self.dirty = False
        self.btn_save.setEnabled(False)


# ────────────────────────────────────────────────────────────────
# Demo（假資料，不碰 DB）
# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)

    def fake_save(rows):
        # 實務上這裡開 DB 連線：
        #   for i, row in enumerate(rows, start=1):
        #       conn.execute("UPDATE Ref_Personnel SET sort_order=? WHERE staff_id=?",
        #                    (i, row[0]))
        #   conn.commit()
        print("儲存順序：", [r[0] for r in rows])

    w = SortableTable(headers=("", "序號", "姓名"), on_save=fake_save)
    w.set_rows([
        ("A01", "王小明"),
        ("A02", "陳大文"),
        ("A03", "林測試"),
        ("A04", "張範例"),
        ("A05", "李虛構"),
    ])
    w.setWindowTitle("拖拉排序 Demo")
    w.resize(420, 360)
    w.show()
    sys.exit(app.exec())
