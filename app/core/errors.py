class AppError(Exception):
    """Base for every failure the API reports rather than crashes on.

    A subclass declares its status and its machine-readable code *as a class
    attribute*, so the handler never has to know which types exist.
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code

    def __init_subclass__(cls, status_code: int, code: str, **kw):
        super().__init_subclass__(**kw)
        cls.status_code = status_code
        cls.code = code
