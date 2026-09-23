# ============================================================
# SHARED REQUEST FIELD TYPES
# ============================================================

from typing import Annotated

from pydantic import StringConstraints

Name150 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
Short50 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
Code50 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Code100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Text255 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Phone = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50, pattern=r"^[0-9+()\-\s]*$")]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=150, pattern=r"^$|^[^@\s]+@[^@\s]+\.[^@\s]+$"),
]


def blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
