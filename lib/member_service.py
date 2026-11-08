"""會員名單:收銀確認會員身分用,不含點數。電話與會員多對多;刪除＝停用。"""
import re
import unicodedata

from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ForbiddenError, ValidationError
from lib.product_rules import allow_keys, is_int

PHONE_RE = re.compile(r"09\d{8}")
LOOKUP_LENGTH_MESSAGE = "請輸入電話末三碼或完整 10 碼"


def normalize_phone(text):
    """全形轉半形、去空白與橫線;符合 09 開頭 10 碼才回傳,否則 None。"""
    if not isinstance(text, str):
        return None
    value = re.sub(r"[\s\-－–]", "", unicodedata.normalize("NFKC", text))
    return value if PHONE_RE.fullmatch(value) else None


def phone_pattern(text):
    """查詢字串只留數字:3 碼比末三碼、10 碼比完整;回傳 LIKE 樣式。"""
    digits = re.sub(r"\D", "", unicodedata.normalize("NFKC", text))
    if len(digits) == 3:
        return "%" + digits
    if len(digits) == 10:
        return digits
    raise ValidationError(LOOKUP_LENGTH_MESSAGE)


def _text(payload, key):
    if not isinstance(payload.get(key), str):
        raise ValidationError(f"{key} 必須是字串")
    return payload[key]


def _member_id(payload):
    if not is_int(payload.get("member_id")):
        raise ValidationError("member_id 必須是整數")
    return payload["member_id"]


def _phones(payload):
    phones = payload.get("phones")
    if not isinstance(phones, list) or not phones:
        raise ValidationError("請至少輸入一支電話")
    clean = []
    for raw in phones:
        phone = normalize_phone(raw)
        if phone is None:
            raise ValidationError(f"電話格式不正確：{raw}")
        if phone not in clean:
            clean.append(phone)
    return clean


def _member_body(payload):
    name = _text(payload, "name").strip()
    if not name:
        raise ValidationError("請輸入會員名稱")
    return {"name": name, "phones": _phones(payload)}


def _validate(action, payload):
    allowed = {"members.lookup": {"query"}, "members.list": {"query"},
               "members.create": {"name", "phones"}, "members.update": {"member_id", "name", "phones"},
               "members.phone_owners": {"phones", "exclude_member_id"},
               "members.delete": {"member_id"}}[action]
    allow_keys(payload, allowed)
    if action == "members.lookup":
        return {"pattern": phone_pattern(_text(payload, "query"))}
    if action == "members.list":
        return {"query": _text(payload, "query").strip()}
    if action == "members.create":
        return _member_body(payload)
    if action == "members.update":
        return {"member_id": _member_id(payload), **_member_body(payload)}
    if action == "members.phone_owners":
        if "exclude_member_id" not in payload:
            raise ValidationError("exclude_member_id 為必填")
        exclude = payload.get("exclude_member_id")
        if exclude is not None and not is_int(exclude):
            raise ValidationError("exclude_member_id 必須是整數")
        return {"phones": _phones(payload), "exclude": exclude}
    return {"member_id": _member_id(payload)}


