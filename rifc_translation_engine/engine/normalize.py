"""Normalisation helpers: headers, names, seasons, ages."""
from __future__ import annotations

import re
import unicodedata

import numpy as np
import pandas as pd


def norm_header(h) -> str:
    s = str(h).strip().lower()
    s = s.replace("\n", " ").replace("_", " ")
    s = re.sub(r"[,\.\(\)\[\]:;]", " ", s)
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_name(name) -> str | None:
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return None
    s = strip_accents(str(name)).lower()
    s = re.sub(r"[^a-z\s\.]", " ", s)
    s = s.replace(".", ". ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def short_name(n: str | None) -> str | None:
    """'john smith' -> 'j smith'; 'j. smith' -> 'j smith'."""
    if not n:
        return None
    parts = n.replace(".", "").split()
    if len(parts) < 2:
        return parts[0] if parts else None
    return f"{parts[0][0]} {parts[-1]}"


def is_abbreviated(n: str | None) -> bool:
    if not n:
        return False
    first = n.split()[0].replace(".", "")
    return len(first) == 1


def norm_team(t) -> str | None:
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return None
    s = strip_accents(str(t)).lower()
    s = re.sub(r"\b(fc|sc|cf|afc|cd|club)\b", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip() or None


_SPLIT = re.compile(r"^\s*(\d{2,4})\s*[-/]\s*(\d{2,4})\s*$")
_SINGLE = re.compile(r"^\s*(\d{4})(?:\.0+)?\s*$")


def parse_season(label):
    """Return (normalised label, start, end, kind).

    Calendar seasons (USL, MLS, Scandinavia, Brazil...) like '2025' run roughly
    March to November: start = year + 0.2, end = year + 0.85.
    Split seasons like '2024-25' run roughly August to May:
    start = first year + 0.6, end = second year + 0.4.
    Values are approximate decimal years used only for ordering and gaps.
    """
    if label is None or (isinstance(label, float) and np.isnan(label)):
        return None, np.nan, np.nan, None
    s = str(label).strip()
    m = _SINGLE.match(s)
    if m:
        y = int(m.group(1))
        return str(y), y + 0.2, y + 0.85, "calendar"
    m = _SPLIT.match(s)
    if m:
        a, b = m.group(1), m.group(2)
        a = int(a) if len(a) == 4 else 2000 + int(a)
        b = int(b) if len(b) == 4 else (a // 100) * 100 + int(b)
        if b == a:  # '2025-2025'
            return str(a), a + 0.2, a + 0.85, "calendar"
        return f"{a}-{str(b)[-2:]}", a + 0.6, b + 0.4, "split"
    # last resort: first 4 digit year
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        y = int(m.group(0))
        return str(y), y + 0.2, y + 0.85, "calendar"
    return s, np.nan, np.nan, None


def parse_age(v):
    """Ages like 25, '25', or FBref '25-123' (years-days)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    s = str(v).strip()
    m = re.match(r"^(\d{1,2})-(\d{1,3})$", s)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 365.25
    try:
        return float(s)
    except ValueError:
        return np.nan


def to_number(series: pd.Series) -> pd.Series:
    if series.dtype.kind in "fiu":
        return series.astype(float)
    s = series.astype(str).str.strip()
    s = s.str.replace("%", "", regex=False).str.replace("−", "-", regex=False)
    # thousands separators like 2,500 but keep decimal commas like 0,45
    s = s.where(~s.str.match(r"^\d{1,3}(,\d{3})+(\.\d+)?$"), s.str.replace(",", "", regex=False))
    s = s.str.replace(",", ".", regex=False)
    s = s.replace({"": np.nan, "nan": np.nan, "None": np.nan, "-": np.nan, "—": np.nan})
    return pd.to_numeric(s, errors="coerce")


def parse_height(series: pd.Series) -> pd.Series:
    h = to_number(series)
    h = h.where(h > 0)  # Wyscout writes 0 for unknown height
    # metres -> cm, inches -> cm
    h = np.where(h < 3, h * 100, h)
    h = np.where((h > 50) & (h < 90), h * 2.54, h)
    return pd.Series(h, index=series.index, dtype=float)
