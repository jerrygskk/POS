"""會員名單:電話正規化、末三碼／完整查詢、多對多、停用、刪除權限。"""
from base import FacadeTestCase
from lib.application_errors import ApplicationError
from lib.member_service import normalize_phone


class TestNormalizePhone(FacadeTestCase):
    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("0912-000 111"), "0912000111")
        self.assertEqual(normalize_phone("０９１２０００１１１"), "0912000111")
        self.assertIsNone(normalize_phone("912000111"))
        self.assertIsNone(normalize_phone("0812000111"))
        self.assertIsNone(normalize_phone(""))


class TestMembers(FacadeTestCase):
    def _create(self, name, *phones):
        return self.invoke("members.create", {"name": name, "phones": list(phones)})["member_id"]

    def test_lookup_last3_and_full(self):
        a = self._create("王小明", "0912000111")
        b = self._create("李小華", "0933000111")
        self.assertEqual({r["member_id"] for r in self.invoke("members.lookup", {"query": "111"})}, {a, b})
        rows = self.invoke("members.lookup", {"query": "0912-000-111"})
        self.assertEqual(rows, [{"member_id": a, "name": "王小明", "phone": "0912000111", "phones": ["0912000111"]}])

    def test_lookup_returns_all_phones_for_editing(self):
        self._create("王小明", "0988000111", "0912000222")
        rows = self.invoke("members.lookup", {"query": "111"})
        self.assertEqual((rows[0]["phone"], rows[0]["phones"]), ("0988000111", ["0912000222", "0988000111"]))

    def test_lookup_one_member_two_phones_listed_once(self):
        self._create("王小明", "0912000111", "0988000111")
        self.assertEqual(len(self.invoke("members.lookup", {"query": "111"})), 1)

    def test_same_phone_two_members(self):
        self._create("王小明", "0912000111")
        self._create("王太太", "0912000111")
        self.assertEqual(len(self.invoke("members.lookup", {"query": "0912000111"})), 2)

    def test_lookup_bad_length(self):
        error = self.assert_application_error("validation_error", "members.lookup", {"query": "12"})
        self.assertEqual(error.message, "請輸入電話末三碼或完整 10 碼")

    def test_deleted_member_not_found(self):
        mid = self._create("王小明", "0912000111")
        self.invoke("members.delete", {"member_id": mid})
        self.assertEqual(self.invoke("members.lookup", {"query": "111"}), [])
        self.assertEqual(self.invoke("members.list", {"query": ""}), [])
        self.assert_application_error("validation_error", "members.delete", {"member_id": mid})

    def test_employee_cannot_delete(self):
        mid = self._create("王小明", "0912000111")
        self.facade.session.logout()
        self.assert_application_error("forbidden", "members.delete", {"member_id": mid})
        # 員工仍可新增、修改
        self.invoke("members.update", {"member_id": mid, "name": "王大明", "phones": ["0912000111"]})
        self._create("李小華", "0933000111")

    def test_update_replaces_phones(self):
        mid = self._create("王小明", "0912000111")
        self.invoke("members.update", {"member_id": mid, "name": " 王大明 ", "phones": ["0988000222", "0988-000-222"]})
        rows = self.invoke("members.list", {"query": ""})
        self.assertEqual([(r["name"], r["phones"]) for r in rows], [("王大明", ["0988000222"])])

    def test_create_rejects_empty_name_and_bad_phone(self):
        self.assert_application_error("validation_error", "members.create", {"name": "  ", "phones": ["0912000111"]})
        self.assert_application_error("validation_error", "members.create", {"name": "王小明", "phones": []})
        error = self.assert_application_error("validation_error", "members.create", {"name": "王小明", "phones": ["12345"]})
        self.assertEqual(error.message, "電話格式不正確：12345")

    def test_list_search_by_name_or_phone(self):
        self._create("王小明", "0912000111")
        self._create("李小華", "0933000222")
        self.assertEqual([r["name"] for r in self.invoke("members.list", {"query": "小華"})], ["李小華"])
        self.assertEqual([r["name"] for r in self.invoke("members.list", {"query": "0111"})], ["王小明"])

    def test_phone_owners_excludes_self(self):
        a = self._create("王小明", "0912000111")
        self._create("王太太", "0912000111")
        owners = self.invoke("members.phone_owners", {"phones": ["0912000111", "0999000999"], "exclude_member_id": a})
        self.assertEqual(owners, [{"phone": "0912000111", "names": ["王太太"]}])
