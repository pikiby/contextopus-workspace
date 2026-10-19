"""Literal UTF-8 workspace text, independent of OS locale/newline defaults."""


def read_utf8(path):
    """Decode source bytes without translating CRLF or standalone CR."""
    with path.open("r", encoding="utf-8", newline="") as stream:
        return stream.read()
