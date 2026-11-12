"""MAG scenes: what is said, as the sounds of speech (phonemes).

Pure Python and nothing else: it runs inside Blender's Python in the
sandboxed scene worker (no packages, no programs, no network). German words
go to their sounds by rules: long and short vowels (ie, a silent h after a
vowel, a doubled vowel; a doubled consonant after a short one), sch, ch
(ich/ach), ng/nk, pf, qu, z/tz, ck, st/sp at the start of a word, the weak e
and the r at the end of a word, the diphthongs. Numbers are said as words,
as the page's voice says them (ops/tts/tts-worker.js), and short words in
capitals that the voice spells are spelled. A text that is clearly English
(the page's own test) goes by a lighter set of English rules. face.py turns
the sounds into mouth shapes.
"""
import re

VOWELS = "aeiouäöüy"
# Sounds: a  a: e: ɛ ɛ: i: ɪ o: ɔ u: ʊ y: ʏ ø: œ ə ɐ (and English æ ɒ ʌ ɜ),
# p b m f v t d n l s z ʃ ç x k g ŋ r j h (and English θ ð w ɹ).
PAUSE, GAP = "_", " "

# ---- numbers, as the voice says them ---------------------------------------------
ONES = ("null ein zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn fünfzehn sechzehn siebzehn "
        "achtzehn neunzehn").split()
TENS = ("", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig")
EN_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
           "eighteen nineteen").split()
EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
# Letters as they are named in German, and the short words in capitals the voice spells.
NAMED = {"A": "a", "B": "be", "C": "ce", "D": "de", "E": "e", "F": "eff", "G": "ge", "H": "ha", "I": "i", "J": "jott", "K": "ka",
         "L": "ell", "M": "emm", "N": "enn", "O": "o", "P": "pe", "Q": "ku", "R": "err", "S": "ess", "T": "te", "U": "u", "V": "vau",
         "W": "we", "X": "ix", "Y": "ypsilon", "Z": "zett"}
SPELLED = set(("KI VM GPU CPU PDF URL USB API UI PR SSH HTML CSS GLB STL PLY UV TTS STT LLM RAM SSD PC TV DVD CD EU USA BRD DDR ZDF "
               "ARD LKW PKW ICE WC SMS DNS VPN IP TCP UDP HTTP HTTPS JSON XML SQL PNG JPG GIF MP WLAN LAN NUMA ONNX WASM GGUF CMU IK "
               "FPS KVM QR OCR MCP").split())
DE_WORDS = set("der die das und ist nicht ich du wir sie ein eine mit auf für von zu den dem des sich auch es bitte danke ja nein "
               "oder aber wie was kann noch nur schon hier jetzt".split())
EN_WORDS = set("the and is are was were you your i it this that with for of to in on not have has be can will what please thanks "
               "yes no but how do does".split())


