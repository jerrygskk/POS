import unittest
from lib.refund_rules import allocate_paid, refund_limit


class RefundRulesTest(unittest.TestCase):
    def test_discount_allocation_and_equal_remainders_are_exact(self):
        lines = [{"qty": 1,"unit_price": 100,"discount":0} for _ in range(3)]
        self.assertEqual(allocate_paid(lines, 298), [100,99,99])
        self.assertEqual(allocate_paid(lines, 0), [0,0,0])
        self.assertEqual(allocate_paid([{"qty":1,"unit_price":0,"discount":0}], 0), [0])

    def test_last_piece_returns_all_remaining_money(self):
        self.assertEqual(refund_limit(199,2,2,199,1),100)
        self.assertEqual(refund_limit(199,2,1,99,1),99)
        self.assertEqual(refund_limit(1,2,2,1,1),1)
