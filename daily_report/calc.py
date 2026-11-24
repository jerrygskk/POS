"""日報表計算：算法照老闆現用的 Excel（日營業額v5.xlsx），每月 1 日重新累計。"""
import calendar
import datetime

WEEKDAYS = "一二三四五六日"


def _sum(lines):
    return sum(line["amount"] or 0 for line in lines)


def build_month(year, month, pos_days, entries):
    """回傳當月每一天的報表列（list，索引 0＝1 日）。

    尚未立帳的日子營業額、個績、當日餘額為 None；累計時營業額當 0，手填數字照常加總。
    """
    rows = []
    cum = {"revenue": 0, "expense": 0, "cash": 0, "goods": 0}
    cum_emp = [0] * 6
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        date = datetime.date(year, month, day)
        key = date.isoformat()
        pos = pos_days.get(key)
        entry = entries.get(key) or {"weather": "", "payable": None,
                                     "goods": [], "owed": [], "note": [], "expense": []}
        revenue = pos["revenue"] if pos else None
        employees = list(pos["employees"]) if pos else None
        expense = _sum(entry["expense"])
        goods = _sum(entry["goods"])
        goods_before = cum["goods"]
        cum["revenue"] += revenue or 0
        cum["expense"] += expense
        cum["goods"] += goods
        # 累計現流＝已立帳營業額合計－全部開支合計（Excel 總表 H 欄逐日加總當日餘額）
        cum["cash"] += (revenue or 0) - expense
        if employees:
            cum_emp = [a + b for a, b in zip(cum_emp, employees)]
        # 個績只顯示當月到這天為止有過業績的員工：沒班、還沒到職的代號留白，比照紙本只寫有人的格子
        active = [n > 0 for n in cum_emp]
        balance = None if revenue is None else revenue - expense
        rows.append({
            "date": key,
            "weekday": "星期" + WEEKDAYS[date.weekday()],
            "closed": pos is not None,
            "has_data": pos is not None or key in entries,
            "revenue": revenue,
            "cum_revenue": cum["revenue"],
            "employees": employees,
            "cum_employees": list(cum_emp),
            "emp_active": active,
            "expense": expense,
            "cum_expense": cum["expense"],
            "balance": balance,
            "cum_cash": cum["cash"],
            "goods": goods,
            "cum_goods": goods_before,   # 累計貨款＝到前一天（Excel G5）
            "goods_total": cum["goods"],  # 總貨款＝含當天（Excel G8、總表 J 欄）
            "note_total": _sum(entry["note"]),
            "remain": balance,            # 尚餘現流＝當日餘額（Excel M9=M3）
            "entry": entry,
        })
    return rows
