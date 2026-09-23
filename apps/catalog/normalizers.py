"""Pure functions that turn raw text into comparable attributes. No database access.

Every function returns the parsed value plus a list of Finding objects describing anything
missing, assumed or contradictory, so the caller can persist them as Issues with provenance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from apps.core import rules

KEY_FIELDS = ("category", "position", "fitment", "dims", "oe_numbers")

# Every code normalize_record() can emit: these are recomputed whenever a row is re-normalized.
NORMALIZER_CODES = {
    "UNKNOWN_CATEGORY", "INVALID_VALUE", "POSITION_INCONSISTENT", "MISSING_POSITION",
    "FITMENT_UNPARSED", "MISSING_FITMENT", "UNIT_ASSUMED", "MISSING_DIMS", "MISSING_OE",
    "CURRENCY_INFERRED", "MISSING_PRICE", "MISSING_CURRENCY", "MISSING_MOQ", "AMBIGUOUS_DATE",
    "MISSING_QUOTE_DATE", "MISSING_PART_NO",
}

ISO_CURRENCIES = {
    "USD", "EUR", "GBP", "CAD", "MXN", "CNY", "RMB", "JPY", "AUD", "CHF", "HKD", "SGD",
    "KRW", "INR", "BRL", "SEK", "NOK", "DKK", "PLN", "TWD", "NZD",
}
CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "CNY", "C$": "CAD"}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # error / warning / info
    field: str
    message: str
    details: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------- text helpers


def to_text(value) -> str:
    """Canonical text form of a cell value (what we store as the raw value)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.hour == value.minute == value.second == 0:
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value).strip()


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+|[一-鿿]+", text.lower())


def normalize_header(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9一-鿿]+", str(text or "").lower()))


def normalize_number(text: str) -> str:
    """Search/compare form of a part number: upper case, alphanumerics only."""
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _find_phrase(tokens: list[str], phrase_tokens: list[str]) -> int:
    n = len(phrase_tokens)
    for i in range(len(tokens) - n + 1):
        if tokens[i : i + n] == phrase_tokens:
            return i
    return -1


# ---------------------------------------------------------------------------- category & position


def position_words() -> dict[str, list[list[str]]]:
    return {
        pos: [words(p) for p in phrases]
        for pos, phrases in rules.synonyms()["positions"].items()
    }


def position_from_text(text: str) -> str | None:
    """'left'/'right' if the text names exactly one side, else None."""
    tokens = words(text)
    found = {
        pos
        for pos, phrases in position_words().items()
        for p in phrases
        if p and _find_phrase(tokens, p) >= 0
    }
    return found.pop() if len(found) == 1 else None


def position_from_sku(part_no: str) -> str | None:
    """Trailing L/R/LH/RH preceded by a digit or separator: A-03-L, B03L, 12345-RH."""
    m = re.search(r"(?:[0-9\-_ /.])(LH|RH|L|R)$", str(part_no or "").strip().upper())
    if not m:
        return None
    return "left" if m.group(1).startswith("L") else "right"


def category_from_name(name: str) -> tuple[str | None, list[str]]:
    """Longest synonym phrase wins, so 'Air Filter Housing Cap' is never read as the housing.

    Returns (category, ambiguous_candidates).
    """
    tokens = words(name)
    # Position words would otherwise split phrases ("Side Grille, Left side").
    for phrases in position_words().values():
        for p in phrases:
            idx = _find_phrase(tokens, p)
            while idx >= 0 and p:
                del tokens[idx : idx + len(p)]
                idx = _find_phrase(tokens, p)
    best: dict[str, int] = {}
    for cat, spec in rules.synonyms()["categories"].items():
        for phrase in spec["phrases"]:
            pt = words(phrase)
            if pt and _find_phrase(tokens, pt) >= 0:
                best[cat] = max(best.get(cat, 0), len(pt))
    if not best:
        return None, []
    top = max(best.values())
    winners = sorted(c for c, n in best.items() if n == top)
    if len(winners) > 1:
        return None, winners
    return winners[0], []


def is_sided(category: str | None) -> bool:
    if not category:
        return False
    return bool(rules.synonyms()["categories"].get(category, {}).get("sided"))


def category_label(category: str | None) -> str:
    if not category:
        return ""
    return rules.synonyms()["categories"].get(category, {}).get("label", category)


