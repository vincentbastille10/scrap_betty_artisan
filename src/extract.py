import re
import json
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")
# Tel FR : accepte espaces, points, parenthèses, +33, 0X...
PHONE_RE = re.compile(r"(\+?\d[\d\s().\-]{7,}\d)")

def _decode_cfemail(hex_str: str) -> str:
    # Cloudflare email obfuscation
    try:
        r = int(hex_str[:2], 16)
        email = "".join(
            chr(int(hex_str[i:i+2], 16) ^ r)
            for i in range(2, len(hex_str), 2)
        )
        return email
    except Exception:
        return ""

def _extract_jsonld_emails_phones(html: str):
    emails, phones = set(), set()
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = (tag.string or "").strip()
        if not txt:
            continue
        try:
            data = json.loads(txt)
        except Exception:
            continue

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k.lower() in ("email",):
                        if isinstance(v, str):
                            emails.add(v.strip())
                    if k.lower() in ("telephone", "phone"):
                        if isinstance(v, str):
                            phones.add(v.strip())
                    walk(v)
            elif isinstance(x, list):
                for it in x:
                    walk(it)

        walk(data)
    return emails, phones

def extract_contacts_from_html_pages(pages: dict):
    emails = set()
    phones = set()

    for url, html in pages.items():
        soup = BeautifulSoup(html, "lxml")

        # tel: et mailto:
        for a in soup.select("a[href^='mailto:']"):
            val = a.get("href", "")[7:].split("?")[0].strip()
            if val:
                emails.add(val)

        for a in soup.select("a[href^='tel:']"):
            val = a.get("href", "")[4:].strip()
            if val:
                phones.add(val)

        # Cloudflare data-cfemail
        for a in soup.select("a.__cf_email__"):
            hex_str = a.get("data-cfemail", "") or ""
            dec = _decode_cfemail(hex_str)
            if dec:
                emails.add(dec)

        # brut texte
        text = soup.get_text(" ", strip=True)
        for m in EMAIL_RE.findall(text):
            emails.add(m.strip())
        for m in PHONE_RE.findall(text):
            phones.add(m.strip())

        # JSON-LD
        je, jp = _extract_jsonld_emails_phones(html)
        emails |= je
        phones |= jp

    # nettoyage emails “vides” / doublons
    clean_emails = []
    for e in sorted(emails):
        e2 = e.strip().lower()
        # garde quand même dpo@ si tu veux (important parfois)
        if "@" in e2 and "." in e2.split("@")[-1]:
            if e2 not in clean_emails:
                clean_emails.append(e2)

    clean_phones = []
    for p in sorted(phones):
        p2 = p.strip()
        if len(p2) >= 8:
            clean_phones.append(p2)

    return clean_phones, clean_emails
