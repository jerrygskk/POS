"""退款分攤只用整數，避免浮點誤差使累計退款超過實付。"""


def allocate_paid(lines, total):
    """按折扣後金額分攤應收；同餘數依明細原順序補元。"""
    weights = [r["qty"] * r["unit_price"] - r["discount"] for r in lines]
    whole = sum(weights)
    if not whole:
        return [0] * len(lines)
    parts = [divmod(total * w, whole) for w in weights]
    paid = [p[0] for p in parts]
    for i in sorted(range(len(parts)), key=lambda i: (-parts[i][1], i))[:total - sum(paid)]:
        paid[i] += 1
    return paid


def refund_limit(paid, original_qty, remaining_qty, remaining_amount, qty):
    if qty == remaining_qty:
        return remaining_amount
    return min(remaining_amount, (paid * qty * 2 + original_qty) // (2 * original_qty))
