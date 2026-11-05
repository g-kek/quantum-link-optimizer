import re


def transport_session(logical_id, path):
    if not isinstance(logical_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,100}", logical_id
    ):
        raise ValueError("invalid logical session")
    if path not in {"A", "B"}:
        raise ValueError("invalid path")
    return f"{logical_id}/{path}"


def logical_session(transport_id, path):
    logical_id, separator, bound_path = transport_id.rpartition("/")
    if not separator or bound_path != path:
        raise ValueError("unbound transport session")
    if transport_session(logical_id, path) != transport_id:
        raise ValueError("invalid transport session")
    return logical_id