# ---------------------------------------------------------------------------- fitment

YEAR_RANGE = re.compile(
    r"\b((?:19|20)\d{2})\s*(?:-|–|~|to)\s*((?:19|20)\d{2}|present|current|up)\b"
    r"|\b((?:19|20)\d{2})\s*\+",
    re.I,
)
SINGLE_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass(frozen=True)
class Fitment:
    make: str = ""
    model: str = ""
    year_from: int | None = None
    year_to: int | None = None

    @property
    def key(self) -> str:
        return f"{self.make.upper()}|{self.model.upper()}"

    @property
    def label(self) -> str:
        parts = [self.make, self.model]
        if self.year_from:
            parts.append(f"{self.year_from}-{self.year_to or ''}")
        return " ".join(p for p in parts if p)


def parse_fitment(text: str) -> tuple[Fitment | None, list[Finding]]:
    text = (text or "").strip()
    if not text:
        return None, []
    findings: list[Finding] = []
    year_from = year_to = None
    rest = text
    m = YEAR_RANGE.search(text)
    if m:
        if m.group(1):
            year_from = int(m.group(1))
            end = m.group(2)
            year_to = int(end) if end.isdigit() else None
        else:
            year_from = int(m.group(3))
        rest = (text[: m.start()] + " " + text[m.end() :]).strip()
    else:
        m1 = SINGLE_YEAR.search(text)
        if m1:
            year_from = year_to = int(m1.group(1))
            rest = (text[: m1.start()] + " " + text[m1.end() :]).strip()

    make = ""
    lowered = rest.lower()
    for candidate in sorted(rules.synonyms()["makes"], key=len, reverse=True):
        idx = lowered.find(candidate.lower())
        if idx >= 0:
            make = candidate
            rest = (rest[:idx] + " " + rest[idx + len(candidate) :]).strip()
            break
    model = re.sub(r"\s+", " ", re.sub(r"[,;()]+", " ", rest)).strip()
    if not make:
        findings.append(
            Finding("FITMENT_UNPARSED", "info", "fitment", f"未识别车型品牌：{text}")
        )
    return Fitment(make, model, year_from, year_to), findings


# ---------------------------------------------------------------------------- dimensions

DIMS = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*[x×X*]\s*(\d+(?:[.,]\d+)?)\s*[x×X*]\s*(\d+(?:[.,]\d+)?)"
    r"\s*(cm|mm|m|in|inch|inches|\"|厘米|毫米)?",
)
UNIT_TO_CM = {"cm": 1, "厘米": 1, "mm": 0.1, "毫米": 0.1, "m": 100, "in": 2.54,
              "inch": 2.54, "inches": 2.54, '"': 2.54}


def parse_dims(text: str) -> tuple[tuple[float, float, float] | None, list[Finding]]:
    text = (text or "").strip()
    if not text:
        return None, []
    m = DIMS.search(text)
    if not m:
        return None, [Finding("INVALID_VALUE", "error", "package_dims", f"无法解析尺寸：{text}")]
    unit = (m.group(4) or "").lower()
    findings = []
    if not unit:
        findings.append(
            Finding("UNIT_ASSUMED", "info", "package_dims", f"尺寸未写单位，按 cm 处理：{text}")
        )
    factor = UNIT_TO_CM.get(unit, 1)
    dims = tuple(round(float(g.replace(",", ".")) * factor, 2) for g in m.groups()[:3])
    return dims, findings


def dims_equal(a, b, tolerance: float) -> bool:
    """Package dimensions compared orientation-independently (sorted) within a tolerance."""
    return all(abs(x - y) <= tolerance for x, y in zip(sorted(a), sorted(b), strict=True))


# ---------------------------------------------------------------------------- OE numbers


def split_oe(text: str) -> list[tuple[str, str]]:
    """[(raw, normalized)] for each OE / cross-reference number in a cell."""
    out = []
    for part in re.split(r"[,;/|\n]+", text or ""):
        raw = part.strip()
        norm = normalize_number(raw)
        if norm and (raw, norm) not in out:
            out.append((raw, norm))
    return out


# ---------------------------------------------------------------------------- offers


