class ApplicationError(Exception):
    code = "internal_error"
    default_message = "系統發生未預期錯誤"

    def __init__(self, message=None, details=None):
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class ValidationError(ApplicationError):
    code = "validation_error"
    default_message = "輸入資料不正確"


class NotFoundError(ApplicationError):
    code = "not_found"
    default_message = "找不到指定資料"


class ConflictError(ApplicationError):
    code = "conflict"
    default_message = "資料狀態衝突"


class DatabaseError(ApplicationError):
    code = "database_error"
    default_message = "資料庫操作失敗"


class InternalError(ApplicationError):
    code = "internal_error"
    default_message = "系統發生未預期錯誤"
