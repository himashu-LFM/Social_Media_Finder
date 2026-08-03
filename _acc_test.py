import testing as t

def norm(url, plat):
    if not url:
        return ""
    return t.normalize_profile_url(url, plat).rstrip("/").lower().split("?")[0]

cases = [
    ("Bryce Dettloff", {
        "Instagram": "https://www.instagram.com/brycealakai",
        "TikTok": "https://www.tiktok.com/@brycealakaii",
    }),
    ("Gabriel Vasconcelos", {
        "Instagram": "https://www.instagram.com/gvasconcelosv",
        "TikTok": "https://www.tiktok.com/@gvasconcelosv",
    }),
]

for name, exp in cases:
    t._PLAIN_SEARCH_CACHE.clear()
    print("===", name, "===")
    slugs = t.prefetch_handle_slugs_for_talent(name, "Talent")
    for plat in ("Instagram", "TikTok"):
        out = t.search_one_platform(
            name, plat, t.PLATFORMS[plat], "Talent", "",
            discovered_handle_slugs=slugs,
        )
        ok = norm(out[1], plat) == norm(exp.get(plat, ""), plat)
        print(plat, "OK" if ok else "MISS", "|", out[1] or "blank")