def parse_price(text: str) -> tuple[Decimal | None, str | None, list[Finding]]:
    text = (text or "").strip()
    if not text:
        return None, None, []
    symbol_currency = None
    for sym in sorted(CURRENCY_SYMBOLS, key=len, reverse=True):
        if sym in text:
            symbol_currency = CURRENCY_SYMBOLS[sym]
            text = text.replace(sym, "")
            break
    cleaned = re.sub(r"[A-Za-z\s]", "", text)
    if re.fullmatch(r"\d+,\d{1,2}", cleaned):  # decimal comma
        cleaned = cleaned.replace(",", ".")
    cleaned = cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None, None, [Finding("INVALID_VALUE", "error", "price", f"无法解析价格：{text}")]
    if value < 0:
        return None, None, [Finding("INVALID_VALUE", "error", "price", f"价格为负：{text}")]
    return value, symbol_currency, []


def parse_currency(text: str) -> tuple[str | None, list[Finding]]:
    text = (text or "").strip().upper()
    if not text:
        return None, []
    code = CURRENCY_SYMBOLS.get(text, text)
    if code == "RMB":
        code = "CNY"
    if code not in ISO_CURRENCIES:
        return None, [Finding("INVALID_VALUE", "error", "currency", f"未知币种：{text}")]
    return code, []


def parse_moq(text: str) -> tuple[int | None, list[Finding]]:
    text = (text or "").strip()
    if not text:
        return None, []
    m = re.search(r"\d+(?:\.0+)?", text.replace(",", ""))
    if not m:
        return None, [Finding("INVALID_VALUE", "error", "moq", f"无法解析 MOQ：{text}")]
    return int(float(m.group(0))), []


DATE_FORMATS = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%b-%Y", "%b %d %Y", "%d %b %Y",
                "%Y-%m-%dT%H:%M:%S"]


def parse_date(text: str) -> tuple[date | None, list[Finding]]:
    text = (text or "").strip()
    if not text:
        return None, []
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.replace(",", ""), fmt).date(), []
        except ValueError:
            continue
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 >= b:
            return date(y, b, a), []
        if b > 12 >= a:
            return date(y, a, b), []
        # Both readings are valid; North American files usually mean MM/DD.
        return date(y, a, b), [
            Finding("AMBIGUOUS_DATE", "warning", "quote_date",
                    f"日期 {text} 可读作 MM/DD 或 DD/MM，已按 MM/DD 处理，请核对")
        ]
    return None, [Finding("INVALID_VALUE", "error", "quote_date", f"无法解析日期：{text}")]


# ---------------------------------------------------------------------------- whole record


@dataclass
class NormalizedRecord:
    supplier_part_no: str = ""
    source_ref: str = ""
    brand: str = ""
    name: str = ""
    category: str | None = None
    position: str | None = None
    fitment: Fitment | None = None
    dims: tuple[float, float, float] | None = None
    oe_numbers: list[tuple[str, str]] = field(default_factory=list)
    price: Decimal | None = None
    currency: str | None = None
    moq: int | None = None
    quote_date: date | None = None
    findings: list[Finding] = field(default_factory=list)
    normalized: dict = field(default_factory=dict)  # field -> JSON-able normalized value

    def key_attrs(self) -> dict:
        """Attributes that form the matching evidence fingerprint (a change re-opens decisions)."""
        return {
            "category": self.category,
            "position": self.position,
            "fitment": self.fitment.label if self.fitment else None,
            "dims": list(self.dims) if self.dims else None,
            "oe_numbers": sorted(n for _, n in self.oe_numbers),
        }


