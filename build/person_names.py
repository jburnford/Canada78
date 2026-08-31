#!/usr/bin/env python3
"""Historical person-name normalisation and matching, shared by the DIA linkers.

The DIA staff lists, Ben Hoy's transcription and the LINCS Indian Affairs
agent labels are three renderings of the same names, and they disagree in
ways a single canonical key cannot absorb:

    printed return   Hoy's sheet                LINCS agent label
    Rev. Thos. Butler   Butler, Thomas J.        Thomas J. Butler
    Wm. Van Abbott      Van Abbott, Wm.          W. Van Abbott
    Dr. A.F. Clark      Clark, A.F., M.D.        A.F. Clark
    S.E. Mostyn Hoops   Mostyn-Hoops, S.E., M.D. S.E. Mostyn Hoops

So we do not reduce a name to one key. `parse` splits it into a set of
plausible *surname variants* and an ordered list of given initials, and
`score` compares two parsed names on those two axes separately. Callers block
on any shared surname variant and then rank by score.

Two rules earn their keep and should not be simplified away:

- **Strip suffixes before deciding the comma form.** "Peter E. Jones, M.D."
  is `Given Surname, Suffix`, not `Surname, Given`; reading its comma as an
  inversion produced the surname "peterejones". Suffixes go first, then a
  surviving comma means inversion.
- **Keep particles with the surname.** "Van Abbott" and "D'Amour" lose their
  identity when the last whitespace token is taken as the surname ("abbott",
  "amour"), and those forms then collide with unrelated people.
"""
import re
import unicodedata

from rapidfuzz.distance import JaroWinkler

# Honorifics and ranks. These precede the name and must not contribute an
# initial: "Rev. John Jacobs" is J. Jacobs, not R.J. Jacobs.
TITLES = {
    "rev", "revd", "reverend", "dr", "doctor", "hon", "honourable", "honorable",
    "sir", "mr", "mrs", "miss", "ms", "mme", "mlle", "capt", "captain", "col",
    "colonel", "lieut", "lieutenant", "lt", "major", "maj", "gen", "general",
    "prof", "professor", "sergt", "sgt", "sergeant", "insp", "inspector",
    "supt", "superintendent", "father", "fr", "ven", "venerable", "rt", "most",
    "brother", "bro", "sister", "sr_title", "msgr", "monsignor", "the", "late",
    "his", "grace", "right", "chief",
}

# Post-nominals: degrees, orders, professional letters. Removed wherever they
# appear, because they sit both after a comma and at the end of a bare name.
SUFFIXES = {
    "md", "ba", "ma", "bsc", "msc", "phd", "dd", "lld", "dcl", "bd", "llb",
    "jr", "junior", "sr", "senior", "esq", "esquire", "qc", "kc", "mp", "mpp",
    "mla", "ce", "ols", "dls", "pls", "plc", "cmg", "kcb", "kcmg", "cb", "cvo",
    "kcvo", "vd", "rn", "ra", "re", "frcs", "lrcp", "lrcs", "mrcs", "plb",
    "plsc", "plr", "mb", "cm", "plsr", "np", "jp", "jun", "junr", "sen",
    "senr",
}

# Nobiliary and patronymic particles that belong to the surname.
PARTICLES = {
    "van", "von", "de", "du", "des", "del", "della", "da", "das", "dos", "di",
    "la", "le", "les", "st", "ste", "saint", "sainte", "mac", "mc", "ter",
    "ten", "op", "vander", "vanden",
}

_ACCENT = re.compile(r"[̀-ͯ]")
_APOS = re.compile(r"[‘’ʼ`]")
_KEEP = re.compile(r"[^a-z',\s-]")
_SPACE = re.compile(r"\s+")


def _fold(raw):
    """Lowercase, strip accents, normalise apostrophes, drop stray punctuation.

    Periods become spaces so "A.F." tokenises to two initials, but commas and
    apostrophes survive: the comma still carries the inversion signal and the
    apostrophe holds "O'Leary" together.
    """
    s = unicodedata.normalize("NFKD", str(raw or ""))
    s = _ACCENT.sub("", s).lower()
    s = _APOS.sub("'", s)
    s = s.replace(".", " ").replace("&", " ")
    s = _KEEP.sub(" ", s)
    return _SPACE.sub(" ", s).strip(" ,-")


# Degrees that are always written with periods and are never a person's
# initials. "M.A." and "B.A." are excluded on purpose: "Smith, M.A." is far
# more often Malcolm Alexander Smith than a Master of Arts.
DOTTED_SUFFIXES = {
    "md", "dd", "lld", "phd", "dcl", "llb", "frcs", "lrcp", "lrcs", "mrcs",
    "kcb", "kcmg", "cmg", "qc", "kc", "vd",
}


