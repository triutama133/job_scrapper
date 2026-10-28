"""Ekstraksi salary STRICT untuk sumber yang halamannya tidak punya field salary terstruktur.

Hanya menerima RENTANG gaji lengkap (dua angka, masing-masing >= 10.000) dengan simbol mata uang.
Menolak potongan seperti 'Range: $180', '$170-200' (k), 'up to $350' atau angka tunggal yang
sering salah ambil dari elemen halaman lain.
"""
import re

_CUR = r"(US\$|USD|CA\$|CAD|AU\$|AUD|EUR|GBP|[$€£])"
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{5,}(?:\.\d+)?)"
_RANGE = re.compile(rf"{_CUR}\s?{_NUM}\s*(?:[-–—]|to)\s*{_CUR}?\s?{_NUM}", re.I)


def strict_salary_range(text, min_value=10000):
    """Kembalikan 'USD 125,000 - 150,000' dari teks, atau None bila tidak ada rentang yang valid."""
    if not text:
        return None
    for m in _RANGE.finditer(text):
        cur, lo, _, hi = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            lo_v, hi_v = float(lo.replace(",", "")), float(hi.replace(",", ""))
        except ValueError:
            continue
        if lo_v < min_value or hi_v < min_value or hi_v < lo_v:
            continue
        code = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "CA$": "CAD", "AU$": "AUD"}.get(cur.upper() if cur.isalpha() else cur, cur.upper())
        fmt = lambda v: f"{int(round(v)):,}"
        return f"{code} {fmt(lo_v)} - {fmt(hi_v)}"
    return None