def normalize_record(values: dict[str, str], use_sku_suffix: bool | None = None) -> NormalizedRecord:
    """values: semantic field -> raw text. Returns parsed attributes and findings."""
    if use_sku_suffix is None:
        use_sku_suffix = rules.matching().get("normalization", {}).get("sku_position_suffix", True)
    get = lambda k: (values.get(k) or "").strip()  # noqa: E731
    rec = NormalizedRecord(
        supplier_part_no=get("supplier_part_no"),
        source_ref=get("source_ref"),
        brand=get("brand"),
        name=get("name"),
    )
    f = rec.findings

    # Category: explicit column first, else from the name.
    explicit = get("category")
    cat, ambiguous = category_from_name(explicit or rec.name)
    if not cat and explicit and rec.name:
        cat, ambiguous = category_from_name(rec.name)
    rec.category = cat
    if not cat:
        if ambiguous:
            f.append(Finding("UNKNOWN_CATEGORY", "warning", "name",
                             f"名称对应多个类别 {ambiguous}：{rec.name}", {"candidates": ambiguous}))
        else:
            f.append(Finding("UNKNOWN_CATEGORY", "warning", "name",
                             f"无法从名称识别产品类别：{rec.name or '（空）'}"))

    # Position: column, name and SKU suffix must agree; disagreement means "unknown".
    sources = {}
    if get("position"):
        p = position_from_text(get("position"))
        if p:
            sources["position"] = p
        else:
            f.append(Finding("INVALID_VALUE", "warning", "position", f"无法识别位置：{get('position')}"))
    p = position_from_text(rec.name)
    if p:
        sources["name"] = p
    if use_sku_suffix:
        p = position_from_sku(rec.supplier_part_no)
        if p:
            sources["supplier_part_no"] = p
    distinct = set(sources.values())
    if len(distinct) == 1:
        rec.position = distinct.pop()
    elif len(distinct) > 1:
        f.append(Finding("POSITION_INCONSISTENT", "warning", "position",
                         "位置来源互相矛盾：" + "，".join(f"{k}={v}" for k, v in sources.items()),
                         {"sources": sources}))
    if rec.position is None and is_sided(rec.category) and len(distinct) <= 1:
        f.append(Finding("MISSING_POSITION", "warning", "position",
                         f"{category_label(rec.category)} 有左右之分，但未给出位置"))

    fit, ff = parse_fitment(get("fitment"))
    rec.fitment = fit
    f.extend(ff)
    if not fit:
        f.append(Finding("MISSING_FITMENT", "warning", "fitment", "缺少适配车型"))

    rec.dims, df = parse_dims(get("package_dims"))
    f.extend(df)
    if not get("package_dims"):
        f.append(Finding("MISSING_DIMS", "warning", "package_dims", "缺少尺寸"))

    rec.oe_numbers = split_oe(get("oe_numbers"))
    if not rec.oe_numbers:
        f.append(Finding("MISSING_OE", "info", "oe_numbers", "缺少 OE/交叉参考号"))

    price, symbol_ccy, pf = parse_price(get("price"))
    rec.price = price
    f.extend(pf)
    ccy, cf = parse_currency(get("currency"))
    f.extend(cf)
    if not ccy and symbol_ccy:
        ccy = symbol_ccy
        f.append(Finding("CURRENCY_INFERRED", "info", "currency",
                         f"币种列为空，按价格符号推断为 {symbol_ccy}"))
    rec.currency = ccy
    if not get("price"):
        f.append(Finding("MISSING_PRICE", "warning", "price", "缺少报价"))
    if not rec.currency and not get("currency"):
        f.append(Finding("MISSING_CURRENCY", "warning", "currency", "缺少币种"))
    rec.moq, mf = parse_moq(get("moq"))
    f.extend(mf)
    if not get("moq"):
        f.append(Finding("MISSING_MOQ", "info", "moq", "缺少 MOQ"))
    rec.quote_date, qf = parse_date(get("quote_date"))
    f.extend(qf)
    if not get("quote_date"):
        f.append(Finding("MISSING_QUOTE_DATE", "info", "quote_date", "缺少报价日期"))
    if not rec.supplier_part_no:
        f.append(Finding("MISSING_PART_NO", "error", "supplier_part_no",
                         "缺少供应商/品牌编号，无法作为条目跟踪"))

    rec.normalized = {
        "supplier_part_no": normalize_number(rec.supplier_part_no) or None,
        "source_ref": rec.source_ref or None,
        "brand": rec.brand or None,
        "name": rec.category,
        "category": rec.category,
        "position": rec.position,
        "fitment": (
            {"make": fit.make, "model": fit.model, "year_from": fit.year_from,
             "year_to": fit.year_to} if fit else None
        ),
        "package_dims": list(rec.dims) if rec.dims else None,
        "oe_numbers": [n for _, n in rec.oe_numbers] or None,
        "price": str(price) if price is not None else None,
        "currency": rec.currency,
        "moq": rec.moq,
        "quote_date": rec.quote_date.isoformat() if rec.quote_date else None,
    }
    return rec