def _strip_suffixes(s):
    """Remove post-nominal tokens anywhere in the name, and the commas they leave.

    Runs before the comma is interpreted, so "Ferguson, A.A., M.D." reduces to
    "ferguson, a a" (a true inversion) while "Peter E. Jones, M.D." reduces to
    "peter e jones" (no comma left, so no inversion).

    The period-splitting in `_fold` leaves "M.D." as two single-letter tokens,
    so a trailing run of single letters is rejoined and tested as well. Without
    this, "J. Macdonald, M.D." kept its comma, read as an inversion, and scored
    its stray "m d" initials against *M.A.* McDonald — the wrong person.
    """
    chunks = [c.split() for c in s.split(",")]
    chunks = [[t for t in c if t and t not in SUFFIXES] for c in chunks]
    # Trailing dotted degree, e.g. [... "benson", "m", "d"] or a whole final
    # chunk of ["m", "d"]. Only ever stripped from the end, and never when it
    # would leave nothing that could be a surname.
    for _ in range(2):
        flat = [t for c in chunks for t in c]
        for n in (4, 3, 2):
            tail = flat[-n:] if len(flat) >= n else []
            if (len(tail) == n and all(len(t) == 1 for t in tail)
                    and "".join(tail) in DOTTED_SUFFIXES
                    and any(len(t) > 1 for t in flat[:-n])):
                drop = n
                for c in reversed(chunks):
                    while drop and c:
                        c.pop()
                        drop -= 1
                    if not drop:
                        break
                break
        else:
            break
    return ", ".join(" ".join(c) for c in chunks if c)


def _strip_titles(toks):
    """Drop leading honorifics. Never strip the whole name."""
    i = 0
    while i < len(toks) - 1 and toks[i] in TITLES:
        i += 1
    return toks[i:]


def _surname_variants(sur_toks):
    """Every reasonable spelling of a multi-token surname.

    "van abbott" yields {vanabbott, van abbott, abbott}; the bare last token is
    kept because the printed returns often drop the particle, but it is listed
    after the full form so an exact full-form hit outranks it.
    """
    if not sur_toks:
        return []
    # A hyphenated surname is one token to the tokeniser but two names to the
    # printer: "Mostyn-Hoops" appears as "Mostyn Hoops" and as plain "Hoops".
    hyphenated = any("-" in t for t in sur_toks)
    sur_toks = [p for t in sur_toks for p in t.split("-") if p]
    joined = "".join(sur_toks)
    variants = [joined]
    if len(sur_toks) > 1:
        variants.append(sur_toks[-1])
        if hyphenated:
            # "Mostyn-Hoops" is printed as plain "Mostyn" as well as "Hoops".
            # Whitespace-separated tokens get no such licence: the first token
            # of "Thomas Coffey" is a given name, and offering it as a surname
            # matched him to J.L. Thompson.
            variants.append(sur_toks[0])
        # "Mc Donald" / "Mac Donald" printed with a space.
        if sur_toks[0] in {"mc", "mac"}:
            variants.append(sur_toks[0] + sur_toks[-1])
    # "o'leary" -> also "oleary" and "leary"; "d'amour" -> "damour", "amour".
    out = []
    for v in variants:
        out.append(v.replace("'", ""))
        if "'" in v:
            out.append(v.split("'", 1)[1])
    seen, uniq = set(), []
    for v in out:
        v = v.strip()
        if len(v) > 1 and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def parse(raw):
    """Split a name into (surname_variants, initials, given_tokens).

    `initials` is the ordered first letters of the given names; `given_tokens`
    keeps the full given words so a caller can tell "Thomas" from "T." when it
    matters.
    """
    s = _strip_suffixes(_fold(raw))
    if not s:
        return [], "", []

    if "," in s:
        head, _, tail = s.partition(",")
        sur_toks = _strip_titles(head.split()) or head.split()
        given = _strip_titles(tail.split())
        # A stray comma after the first name inverts nothing: "Alice, M.S.
        # Graham" is Alice M.S. Graham, whose surname is Graham. When the
        # tail ends in a full word rather than an initial, offer that word as
        # a surname variant too and let the margin decide.
        if (given and len(given) > 1 and len(given[-1]) >= 4
                and len(sur_toks) == 1 and len(sur_toks[0]) >= 4
                and all(len(t) == 1 for t in given[:-1])):
            extra = given[-1]
            va = _surname_variants(sur_toks) + _surname_variants([extra])
            seen, uniq = set(), []
            for v in va:
                if v not in seen:
                    seen.add(v)
                    uniq.append(v)
            inits = "".join(t[0] for t in given[:-1] if t and t[0].isalpha())
            return uniq, inits or "".join(t[0] for t in given if t), given[:-1]
    else:
        toks = _strip_titles(s.split())
        if not toks:
            return [], "", []
        if len(toks) == 1:
            sur_toks, given = toks, []
        elif len(toks[-1]) == 1 and len(toks[0]) > 1:
            # "Brass A.", "Allan Helen M." — Hoy's inverted form with the comma
            # lost. A name never *ends* in a bare initial unless the surname
            # came first, so read it that way.
            sur_toks, given = toks[:1], toks[1:]
        else:
            # Walk back over particles so "van abbott" stays together.
            i = len(toks) - 1
            while i > 1 and toks[i - 1] in PARTICLES:
                i -= 1
            sur_toks, given = toks[i:], toks[:i]

    given = [t for t in given if t and t not in TITLES]
    initials = "".join(t[0] for t in given if t[0].isalpha())
    return _surname_variants(sur_toks), initials, given


