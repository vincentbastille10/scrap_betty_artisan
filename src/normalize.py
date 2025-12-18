import re

def normalize_phone_fr(raw: str) -> str:
    """
    Normalise en +33XXXXXXXXX (quand possible)
    - enlève espaces/points/parenthèses
    - gère 0X.., +33 X.., 0033 X..
    """
    if not raw:
        return ""
    s = raw.strip()
    s = s.replace("(0)", "")
    s = re.sub(r"[^\d+]", "", s)

    # 0033 -> +33
    if s.startswith("0033"):
        s = "+33" + s[4:]

    if s.startswith("+33"):
        digits = re.sub(r"\D", "", s[3:])
        if digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 9:
            return "+33" + digits
        return ""

    # format national 0XXXXXXXXX
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10 and digits.startswith("0"):
        return "+33" + digits[1:]

    # déjà en 9 chiffres (rare) → on assume FR
    if len(digits) == 9:
        return "+33" + digits

    return ""
