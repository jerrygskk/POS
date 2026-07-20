from lib.application_errors import (
    ConflictError,
    DatabaseError,
    InternalError,
    NotFoundError,
    ValidationError,
)


def application_error_response(exc):
    if isinstance(exc, ValidationError):
        return 422, exc.message
    if isinstance(exc, NotFoundError):
        return 404, exc.message
    if isinstance(exc, ConflictError):
        return 409, exc.message
    if isinstance(exc, DatabaseError):
        return 500, DatabaseError.default_message
    return 500, InternalError.default_message
