# ============================================================
# GS1 BARCODES
# ============================================================
# Medicine packs carry either a plain product barcode (EAN-8 / UPC-A /
# EAN-13 / GTIN-14) or a GS1 DataMatrix / GS1-128 code holding several
# Application Identifiers (AIs):
#
#   (01) GTIN          14 digits, fixed length
#   (17) expiry date   YYMMDD, fixed length (DD = 00 means end of month)
#   (10) batch / lot   up to 20 characters, variable length
#   (21) serial number up to 20 characters, variable length
#   (11) production date YYMMDD, fixed length
#   (30) count         up to 8 digits, variable length
#
# Variable-length fields end with the GS separator (ASCII 29) unless they
# are last. Scanners transmit GS as \x1d; some keyboard-wedge scanners send
# it as "<GS>", "{GS}" or "^]"; human-readable text uses "(01)...(17)...".
# Not every code carries every field: the parser returns what is present and
# never invents the rest.

import calendar
import re
from datetime import date

GS = "\x1d"
FIXED = {"01": 14, "02": 14, "11": 6, "13": 6, "15": 6, "17": 6, "20": 2}
VARIABLE = {"10": 20, "21": 20, "30": 8, "240": 30, "241": 30}
NAMES = {"01": "gtin", "02": "content_gtin", "10": "batch_number", "11": "production_date",
         "13": "packaging_date", "15": "best_before", "17": "expiry_date", "20": "variant",
         "21": "serial_number", "30": "count", "240": "additional_id", "241": "customer_part_number"}
DATE_AIS = {"11", "13", "15", "17"}
SYMBOLOGY_PREFIXES = ("]d2", "]C1", "]e0", "]Q3")


class BarcodeError(ValueError):
    pass


def gtin_check_digit(body: str) -> int:
    """GS1 mod-10 check digit for the digits before the check digit."""
    total = 0
    for position, digit in enumerate(reversed(body)):
        total += int(digit) * (3 if position % 2 == 0 else 1)
    return (10 - total % 10) % 10


def valid_gtin(value: str) -> bool:
    return (bool(re.fullmatch(r"\d{8}|\d{12,14}", value or ""))
            and gtin_check_digit(value[:-1]) == int(value[-1]))


def normalize_gtin(value: str | None) -> str | None:
    """Validate a GTIN-8/12/13/14 and return it as a 14-digit GTIN (the form
    GS1 codes carry), so product barcodes and DataMatrix codes match."""
    if value is None or not value.strip():
        return None
    digits = value.strip().replace(" ", "")
    if not valid_gtin(digits):
        raise BarcodeError("Invalid GTIN: expected 8, 12, 13 or 14 digits with a correct check digit")
    return gtin14(digits)


def gtin14(value: str) -> str:
    return value.zfill(14)


def _gs1_date(text: str) -> date:
    if not re.fullmatch(r"\d{6}", text):
        raise BarcodeError(f"Invalid GS1 date '{text}'")
    year, month, day = 2000 + int(text[:2]), int(text[2:4]), int(text[4:6])
    # GS1 rule: a year more than 49 years ahead belongs to the previous century.
    if year - date.today().year > 49:
        year -= 100
    if not 1 <= month <= 12:
        raise BarcodeError(f"Invalid GS1 date '{text}'")
    if day == 0:
        day = calendar.monthrange(year, month)[1]
    try:
        return date(year, month, day)
    except ValueError as error:
        raise BarcodeError(f"Invalid GS1 date '{text}'") from error


def _clean(raw: str) -> str:
    text = raw.strip()
    for prefix in SYMBOLOGY_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
    for token in ("<GS>", "{GS}", "^]", "\\x1d", "␝"):
        text = text.replace(token, GS)
    return text.strip(GS)


def _parse_parenthesized(text: str) -> list[tuple[str, str]]:
    parts = re.findall(r"\((\d{2,4})\)([^()]*)", text)
    if not parts or "".join(f"({a}){v}" for a, v in parts) != text.replace(GS, ""):
        raise BarcodeError("Unrecognised GS1 human-readable format")
    return [(ai, value.strip()) for ai, value in parts]


def _parse_element_string(text: str) -> list[tuple[str, str]]:
    elements, i = [], 0
    while i < len(text):
        if text[i] == GS:
            i += 1
            continue
        ai = next((a for a in (text[i:i + 2], text[i:i + 3]) if a in FIXED or a in VARIABLE), None)
        if ai is None:
            raise BarcodeError(f"Unsupported GS1 application identifier at position {i + 1}")
        i += len(ai)
        if ai in FIXED:
            value = text[i:i + FIXED[ai]]
            if len(value) != FIXED[ai]:
                raise BarcodeError(f"GS1 field ({ai}) is too short")
            i += FIXED[ai]
        else:
            end = text.find(GS, i)
            end = len(text) if end == -1 else end
            value = text[i:end]
            if not value or len(value) > VARIABLE[ai]:
                raise BarcodeError(f"GS1 field ({ai}) has an invalid length")
            i = end
        elements.append((ai, value))
    return elements


def parse(raw: str) -> dict:
    """Parse a scanned code. Returns the fields present:
    {format, gtin, batch_number, expiry_date, serial_number, ..., raw}."""
    if not raw or not raw.strip():
        raise BarcodeError("Empty barcode")
    text = _clean(raw)
    if len(text) > 200:
        raise BarcodeError("Barcode is too long")

    if re.fullmatch(r"\d{8}|\d{12,14}", text):
        if not valid_gtin(text):
            raise BarcodeError("Invalid product barcode check digit")
        return {"format": "GTIN", "gtin": gtin14(text), "raw": raw.strip()}

    elements = _parse_parenthesized(text) if text.startswith("(") else _parse_element_string(text)
    result: dict = {"format": "GS1", "raw": raw.strip()}
    for ai, value in elements:
        name = NAMES[ai]
        if ai in DATE_AIS:
            result[name] = _gs1_date(value)
        elif ai in ("01", "02"):
            if not valid_gtin(value):
                raise BarcodeError("Invalid GTIN check digit in GS1 code")
            result[name] = value
        else:
            result[name] = value
    if "gtin" not in result and "content_gtin" not in result:
        raise BarcodeError("GS1 code has no product GTIN (01)")
    return result