class MemberRepository(BaseRepository):
    def lookup(self, pattern):
        # 同一位會員多支電話都符合時只列一次(取號碼最小的那支)
        return self.all(
            "SELECT m.member_id, m.name, MIN(p.phone) phone FROM Member m JOIN MemberPhone p ON p.member_id=m.member_id "
            "WHERE m.active=1 AND p.phone LIKE ? GROUP BY m.member_id ORDER BY m.name, m.member_id", (pattern,))

    def search(self, query):
        escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")  # 名稱裡的 % _ 照字面比對
        like = f"%{escaped}%"
        return self.all(
            "SELECT m.member_id, m.name, m.ts FROM Member m WHERE m.active=1 AND (?='' OR m.name LIKE ? ESCAPE '!' OR EXISTS "
            "(SELECT 1 FROM MemberPhone p WHERE p.member_id=m.member_id AND p.phone LIKE ? ESCAPE '!')) ORDER BY m.member_id DESC",
            (query, like, like))

    def phones_of(self, member_ids):
        if not member_ids:
            return {}
        marks = ",".join("?" * len(member_ids))
        out = {}
        for row in self.all(f"SELECT member_id, phone FROM MemberPhone WHERE member_id IN ({marks}) ORDER BY phone",
                            member_ids):
            out.setdefault(row["member_id"], []).append(row["phone"])
        return out

    def is_active(self, member_id):
        return self.one("SELECT 1 FROM Member WHERE member_id=? AND active=1", (member_id,)) is not None

    def create(self, name):
        return self.execute("INSERT INTO Member(name) VALUES(?)", (name,)).lastrowid

    def rename(self, member_id, name):
        self.execute("UPDATE Member SET name=? WHERE member_id=?", (name, member_id))

    def set_phones(self, member_id, phones):
        self.execute("DELETE FROM MemberPhone WHERE member_id=?", (member_id,))
        for phone in phones:
            self.execute("INSERT INTO MemberPhone(member_id, phone) VALUES(?,?)", (member_id, phone))

    def deactivate(self, member_id):
        self.execute("UPDATE Member SET active=0 WHERE member_id=?", (member_id,))

    def owners(self, phone, exclude):
        return [row["name"] for row in self.all(
            "SELECT m.name FROM Member m JOIN MemberPhone p ON p.member_id=m.member_id "
            "WHERE m.active=1 AND p.phone=? AND m.member_id IS NOT ? ORDER BY m.name", (phone, exclude))]


class MemberService:
    def __init__(self, repository):
        self.repo = repository

    def lookup(self, pattern):
        rows = self.repo.lookup(pattern)
        phones = self.repo.phones_of([row["member_id"] for row in rows])  # 收銀選會員時可直接修改
        return [{**dict(row), "phones": phones.get(row["member_id"], [])} for row in rows]

    def list(self, query):
        rows = self.repo.search(query)
        phones = self.repo.phones_of([row["member_id"] for row in rows])
        return [{"member_id": row["member_id"], "name": row["name"], "phones": phones.get(row["member_id"], []),
                 "ts": row["ts"]} for row in rows]

    def create(self, body):
        member_id = self.repo.create(body["name"])
        self.repo.set_phones(member_id, body["phones"])
        return {"member_id": member_id}

    def _require_active(self, member_id):
        if not self.repo.is_active(member_id):
            raise ValidationError("會員不存在或已刪除")

    def update(self, body):
        self._require_active(body["member_id"])
        self.repo.rename(body["member_id"], body["name"])
        self.repo.set_phones(body["member_id"], body["phones"])
        return {}

    def delete(self, member_id):
        self._require_active(member_id)
        self.repo.deactivate(member_id)
        return {}

    def phone_owners(self, phones, exclude):
        out = []
        for phone in phones:
            names = self.repo.owners(phone, exclude)
            if names:
                out.append({"phone": phone, "names": names})
        return out


class MemberFacade(BaseFacade):
    ACTIONS = {"members.lookup", "members.list", "members.create", "members.update",
               "members.phone_owners", "members.delete"}
    ERROR_MESSAGE = "不支援的會員操作"

    def __init__(self, db_path, session=None):
        super().__init__(db_path)
        self.session = session

    def _is_admin(self):
        return self.session is not None and self.session.is_admin()

    def _prepare_payload(self, action, payload):
        if action == "members.delete" and not self._is_admin():
            raise ForbiddenError()
        return _validate(action, payload)

    def _dispatch(self, action, payload, connection):
        service = MemberService(MemberRepository(connection))
        if action == "members.lookup": return service.lookup(payload["pattern"])
        if action == "members.list": return service.list(payload["query"])
        if action == "members.create": return service.create(payload)
        if action == "members.update": return service.update(payload)
        if action == "members.phone_owners": return service.phone_owners(payload["phones"], payload["exclude"])
        # 確認框之後才送出:執行當下在交易內再檢查一次權限
        if not self._is_admin(): raise ForbiddenError()
        return service.delete(payload["member_id"])