def _given_conflict(ga, gb):
    """Penalty when both sides spell out a first name and the names differ.

    "Agnes Cameron" and "Angus Cameron" reduce to the same initial and were
    scoring an identical 1.000 against each other; only the full token
    separates them. Returns None when the given names carry no such signal.
    """
    if not ga or not gb:
        return None
    x, y = ga[0], gb[0]
    if len(x) < 3 or len(y) < 3 or x == y:
        return None
    # Common abbreviations the printer used for the same name.
    if x.startswith(y) or y.startswith(x):
        return None
    return 0.0 if x[0] != y[0] else 0.1


def initials_score(a, b, ga=None, gb=None):
    """How compatible two initial strings are.

    Deliberately asymmetric-tolerant: the printed return often carries more
    initials than LINCS ("F.W. Smith" vs "F. Smith"), and a transposition
    ("W.L.B." vs "W.B.L.") is a transcription slip, not a different person.
    A differing *first* initial is the strong negative signal.
    """
    conflict = _given_conflict(ga, gb)
    if conflict is not None:
        return conflict
    if not a or not b:
        return 0.5  # uninformative, not evidence against
    if a == b:
        return 1.0
    if a.startswith(b) or b.startswith(a):
        return 0.95
    if set(a) == set(b):
        return 0.9
    if a[0] == b[0]:
        sa, sb = set(a), set(b)
        if sa <= sb or sb <= sa:
            return 0.85
        # One middle initial misread: "F.H. Paget" / "F.R. Paget",
        # "G.H. Corbett" / "G.E. Corbett". Same first initial, same length,
        # one position apart. Safe to score high because the margin test
        # still rejects it when the surname has several such candidates.
        if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
            return 0.75
        return 0.6 if sa & sb else 0.5
    return 0.15 if set(a) & set(b) else 0.0


def surname_score(va, vb):
    """Best Jaro-Winkler over the two variant lists, with a full-form bonus."""
    if not va or not vb:
        return 0.0
    best = 0.0
    for i, x in enumerate(va):
        for j, y in enumerate(vb):
            s = JaroWinkler.similarity(x, y)
            # Prefer agreement on the full (particle-bearing) form.
            if i == 0 and j == 0:
                s = min(1.0, s + 0.02)
            best = max(best, s)
    return best


def score(a, b, sur_floor=0.87):
    """Combined similarity of two parsed names, or 0.0 below the surname floor.

    Surname carries most of the weight: initials are frequently abbreviated
    away, but a surname that does not match is a different person.
    """
    va, ia, ga = a
    vb, ib, gb = b
    ss = surname_score(va, vb)
    if ss < sur_floor:
        return 0.0
    s = 0.65 * ss + 0.35 * initials_score(ia, ib, ga, gb)
    # Tie-break: both spelled the same first name out in full. Separates
    # "Alex G. Smith" from "A.G. Smith" when our row reads "Alex. G. Smith".
    if ga and gb and len(ga[0]) >= 3 and ga[0] == gb[0]:
        s = min(1.0, s + 0.01)
    return s


def _skeleton(v):
    """Consonant skeleton: drop vowels, collapse runs.

    Blocks names whose OCR damage is confined to vowels or doubled letters —
    "McNiell"/"McNeill", "Arsenault"/"Arsennault" — which a prefix key misses
    because "Mc" eats the whole prefix.
    """
    out = []
    for c in v:
        if c in "aeiou'":
            continue
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


def blocking_keys(parsed):
    """Keys to bucket a name under so candidates can be found without an O(n^2) sweep.

    A surname variant, its first four characters (which survives the OCR tail
    damage Jaro-Winkler forgives) and its consonant skeleton.
    """
    keys = set()
    for v in parsed[0]:
        keys.add(v)
        if len(v) >= 4:
            keys.add(v[:4])
        sk = _skeleton(v)
        if len(sk) >= 3:
            keys.add("~" + sk)
    return keys


_GENERATION = re.compile(r"\b(jr|jun|junr|junior|sr|sen|senr|senior)\b")


def generation(raw):
    """'jr' / 'sr' if the name carries a generational marker, else None.

    `parse` strips these with the other post-nominals, which is right for
    matching but loses the one token that says "Dreaver, J., sr." and
    "Dreaver, John" are father and son rather than one man in two posts.
    """
    m = _GENERATION.search(_fold(raw))
    if not m:
        return None
    return "jr" if m.group(1).startswith("j") else "sr"
