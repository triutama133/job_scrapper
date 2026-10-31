"""
Apakah lowongan "remote" benar-benar terbuka untuk pelamar di Indonesia, atau dibatasi ke negara/wilayah tertentu
("Remote - US", "must be located in the UK", "authorized to work in Canada")?

Keputusan hanya berdasarkan bukti eksplisit. Tidak ada bukti = dianggap terbuka (jangan membuang lowongan yang sah).
Sinyal global eksplisit ("worldwide", "APAC", "Asia", "Indonesia") selalu menang atas pembatasan.
"""
import re

REGION = (r"(?:u\.?s\.?a?|united\s+states(?:\s+of\s+america)?|america|north\s+america|u\.?k\.?|united\s+kingdom|britain|"
          r"canada|canadian|eu|e\.u\.|europe(?:an(?:\s+union)?)?|emea|latam|latin\s+america|amer|germany|france|netherlands|"
          r"spain|portugal|ireland|poland|italy|sweden|norway|denmark|finland|switzerland|austria|belgium|australia|"
          r"new\s+zealand|india|brazil|mexico|argentina|colombia|south\s+africa|nigeria|kenya|israel|turkey|uae|"
          r"singapore|malaysia|philippines|vietnam|thailand|japan|korea|china|hong\s+kong|taiwan)")

GLOBAL_LOC_RE = re.compile(
    r"world\s*-?\s*wide|anywhere|global|international|everywhere|\bapac\b|\basia\b|\basean\b|south\s*-?\s*east\s+asia|"
    r"\bsea\b|indonesia|jakarta|any\s+(?:country|location)", re.I)

# Kata yang tidak menyebut tempat apa pun (sisa kata selain ini = ada tempat tertentu = terbatas)
_UNSPECIFIED = {"remote", "remotely", "fully", "flexible", "distributed", "work", "from", "home", "wfh", "position",
                "role", "job", "only", "based", "first", "option", "optional", "or", "and", "the", "a", "in", "is",
                "n", "a", "tbd", "time", "full", "part", "contract", "freelance", "permanent", "worldwide"}

GLOBAL_DESC_RE = re.compile(
    rf"world\s*-?\s*wide|anywhere\s+in\s+the\s+world|"
    rf"from\s+anywhere(?!\s+(?:in|within)\s+(?:the\s+)?{REGION}\b)|any\s+(?:country|location|time\s*zone)|all\s+countries|"
    rf"\bapac\b|\basia\b|\basean\b|south\s*-?\s*east\s+asia|indonesia|jakarta", re.I)

_RESTRICT_DESC = [
    r"\b(?:must|need(?:s)?\s+to|required\s+to|have\s+to|should)\s+(?:currently\s+)?(?:be\s+)?(?:located|based|resid\w+|living|live)\s+(?:in|within|near)\b",
    r"\bmust\s+(?:be\s+)?(?:a\s+)?(?:resident|citizen)\b",
    r"\b(?:authori[sz]ed|eligible|legally\s+(?:authori[sz]ed|permitted)|permitted)\s+to\s+work\s+in\b",
    r"\bright\s+to\s+work\s+in\b",
    r"\bwork\s+authori[sz]ation\b",
    r"\b(?:unable|not\s+able|cannot|can't|do\s+not|don't|will\s+not|won't|does\s+not)\s+(?:to\s+)?(?:offer\s+|provide\s+)?(?:visa\s+)?sponsor",
    rf"\b{REGION}[-\s]?(?:based|only|residents?|citizens?)\b",
    rf"\bremote\s*(?:[-–—(,/:]|\bin\b|\bwithin\b|\bfrom\b)\s*(?:the\s+)?{REGION}\b",
    rf"\b(?:only|limited\s+to|restricted\s+to|open\s+to)\s+(?:candidates|applicants|residents|people)?\s*(?:\w+\s+){{0,3}}(?:in|from|within)\s+(?:the\s+)?{REGION}\b",
    rf"\b(?:within|in)\s+(?:the\s+)?{REGION}\s+(?:time\s*zones?|only)\b",
    rf"\banywhere\s+(?:in|within)\s+(?:the\s+)?{REGION}\b",
]
RESTRICT_DESC_RE = [re.compile(p, re.I) for p in _RESTRICT_DESC]

# Judul dengan wilayah dalam kurung/tanda baca: "(USA) Staff Data Engineer", "Data Engineer [UK]", "Analyst - Remote (US)"
TITLE_RESTRICT_RE = re.compile(
    rf"[\[(]\s*(?:remote\s*[-–—,]?\s*)?{REGION}\s*(?:[-–—,/]\s*remote\s*)?[\])]|"
    rf"\bremote\s*[-–—,:]\s*{REGION}\b|\b{REGION}\s*[-–—]\s*remote\b", re.I)

HEAD_CHARS = 2500


def location_restricted(location):
    """True = lokasi menyebut tempat tertentu non-global; False = lokasi global eksplisit; None = tidak menyebut tempat."""
    if not location or not str(location).strip():
        return None
    loc = str(location)
    if GLOBAL_LOC_RE.search(loc):
        return False
    words = re.sub(r"[^a-z]+", " ", loc.lower()).split()
    return True if any(w not in _UNSPECIFIED for w in words) else None


def description_restriction(description):
    """Potongan teks pembatas negara pada awal deskripsi, atau None. Sinyal global eksplisit membatalkannya."""
    head = (description or "")[:HEAD_CHARS]
    if GLOBAL_DESC_RE.search(head):
        return None
    for rx in RESTRICT_DESC_RE:
        m = rx.search(head)
        if m:
            return re.sub(r"\s+", " ", head[max(0, m.start() - 15): m.end() + 25]).strip()
    return None


def restriction_reason(location=None, description=None, implicit_region=None, title=None):
    """None = terbuka/tidak ada bukti pembatasan; selain itu alasan singkat.

    implicit_region: negara tempat pencarian dilakukan untuk sumber yang tidak menyimpan lokasi (LinkedIn/Jobstreet
    hasil pencarian per negara = "remote di negara itu"); terbatas kecuali teks menyebut sinyal global.
    """
    loc_r = location_restricted(location)
    if loc_r is True:
        return f"location: {str(location).strip()[:60]}"
    if loc_r is False:
        return None
    if title and not GLOBAL_DESC_RE.search(title):
        m = TITLE_RESTRICT_RE.search(title)
        if m:
            return f"title: {m.group(0)[:40]}"
    reason = description_restriction(description)
    if reason:
        return f"text: {reason[:80]}"
    if implicit_region and not GLOBAL_DESC_RE.search((description or "")[:HEAD_CHARS]):
        return f"searched in {implicit_region}"
    return None
