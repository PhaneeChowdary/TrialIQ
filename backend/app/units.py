"""Unit conversion for the lab analytes. The code converts; a model never does the arithmetic."""

ALIASES = {
    "%": "%", "percent": "%", "pct": "%",
    "mmol/mol": "mmol/mol",
    "mg/g": "mg/g", "mg/g creat": "mg/g", "mg/g cr": "mg/g", "ug/mg": "mg/g",
    "mg/mmol": "mg/mmol",
    "pg/ml": "pg/mL", "ng/l": "pg/mL",
    "mmol/l": "mmol/L", "meq/l": "mmol/L",
    "ml/min/1.73m2": "mL/min/1.73m2", "ml/min/1.73 m2": "mL/min/1.73m2", "ml/min/1.73m²": "mL/min/1.73m2",
    "ml/min/1.73 m²": "mL/min/1.73m2", "ml/min": "mL/min/1.73m2",
    "kg/m2": "kg/m2", "kg/m^2": "kg/m2", "kg/m²": "kg/m2",
    "mm[hg]": "mm[Hg]", "mmhg": "mm[Hg]", "mm hg": "mm[Hg]",
}

# -> function. From, to.
_CONVERT = {
    ("mmol/mol", "%"): lambda v: v / 10.929 + 2.15,
    ("%", "mmol/mol"): lambda v: (v - 2.15) * 10.929,
    ("mg/mmol", "mg/g"): lambda v: v * 8.84,
    ("mg/g", "mg/mmol"): lambda v: v / 8.84,
}


def alternatives(unit: str | None) -> set[str]:
    """The other units that convert to or from this unit. Empty: the analyte has one unit."""
    u = normalize(unit)
    return {b for a, b in _CONVERT if a == u} | {a for a, b in _CONVERT if b == u}


_UNIT_IN_TEXT = sorted(ALIASES, key=len, reverse=True)


def unit_after(value: float, text: str) -> str | None:
    """The unit written right after the value in a text, e.g. '64 mmol/mol' -> 'mmol/mol'."""
    import re

    for m in re.finditer(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text):  # whole numbers only, not the 1 of "A1c"
        if abs(float(m.group(0)) - value) > 1e-6:
            continue
        tail = text[m.end():m.end() + 16].strip().lower()
        for alias in _UNIT_IN_TEXT:
            if tail.startswith(alias):
                return ALIASES[alias]
    return None


class UnitError(ValueError):
    pass


def normalize(unit: str | None) -> str:
    if not unit:
        return ""
    key = unit.strip().lower().replace("µ", "u")
    return ALIASES.get(key, unit.strip())


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float:
    """Convert value; raises UnitError when the two units do not convert."""
    a, b = normalize(from_unit), normalize(to_unit)
    if not b or a == b:
        return value
    if not a:
        raise UnitError(f"no unit for the value {value}; expected {b}")
    if (a, b) not in _CONVERT:
        raise UnitError(f"cannot convert {a} to {b}")
    return round(_CONVERT[(a, b)](value), 2)
