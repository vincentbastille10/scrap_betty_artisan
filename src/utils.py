from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

TRACKING_PARAMS_PREFIXES = ("utm_",)
DROP_PARAMS = {"fbclid", "gclid"}

def safe_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url

def normalize_url(url: str) -> str:
    """
    - supprime fragments
    - nettoie paramètres de tracking
    - normalise scheme/host en lowercase
    """
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or "/"
    # clean query
    q = []
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        kl = k.lower()
        if kl in DROP_PARAMS:
            continue
        if any(kl.startswith(pref) for pref in TRACKING_PARAMS_PREFIXES):
            continue
        q.append((k, v))
    query = urlencode(q, doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))

def same_domain(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.netloc or "").lower() == (pb.netloc or "").lower()