def _below1000(n, alone=False):
    out = ""
    if n >= 100:
        out += ONES[n // 100] + "hundert"
        n %= 100
    if n >= 20:
        out += (ONES[n % 10] + "und" if n % 10 else "") + TENS[n // 10]
    elif n > 0:
        out += "eins" if n == 1 and alone else ONES[n]
    return out


def cardinal(n):
    """A whole number in German words, as the voice says it."""
    if n == 0:
        return "null"
    if n > 999999999:
        return " ".join(ONES[int(d)] for d in str(n))
    millions, thousands, rest = n // 1000000, n // 1000 % 1000, n % 1000
    parts = []
    if millions:
        parts.append("eine million" if millions == 1 else _below1000(millions) + " millionen")
    tail = (("ein" if thousands == 1 else _below1000(thousands)) + "tausend" if thousands else "") + (_below1000(rest, True) if rest else "")
    if tail:
        parts.append(tail)
    return " ".join(parts)


def english_cardinal(n):
    """A whole number in English words (up to a million; longer ones digit by digit)."""
    if n > 999999:
        return " ".join(EN_ONES[int(d)] for d in str(n))
    if n < 20:
        return EN_ONES[n]
    if n < 100:
        return EN_TENS[n // 10] + (" " + EN_ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return EN_ONES[n // 100] + " hundred" + (" " + english_cardinal(n % 100) if n % 100 else "")
    return english_cardinal(n // 1000) + " thousand" + (" " + english_cardinal(n % 1000) if n % 1000 else "")


def english(text):
    """Whether a text is clearly English: the page's own test for which voice reads it."""
    if re.search(r"[äöüß]", text, re.I):
        return False
    found = re.findall(r"[a-z']+", text.lower())
    de = sum(w in DE_WORDS for w in found)
    en = sum(w in EN_WORDS for w in found)
    return not de and en >= 2 and en * 4 >= len(found)


def _said(text, en):
    """The text as it is spoken: numbers in words, spelled capitals as their letters."""
    if not en:
        text = re.sub(r"\b[A-Z]{2,5}\b", lambda m: " ".join(NAMED[c] for c in m.group()) if m.group() in SPELLED else m.group(), text)
        text = re.sub(r"(\d)\s?%", r"\1 prozent", text).replace("&", " und ").replace("+", " plus ")
        text = re.sub(r"\b\d{1,3}(?:\.\d{3})+\b", lambda m: m.group().replace(".", ""), text)
    else:
        text = re.sub(r"(\d)\s?%", r"\1 percent", text).replace("&", " and ").replace("+", " plus ")
        text = re.sub(r"\b\d{1,3}(?:,\d{3})+\b", lambda m: m.group().replace(",", ""), text)
    whole = english_cardinal if en else cardinal
    point, sep = ("point", r"\.") if en else ("komma", ",")

    def number(m):
        head, _, frac = m.group().replace(",", ".").partition(".") if en else m.group().partition(",")
        said = whole(int(head))
        if frac:
            said += " " + point + " " + " ".join((EN_ONES if en else ONES)[int(d)] for d in frac)
        return " " + said + " "
    return re.sub(r"\d+(?:" + sep + r"\d+)?", number, text)


# ---- German words to sounds ------------------------------------------------------
SHORT_WORDS = {"an", "am", "in", "im", "um", "es", "das", "was", "mit", "ab", "ob", "bis", "hat", "man", "von", "vom", "ins",
               "un", "dass", "des", "wes", "ist", "sind", "ich", "mich", "dich", "sich", "doch", "noch", "nach"}
LONG = {"a": "a:", "e": "e:", "i": "i:", "o": "o:", "u": "u:", "ä": "ɛ:", "ö": "ø:", "ü": "y:", "y": "y:"}
SHORT = {"a": "a", "e": "ɛ", "i": "ɪ", "o": "ɔ", "u": "ʊ", "ä": "ɛ", "ö": "œ", "ü": "ʏ", "y": "ʏ"}
BACK = {"a", "a:", "o:", "ɔ", "u:", "ʊ"}
WEAK_ENDS = {"", "n", "l", "m", "s", "t", "r", "rn", "rt", "rs", "ns", "nd", "st", "ls", "ms", "ts"}
SIMPLE = {"b": "b", "d": "d", "f": "f", "g": "g", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p", "t": "t", "j": "j",
          "w": "v", "v": "f", "ß": "s"}


def german_word(w):
    """One German word (lower case) as [(sound, stressed)]. Stress falls on the first full vowel."""
    out, i, n, stressed = [], 0, len(w), False
    first = True                               # the next vowel is the word's first

    def vowel(sound):
        nonlocal stressed, first
        strong = sound not in ("ə", "ɐ") and not stressed
        stressed = stressed or strong
        out.append((sound, strong))
        first = False

    if n > 4 and w[:3] in ("ver", "zer") and w[3] not in VOWELS:
        out.append(("f", False) if w[0] == "v" else ("t", False))
        if w[0] == "z":
            out.append(("s", False))
        vowel("ɐ")
        i = 3
    elif n > 4 and w[:2] in ("be", "ge") and w[2] not in VOWELS + "h" and w[3:4] in tuple(VOWELS):
        out.append((w[0], False))
        vowel("ə")
        i = 2
    start = i                                  # where the stem begins: st/sp there is ʃt/ʃp
    while i < n:
        c, two, three = w[i], w[i:i + 2], w[i:i + 3]
        if c in VOWELS:
            if two in ("ei", "ai", "ey", "ay"):
                vowel("a")
                out.append(("ɪ", False))
                i += 2 + (w[i + 2:i + 3] == "h")
                continue
            if two == "au":
                vowel("a")
                out.append(("ʊ", False))
                i += 2 + (w[i + 2:i + 3] == "h")
                continue
            if two in ("eu", "äu"):
                vowel("ɔ")
                out.append(("ʏ", False))
                i += 2 + (w[i + 2:i + 3] == "h")
                continue
            if two == "ie" and not (i + 2 == n and i > 1 and w[i - 1] == "l" and n > 5):     # Familie: i-ə
                vowel("i:")
                i += 2 + (w[i + 2:i + 3] == "h")
                continue
            if two in ("aa", "ee", "oo"):
                vowel(LONG[c])
                i += 2 + (w[i + 2:i + 3] == "h")
                continue
            rest = w[i + 1:]
            if rest[:1] == "h":                 # a silent h makes the vowel long
                vowel(LONG[c])
                i += 2
                continue
            if c == "i" and rest == "g":        # -ig: ɪç
                vowel("ɪ")
                out.append(("ç", False))
                break
            after = re.match(r"[^" + VOWELS + r"]*", rest).group()
            if c == "e" and not first and after in WEAK_ENDS and not rest[len(after):]:
                vowel("ɐ" if after[:1] == "r" else "ə")        # the weak e; -er is ɐ
                i += 1 + (after[:1] == "r")
                continue
            if after == "":
                long = True
            elif after == "ß" or (len(after) == 1 and rest[1:2]):
                long = True                     # one consonant before the next vowel
            elif len(after) == 1:
                long = first and w not in SHORT_WORDS
            else:
                long = False                    # doubled or several consonants
            vowel(LONG[c] if long else SHORT[c])
            i += 1
            continue
        if three == "sch":
            out.append(("ʃ", False))
            i += 3
        elif three == "chs":
            out += [("k", False), ("s", False)]
            i += 3
        elif two == "ch":
            if i == 0:
                out.append(("k", False) if w[2:3] in tuple("rlao") else ("ʃ", False))
            else:
                out.append(("x", False) if out and out[-1][0] in BACK else ("ç", False))
            i += 2
        elif w[i:i + 4] == "tion":
            out += [("t", False), ("s", False), ("j", False)]
            vowel("o:")
            out.append(("n", False))
            i += 4
        elif two == "ng":
            out.append(("ŋ", False))
            i += 2
        elif two == "nk":
            out += [("ŋ", False), ("k", False)]
            i += 2
        elif two == "pf":
            out += [("p", False), ("f", False)]
            i += 2
        elif two == "ph":
            out.append(("f", False))
            i += 2
        elif two == "qu":
            out += [("k", False), ("v", False)]
            i += 2
        elif two in ("tz", "zz") or c == "z":
            out += [("t", False), ("s", False)]
            i += 2 if two in ("tz", "zz") else 1
        elif two in ("ck", "dt", "th"):
            out.append(("k" if two == "ck" else "t", False))
            i += 2
        elif i == start and two in ("sp", "st"):
            out += [("ʃ", False), (two[1], False)]
            i += 2
        elif c == "s":
            between = (i == 0 or w[i - 1] in VOWELS) and w[i + 1:i + 2] in tuple(VOWELS)
            out.append(("z" if between else "s", False))
            i += 2 if two == "ss" else 1
        elif c == "x":
            out += [("k", False), ("s", False)]
            i += 1
        elif c == "c":
            out += [("t", False), ("s", False)] if w[i + 1:i + 2] in tuple("eiäy") else [("k", False)]
            i += 1
        elif c == "h":
            if w[i + 1:i + 2] in tuple(VOWELS):
                out.append(("h", False))
            i += 1
        elif c == "r":
            if i == 0 or w[i + 1:i + 2] in tuple(VOWELS):
                out.append(("r", False))
            elif not out or out[-1][0] != "ɐ":
                out.append(("ɐ", False))        # an r after a vowel is spoken as ɐ
            i += 2 if two == "rr" else 1
        elif c in SIMPLE:
            out.append((SIMPLE[c], False))
            i += 2 if w[i + 1:i + 2] == c else 1        # a doubled consonant is one sound
        else:
            i += 1
    return out


# ---- English words to sounds: a lighter set of rules -------------------------------
EN_LEXICON = {"the": "ð ə", "a": "ə", "an": "ə n", "of": "ə v", "to": "t u:", "do": "d u:", "you": "j u:", "your": "j ɔ:",
              "i": "a ɪ", "is": "ɪ z", "was": "w ɒ z", "are": "a:", "one": "w ʌ n", "two": "t u:", "what": "w ɒ t",
              "who": "h u:", "have": "h æ v", "said": "s ɛ d", "says": "s ɛ z", "there": "ð ɛ ɐ", "where": "w ɛ ɐ",
              "they": "ð e ɪ", "be": "b i:", "we": "w i:", "he": "h i:", "she": "ʃ i:", "me": "m i:", "no": "n o: ʊ",
              "go": "g o: ʊ", "so": "s o: ʊ", "my": "m a ɪ", "by": "b a ɪ", "eye": "a ɪ", "come": "k ʌ m", "some": "s ʌ m"}
EN_VOWELS = "aeiouy"
EN_GROUPS = (("igh", ("a", "ɪ")), ("ee", ("i:",)), ("ea", ("i:",)), ("oo", ("u:",)), ("ou", ("a", "ʊ")), ("ow", ("a", "ʊ")),
             ("oa", ("o:", "ʊ")), ("ai", ("e:", "ɪ")), ("ay", ("e:", "ɪ")), ("ey", ("e:", "ɪ")), ("oi", ("ɔ", "ɪ")),
             ("oy", ("ɔ", "ɪ")), ("au", ("ɔ:",)), ("aw", ("ɔ:",)), ("ew", ("j", "u:")), ("ie", ("a", "ɪ")),
             ("ar", ("a:",)), ("or", ("ɔ:",)), ("er", ("ɜ",)), ("ir", ("ɜ",)), ("ur", ("ɜ",)))
EN_MAGIC = {"a": ("e:", "ɪ"), "e": ("i:",), "i": ("a", "ɪ"), "o": ("o:", "ʊ"), "u": ("j", "u:")}
EN_SHORT = {"a": "æ", "e": "ɛ", "i": "ɪ", "o": "ɒ", "u": "ʌ", "y": "ɪ"}
EN_DIGRAPHS = (("tch", ("t", "ʃ")), ("th", ("θ",)), ("sh", ("ʃ",)), ("ch", ("t", "ʃ")), ("ph", ("f",)), ("wh", ("w",)),
               ("ck", ("k",)), ("ng", ("ŋ",)), ("qu", ("k", "w")), ("kn", ("n",)), ("wr", ("ɹ",)))
EN_SIMPLE = {"b": "b", "d": "d", "f": "f", "g": "g", "k": "k", "l": "l", "m": "m", "n": "n", "p": "p", "t": "t", "v": "v",
             "w": "w", "r": "ɹ", "s": "s", "z": "z", "j": "ʃ"}


def english_word(w):
    """One English word (lower case) as [(sound, stressed)]: spelling rules, not a dictionary."""
    if w in EN_LEXICON:
        sounds = EN_LEXICON[w].split()
        out, stressed = [], False
        for s in sounds:
            strong = s[0] in "aeiouɪʊɛæɒʌɔɜ" and s != "ə" and not stressed
            stressed = stressed or strong
            out.append((s, strong))
        return out
    out, i, n, stressed = [], 0, len(w), False
    if n > 2 and w.endswith("e") and any(c in EN_VOWELS for c in w[:-1]):
        silent = n - 1                          # a final e is not spoken ...
        if w.endswith("le") and n > 3 and w[-3] not in EN_VOWELS:
            silent = None                       # ... but -le is l
    else:
        silent = None

    def vowel(sounds):
        nonlocal stressed
        for k, s in enumerate(sounds):
            strong = k == 0 and s not in ("j", "ə") and not stressed
            stressed = stressed or strong
            out.append((s, strong))

    while i < n:
        if i == silent:
            break
        c = w[i]
        if c == "e" and w.endswith("le") and i == n - 1:
            break
        group = next(((g, s) for g, s in EN_GROUPS if w.startswith(g, i)), None)
        if group:
            vowel(group[1])
            i += len(group[0])
            continue
        if c in EN_VOWELS and not (c == "y" and i == 0):
            # a vowel, one consonant and the silent e: its long sound (make, time, home)
            if silent is not None and i + 2 == silent and w[i + 1] not in EN_VOWELS and c in EN_MAGIC:
                vowel(EN_MAGIC[c])
            elif c == "y":
                vowel(("i:",) if i == n - 1 and n > 2 else ("ɪ",))
            else:
                vowel((EN_SHORT[c],))
            i += 1
            continue
        digraph = next(((g, s) for g, s in EN_DIGRAPHS if w.startswith(g, i)), None)
        if digraph:
            out += [(s, False) for s in digraph[1]]
            i += len(digraph[0])
            continue
        if c == "c":
            out.append(("s" if w[i + 1:i + 2] in tuple("eiy") else "k", False))
        elif c == "x":
            out += [("k", False), ("s", False)]
        elif c == "y":
            out.append(("j", False))
        elif c == "h":
            if w[i + 1:i + 2] in tuple(EN_VOWELS):
                out.append(("h", False))
        elif c in EN_SIMPLE:
            out.append((EN_SIMPLE[c], False))
            if w[i + 1:i + 2] == c:
                i += 1                          # a doubled consonant is one sound
        i += 1
    return out


def sounds(text):
    """What is said as [(sound, stressed)], with PAUSE where a sentence breaks and GAP between words."""
    text = str(text)
    en = english(text)
    text = _said(text, en)
    word = english_word if en else german_word
    out = []
    for m in re.finditer(r"[^\W\d_]+|[.,;:!?\n]", text):
        token = m.group()
        if token in ".,;:!?\n":
            if out and out[-1][0] != PAUSE:
                out.append((PAUSE, False))
            continue
        if out and out[-1][0] not in (PAUSE,):
            out.append((GAP, False))
        out += word(token.lower())
    while out and out[-1][0] == GAP:
        out.pop()
    return out
