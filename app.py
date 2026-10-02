import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
from urllib.parse import urljoin, urldefrag
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="Hledač štěněte do bytu 🐾", page_icon="🐾", layout="wide")

st.title("🐾 Multi-útulkový hledač štěněte (Praha -> Celá ČR)")
st.markdown("Rychlé paralelní prohledávání **celých katalogů útulků, dočaskových spolků a Peswebu** s přísnou kontrolou věku, velikosti a vhodnosti do bytu.")

# --- POSTRANNÍ PANEL S FILTRY PRO KAMARÁDKU ---
st.sidebar.header("⚙️ 1. Nastavení parametrů")
st.sidebar.caption("Kdykoliv cokoliv změň – výsledky se samy okamžitě přepočítají!")

max_age = st.sidebar.slider("Maximální věk štěněte (měsíce)", min_value=1, max_value=12, value=6)
min_w, max_w = st.sidebar.slider("Cílová váha v dospělosti (kg)", 3, 30, (10, 15))

gender_filter = st.sidebar.radio("Pohlaví", ["Všechna", "Jen fenky ♀️", "Jen psi ♂️"])
only_short_hair = st.sidebar.checkbox("Upřednostnit krátkosrsté / hladkosrsté", value=True)
hide_garden_only = st.sidebar.checkbox("Přísně vyřadit 'pouze na zahradu / do domu'", value=True)
hide_big_breeds = st.sidebar.checkbox("Přísně vyřadit velká a silná plemena (ovčák, doga, staford, nad limit kg...)", value=True)
custom_word = st.sidebar.text_input("Hledané slovo v textu (např. kočky, děti):", "")

st.sidebar.divider()
st.sidebar.header("🌐 2. Které zdroje prohledat?")
pages_pesweb = st.sidebar.slider("Kolik stránek Peswebu prohledat (má jich 60+)", 1, 15, 6)
src_pbh = st.sidebar.checkbox("Psi bez hranic & Zachránění (Celý katalog)", value=True)
src_dede = st.sidebar.checkbox("Dočasky De De (Celý katalog - 100% do bytu)", value=True)
src_anidef = st.sidebar.checkbox("Útulek AniDef Žim (Celý katalog)", value=True)
src_voriskov = st.sidebar.checkbox("Voříškov & Dogpoint (Celé katalogy)", value=True)
show_rejected = st.sidebar.checkbox("🕵️ Ukázat dole i vyřazené psy (pro kontrolu)", value=True)

# --- SLOVNÍK ČESKÝCH SLOVNÍCH ČÍSLOVEK VĚKU ---
WORD_MONTHS = {
    "měsíční": 1, "dvouměsíční": 2, "tříměsíční": 3, "čtyřměsíční": 4,
    "pětiměsíční": 5, "šestiměsíční": 6, "půlroční": 6, "sedmiměsíční": 7,
    "osmiměsíční": 8, "devítiměsíční": 9, "desetiměsíční": 10, "jedenáctiměsíční": 11
}
WORD_YEARS = {
    "roční": 12, "jednoroční": 12, "dvouletý": 24, "dvouletá": 24,
    "tříletý": 36, "tříletá": 36, "čtyřletý": 48, "čtyřletá": 48,
    "pětiletý": 60, "pětiletá": 60, "šestiletý": 72, "šestiletá": 72,
    "sedmiletý": 84, "sedmiletá": 84, "osmiletý": 96, "osmiletá": 96
}

# --- OČIŠTĚNÍ TEXTU OD PATIČEK A REKLAM NA JINÉ PSY ---
def clean_dog_text(raw_text):
    text = raw_text
    stop_phrases = [
        "Podobní psi plemenem",
        "Další psi v útulku",
        "Další zvířata k adopci",
        "O plemeni Každý mazlíček",
        "Copyright ©",
        "Související příspěvky",
        "Mohlo by vás zajímat",
        "Sledujte nás na",
        "Přečtěte si o těch, kterým jsme společně změnili život"
    ]
    for phrase in stop_phrases:
        if phrase in text:
            text = text.split(phrase)[0]
    return text

# --- FUNKCE PRO PŘÍSNOU ANALÝZU PROFILU PSA ---
def analyze_dog(title, raw_text, source_name):
    t_low = title.lower().strip()

    # 0. Tvrdá pojistka proti informačním stránkám, článkům a již adoptovaným psům
    bad_titles = [
        "postup adopce", "podmínky adopce", "jak adoptovat", "adoptujte",
        "kontakt", "o nás", "dotazník", "jak pomoci", "psi k adopci",
        "nabídka psů", "našel domov", "našla domov", "v adopci", "co je dočasná péče",
        "dočasky de de", "externí inzerce", "virtuální adopce", "slovník",
        "odchyt psů", "naši psi", "informace k adopci", "zachránění a opuštění",
        "poptávka mazlíčka", "skill-port", "rozhovor"
    ]
    if any(b == t_low or b in t_low for b in bad_titles) or len(t_low) < 2:
        return False, -99, [], "Obecná informační stránka"

    cleaned = clean_dog_text(raw_text)
    full = (title + " " + cleaned).lower()

    inactive_phrases = [
        "tato inzerce již není aktuální", "stav: v adopci", "domov již nehledá",
        "nový domov již nehledá", "našel nový domov", "našla domov", "našel domov",
        "rezervován", "v rezervaci", "nás opustil", "přeřazen do projektu virtuální adopce"
    ]
    if any(x in full for x in inactive_phrases):
        return False, -99, [], "Již adoptován / V rezervaci / Neaktuální"

    badges = [f"🏠 {source_name}"]
    score = 0
    reasons_rejected = []

    # 1. BYT vs. ZAHRADA / KOTEC
    garden_red_flags = [
        "pouze na zahradu", "jen k domku", "nevhodný do bytu", "nehodí se do bytu",
        "ne do bytu", "výhradně k domu", "pouze k domu", "do bytu se nehodí",
        "vhodná do domečku se zahradou", "vhodný do domečku se zahradou",
        "pouze na dvorek", "kotcový režim", "pouze do domku", "jen se zahradou"
    ]
    flat_green_flags = [
        "do bytu", "v bytě", "dočasné péči", "hygienické návyky",
        "na podložku", "čistotn", "bydlení uvnitř", "vhodný domů"
    ]

    # Speciální kontrola pro Psi bez hranic: pokud mají štítek "🏡Do domu", ale nemají "Do bytu"
    only_house_pbh = ("do domu" in full and "do bytu" not in full and "Psi bez hranic" in source_name)

    if any(p in full for p in garden_red_flags) or only_house_pbh:
        badges.append("❌ Podmínka: Dům se zahradou")
        score -= 5
        if hide_garden_only:
            reasons_rejected.append("Podmínka zahrady / pouze do domu")
    elif any(p in full for p in flat_green_flags) or "Dočasky De De" in source_name:
        badges.append("✅ Vhodné do bytu / Dočaska")
        score += 3
    else:
        badges.append("⚠️ Byt v textu nezmíněn")

    # 2. PŘÍSNÁ KONTROLA VĚKU (Dospělé roky a slovní číslovky mají absolutní přednost!)
    age_months = None

    # A) Hledání explicitních roků (např. "2022 (4 roky)", "1 rok 8 měs.", "dvouletý", "roční")
    yr_and_m = re.search(r'(\d{1,2})\s*(?:rok|roky)\s*(?:a\s*)?(\d{1,2})\s*měs', full)
    adult_years_match = re.search(r'\b(\d{1,2})\s*(rok|roky|let|roční|letý|letá)\b', full)
    has_pesweb_roky_tag = bool(re.search(r'(\.\s*roky\s*\.|\.\s*rok\s*\.|\.\s*let\s*\.|věk\s*[:\s]*roky|věk\s*[:\s]*senior|\(\d+\s*rok)', full))

    word_year_val = None
    for w_yr, val_m in WORD_YEARS.items():
        if re.search(rf'\b{w_yr}\b', full):
            word_year_val = val_m
            break

    word_month_val = None
    for w_mo, val_m in WORD_MONTHS.items():
        if re.search(rf'\b{w_mo}\b', full):
            word_month_val = val_m
            break

    # Ignorujeme "na 2 měsíce" (např. "hledáme dočasku na 2 měsíce") nebo "před X měsíci"
    cleaned_for_months = re.sub(r'(na|před|za|po)\s+\d{1,2}\s*měs[a-z]*', '', full)
    m_match = re.search(r'\b(\d{1,2})\s*(měsíc|měsíční|měs\.|měs\b)', cleaned_for_months)
    w_match = re.search(r'\b(\d{1,2})\s*(týdn|týden)', full)
    born_match = re.search(r'narozen[a-z]*:\s*(?:(\d{1,2})\s*[./]\s*)?(?:(\d{1,2})\s*[./]\s*)?(20\d{2})', full)

    if yr_and_m:
        age_months = int(yr_and_m.group(1)) * 12 + int(yr_and_m.group(2))
        badges.append(f"📅 Věk: {age_months} měs.")
    elif adult_years_match:
        yrs = int(adult_years_match.group(1))
        age_months = yrs * 12
        badges.append(f"📅 Věk: {yrs} r. ({age_months} měs.)")
    elif word_year_val is not None:
        age_months = word_year_val
        badges.append(f"📅 Věk: ~{age_months // 12} r. ({age_months} měs.)")
    elif has_pesweb_roky_tag:
        age_months = 36
        badges.append("📅 Věk: Dospělý (Roky)")
    elif w_match:
        age_months = max(1, int(w_match.group(1)) // 4)
        badges.append(f"🍼 Věk: ~{w_match.group(1)} týdnů")
        score += 3
    elif m_match:
        age_months = int(m_match.group(1))
        badges.append(f"🍼 Věk: {age_months} měs.")
        score += 3
    elif word_month_val is not None:
        age_months = word_month_val
        badges.append(f"🍼 Věk: {age_months} měs.")
        score += 3
    elif born_match:
        now = datetime.now()
        b_year = int(born_match.group(3))
        b_month = int(born_match.group(2)) if born_match.group(2) else (int(born_match.group(1)) if born_match.group(1) else 6)
        if b_month > 12:
            b_month = 6
        calc_m = max(1, (now.year - b_year) * 12 + (now.month - b_month))
        age_months = calc_m
        badges.append(f"📅 Věk (dle nar.): ~{calc_m} měs.")
    elif any(k in full for k in ["měsíce", "měsíců", "štěně", "štěňátko", "štěňata"]):
        age_months = 4
        badges.append("🍼 Věk: Štěně (Měsíce)")
        score += 2
    elif any(k in full for k in ["dospělý", "senior", "psí seniorka", "starší", "stařík"]):
        age_months = 36
        badges.append("📅 Věk: Dospělý / Senior")

    if age_months is None:
        reasons_rejected.append("Věk není uvedený jako štěně/měsíce")
    elif age_months > max_age:
        reasons_rejected.append(f"Věk ({age_months} měs. > max {max_age} měs.)")

    # 3. CHYTRÁ KONTROLA VELIKOSTI A VÁHY (Rozlišení aktuální váhy štěněte vs. váhy v dospělosti!)
    big_indicators = [
        "velký (61", "61 cm a více", "velikost: velký", "velikost velký", "velké plemeno",
        "většího vzrůstu", "velkého vzrůstu", "vyroste ve většího", "bude větší", "budou větší",
        "mohutn", "statný pes", "doga", "molos", "ovčák", "malinois", "husky", "malamut",
        "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman", "beauceron",
        "středoasiat", "čuvač", "kavkaz", "bernardýn", "staford", "stafbull", "bulteriér",
        "pitbul", "bull", "amstaff", "cane corso", "bandog", "akita", "stafina"
    ]
    if any(b in full for b in big_indicators):
        badges.append("❌ Velký vzrůst / Silné plemeno")
        if hide_big_breeds:
            reasons_rejected.append("Velký vzrůst (61+ cm) nebo velké/molos/bull plemeno")

    # Hledáme explicitní odhad dospělé váhy (např. "v dospělosti 12 kg", "do 15 kg")
    adult_w_match = re.search(r'(?:dospělosti|vyroste|bude mít|odhadujeme|okolo|kolem|cca|do|max\.?|maximálně)\s*(?:cca\s*|kolem\s*|do\s*)?(\d{1,2})\s*kg', full)
    all_weights = [int(w) for w in re.findall(r'\b(\d{1,2})\s*kg', full) if 1 <= int(w) <= 65]

    if adult_w_match:
        est_adult = int(adult_w_match.group(1))
        if min_w <= est_adult <= max_w:
            badges.append(f"✅ Odhad v dospělosti: ~{est_adult} kg")
            score += 4
        elif est_adult > max_w + 2:
            badges.append(f"❌ Vyšší váha: {est_adult} kg")
            if hide_big_breeds:
                reasons_rejected.append(f"Váha {est_adult} kg (nad limit {max_w} kg)")
        else:
            badges.append(f"⚖️ Zmíněná váha: {est_adult} kg")
            score += 1
    elif all_weights:
        max_mentioned_w = max(all_weights)
        min_mentioned_w = min(all_weights)
        if min_w <= max_mentioned_w <= max_w:
            badges.append(f"✅ Váha v textu: {max_mentioned_w} kg")
            score += 3
        elif max_mentioned_w > max_w + 2:
            badges.append(f"❌ Vyšší váha v textu: {max_mentioned_w} kg")
            if hide_big_breeds:
                reasons_rejected.append(f"Váha {max_mentioned_w} kg (nad limit {max_w} kg)")
        else:
            # Štěně má zatím např. 4 kg (aktuální váha) a dospělá není číslem vypsaná
            badges.append(f"⚖️ Aktuální váha: {min_mentioned_w} kg")
            score += 1

    if any(k in full for k in ["malý (do 30", "střední (31", "menší střední", "středního vzrůstu", "střední velikosti", "střední ·", "malý ·", "drobná", "malého vzrůstu"]):
        badges.append("✅ Malý / Střední vzrůst")
        score += 2

    # 4. SRST
    if any(k in full for k in ["dlouhosrst", "hustý kožíšek", "chlupat", "delší srst"]):
        badges.append("⚠️ Delší srst")
        if only_short_hair:
            score -= 2
    elif any(k in full for k in ["krátkosrst", "hladkosrst"]):
        badges.append("✅ Krátkosrsté")
        score += 1

    # 5. POHLAVÍ
    if gender_filter == "Jen fenky ♀️":
        if not any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice", "fena", "slečna", "sestřička"]):
            if any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec", "pes", "klučík", "bratříček"]):
                reasons_rejected.append("Pohlaví (pes)")
    elif gender_filter == "Jen psi ♂️":
        if not any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec", "pes", "klučík"]):
            if any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice", "fena", "slečna", "sestřička"]):
                reasons_rejected.append("Pohlaví (fena)")

    # 6. VLASTNÍ KLÍČOVÉ SLOVO
    if custom_word.strip():
        if custom_word.strip().lower() not in full:
            reasons_rejected.append(f"Chybí slovo '{custom_word.strip()}'")
        else:
            badges.append(f"🔍 Obsahuje: '{custom_word.strip()}'")

    keep = (len(reasons_rejected) == 0)
    return keep, score, badges, ", ".join(reasons_rejected)

# --- RYCHLÉ STAŽENÍ JEDNOHO PROFILU PSA (S ODSTRANĚNÍM MENU A PATIČKY) ---
def scrape_single_dog(task):
    link, base_domain, source_label, headers, extra_card_text = task
    if "#" in link or link.endswith("/poptavka"):
        return None
    bad_url_parts = [
        "postup-adopce", "podminky-adopce", "kontakt", "o-nas", "jak-pomoci",
        "darujte", "smlouva", "dotaznik", "co-je-docasna-pece", "nabidka-kocky",
        "virtualni-adopce", "nasli-domov", "v-leceni", "slovnik-psich-plemen",
        "organizace", "odchyt-psu", "nasi-psi", "adopce", "psi-k-adopci", "skill-port-vsem"
    ]
    path_end = link.rstrip("/").split("/")[-1].lower()
    if path_end in bad_url_parts or any(b in link.lower() for b in ["postup-adopce", "podminky-adopce", "dotaznik", "virtualni-adopce", "/poptavka"]):
        return None

    try:
        r = requests.get(link, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")

        title_el = soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else "Pejsek k adopci"
        title = title.replace("— Pes k adopci", "").replace("🐾 Soukromý útulek pro opuštěné a týrané psy", "").strip()

        # Odstraníme rušivé elementy menu, hlavičky a patičky dřív, než přečteme text!
        for tag in soup.find_all(["nav", "footer", "script", "style", "aside"]):
            tag.decompose()

        text_content = (extra_card_text + " " + soup.get_text(" ", strip=True)).strip()

        # Na Dogpointu musíme ověřit, že jde o psa (má Evidenční číslo nebo Pohlaví), ne o článek z blogu
        if source_label == "Dogpoint" and not any(k in text_content.lower() for k in ["evidenční číslo", "pohlaví:", "plemeno"]):
            return None

        img_url = None
        for img in soup.find_all("img", src=True):
            src = img["src"]
            low = src.lower()
            if any(k in low for k in ["upload", "files", "wp-content", "images", "pes", "dog", "storage", "media", "supabase", "cloudinary"]):
                if not any(bad in low for bad in ["logo", "icon", "banner", "avatar", "svg", "button", "cropped-"]):
                    img_url = urljoin(base_domain, src)
                    break

        return {
            "title": title,
            "url": link,
            "text": text_content,
            "img": img_url,
            "source": source_label
        }
    except Exception:
        return None

# --- SPECIÁLNÍ ČTEČKA CELÉHO KATALOGU DOČASKY DE DE ---
def scrape_dede_cards(headers):
    dede_url = "https://www.docaskydede.cz/k-adopci/nabidka-psu/"
    dede_dogs = []
    try:
        r = requests.get(dede_url, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        for heading in soup.find_all(["h2", "h3", "h4"]):
            name = heading.get_text(strip=True)
            if not name or len(name) > 35 or any(x in name.lower() for x in ["psi k adopci", "menu", "kontakt", "hledáte"]):
                continue
            card = heading.find_parent(["div", "article", "li"])
            if not card:
                continue
            card_text = card.get_text(" ", strip=True)
            if not any(k in card_text.lower() for k in ["kříženec", "pes", "fena", "měsíc", "rok"]):
                continue
            fb_link = dede_url
            for a in card.find_all("a", href=True):
                if "facebook.com" in a["href"] or "fb.com" in a["href"]:
                    fb_link = a["href"]
                    break
            img_url = None
            img_el = card.find("img", src=True)
            if img_el:
                img_url = urljoin(dede_url, img_el["src"])

            dede_dogs.append({
                "title": f"{name} (Dočasky De De)",
                "url": fb_link,
                "text": f"{card_text} - V dočasné péči v bytě (Dočasky De De).",
                "img": img_url,
                "source": "Dočasky De De"
            })
    except Exception:
        pass
    unique = {d["title"]: d for d in dede_dogs}
    return list(unique.values())

# --- SPECIÁLNÍ ČTEČKA PRO PSI BEZ HRANIC & ZACHRÁNĚNÍ A OPUŠTĚNÍ ---
def scrape_pbh_and_zachraneni(headers):
    results = []
    # 1. Katalog psibezhranic.com/psi-k-adopci (zachytíme i štítky z přehledové karty!)
    for pbh_url in ["https://psibezhranic.com/psi-k-adopci", "https://psibezhranic.com/"]:
        try:
            r = requests.get(pbh_url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/psi-k-adopci/" in href and not href.rstrip("/").endswith("/psi-k-adopci") and "#" not in href:
                    full_u = urldefrag(urljoin("https://psibezhranic.com", href))[0]
                    card_text = a.get_text(" ", strip=True)
                    img_el = a.find("img", src=True)
                    img_url = urljoin("https://psibezhranic.com", img_el["src"]) if img_el else None
                    slug_name = full_u.rstrip("/").split("/")[-1].capitalize()
                    results.append({
                        "title": f"{slug_name} (Psi bez hranic)",
                        "url": full_u,
                        "text": card_text,
                        "img": img_url,
                        "source": "Psi bez hranic",
                        "needs_detail": True
                    })
        except Exception:
            pass

    # 2. Všechny stránky na zachraneniaopusteni.cz (strana 1 až 3)
    for z_url in ["https://zachraneniaopusteni.cz/", "https://zachraneniaopusteni.cz/page/2/", "https://zachraneniaopusteni.cz/page/3/"]:
        try:
            r = requests.get(z_url, headers=headers, timeout=8)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for block in soup.find_all(["article", "div", "li"]):
                txt = block.get_text(" ", strip=True)
                if len(txt) < 80 or len(txt) > 1200:
                    continue
                if any(k in txt for k in ["Hodí se k dětem", "Do bytu", "hledá domov", "čeká na svůj nový domov"]):
                    h_el = block.find(["h2", "h3", "h4", "strong"])
                    name = h_el.get_text(strip=True) if h_el else txt[:25]
                    if len(name) < 2 or "organizace" in name.lower() or "upozornění" in name.lower():
                        continue
                    a_el = block.find("a", href=True)
                    link = urljoin(z_url, a_el["href"]) if (a_el and "#" not in a_el["href"]) else z_url
                    img_el = block.find("img", src=True)
                    img_url = urljoin(z_url, img_el["src"]) if img_el else None
                    results.append({
                        "title": name[:45],
                        "url": link,
                        "text": txt,
                        "img": img_url,
                        "source": "Zachránění a opuštění",
                        "needs_detail": False
                    })
        except Exception:
            continue

    unique = {d["url"] if d.get("needs_detail") else d["title"]: d for d in results}
    return list(unique.values())

# --- PARALELNÍ SBĚRAČ ZE VŠECH ZDROJŮ (S DEDUPLIKACÍ) ---
@st.cache_data(ttl=900)
def fetch_all_dogs_fast(pages_pw, use_pbh, use_dede, use_anidef, use_voriskov):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    tasks = []
    seen_urls = set()
    direct_dogs = []

    # 1. PESWEB.CZ
    for page in range(1, pages_pw + 1):
        url = f"https://www.pesweb.cz/cz/psi-k-adopci?page={page}"
        try:
            r = requests.get(url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "#" in href or "/poptavka" in href:
                    continue
                if ("/psi-k-adopci/" in href or "objid=" in href) and not href.rstrip("/").endswith("psi-k-adopci"):
                    full_u = urldefrag(urljoin("https://www.pesweb.cz", href))[0]
                    if full_u not in seen_urls and "page=" not in full_u and "link-utulek" not in full_u and "list_type=" not in full_u:
                        seen_urls.add(full_u)
                        tasks.append((full_u, "https://www.pesweb.cz", "Pesweb.cz", headers, ""))
        except Exception:
            continue

    # 2. PSI BEZ HRANIC & ZACHRÁNĚNÍ A OPUŠTĚNÍ (předáváme i štítky z přehledové karty!)
    if use_pbh:
        pbh_items = scrape_pbh_and_zachraneni(headers)
        for item in pbh_items:
            if item.get("needs_detail") and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                tasks.append((item["url"], "https://psibezhranic.com", "Psi bez hranic", headers, item.get("text", "")))
            else:
                direct_dogs.append(item)

    # 3. DOČASKY DE DE
    if use_dede:
        direct_dogs.extend(scrape_dede_cards(headers))

    # 4. ÚTULEK ANIDEF ŽIM (celý katalog)
    if use_anidef:
        for start_offset in [0, 12, 24, 36]:
            anidef_u = f"https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci?start={start_offset}" if start_offset > 0 else "https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci"
            try:
                r = requests.get(anidef_u, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "#" in href:
                        continue
                    if "/psi-k-adopci/" in href and re.search(r'/\d+-', href):
                        full_u = urldefrag(urljoin("https://www.anidef.cz", href))[0]
                        if full_u not in seen_urls:
                            seen_urls.add(full_u)
                            tasks.append((full_u, "https://www.anidef.cz", "Útulek AniDef", headers, ""))
            except Exception:
                continue

    # 5. VOŘÍŠKOV & DOGPOINT (celé katalogy)
    if use_voriskov:
        for v_url in ["https://voriskov.cz/psi-k-adopci/", "https://voriskov.cz/psi-k-adopci-externi/"]:
            try:
                r = requests.get(v_url, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    if "#" in a["href"]:
                        continue
                    href = urldefrag(urljoin(v_url, a["href"]))[0]
                    if "voriskov.cz/pejsci/" in href and href not in seen_urls:
                        seen_urls.add(href)
                        tasks.append((href, "https://voriskov.cz", "Voříškov", headers, ""))
            except Exception:
                continue

        for dp_page in ["https://www.dog-point.cz/psi-k-adopci", "https://www.dog-point.cz/psi-k-adopci?page=1", "https://www.dog-point.cz/psi-k-adopci?page=2"]:
            try:
                r = requests.get(dp_page, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    if "#" in a["href"]:
                        continue
                    href = urldefrag(urljoin("https://www.dog-point.cz", a["href"]))[0]
                    if "dog-point.cz/obsah/" in href and href not in seen_urls:
                        seen_urls.add(href)
                        tasks.append((href, "https://www.dog-point.cz", "Dogpoint", headers, ""))
            except Exception:
                continue

    # Paralelní čtení všech nasbíraných odkazů najednou (14 vláken)
    dogs = list(direct_dogs)
    with ThreadPoolExecutor(max_workers=14) as executor:
        futures = [executor.submit(scrape_single_dog, t) for t in tasks]
        for f in as_completed(futures):
            res = f.result()
            if res:
                dogs.append(res)

    # Deduplikace podle čistého jména psa, aby se jeden pes z útulku i Peswebu nezobrazoval 2x
    unique_dogs = []
    seen_names = set()
    for d in dogs:
        clean_name = re.sub(r'\(.*?\)', '', d["title"]).lower().strip()
        key = f"{clean_name}_{d.get('source', '')}" if len(clean_name) < 3 else clean_name
        if key not in seen_names:
            seen_names.add(key)
            unique_dogs.append(d)

    return unique_dogs

# --- ZÁLOŽKY APLIKACE ---
tab1, tab2, tab3 = st.tabs([
    "🐶 Automatický skener útulků",
    "🔍 Rychlý rentgen libovolného textu",
    "🚀 Ověřené 'Byt-friendly' spolky & Šablona"
])

with tab1:
    st.subheader("Živé prohledávání celých katalogů útulků a dočasek")
    st.write("Klikni na tlačítko níže. Appka paralelně přečte celé katalogy (Dočasky De De, Psi bez hranic, Zachránění a opuštění, AniDef, Voříškov, Dogpoint) + zvolený počet stránek Peswebu.")
    
    if st.button("🔄 Načíst a vyfiltrovat aktuální inzeráty ze všech zdrojů", type="primary"):
        st.cache_data.clear()
        with st.spinner("Rychle čtu celé katalogy útulků (cca 10–18 vteřin)..."):
            raw_dogs = fetch_all_dogs_fast(pages_pesweb, src_pbh, src_dede, src_anidef, src_voriskov)
            st.session_state["raw_dogs"] = raw_dogs

    if "raw_dogs" in st.session_state:
        raw_dogs = st.session_state["raw_dogs"]
        filtered = []
        rejected = []
        for d in raw_dogs:
            keep, score, badges, reason = analyze_dog(d["title"], d["text"], d.get("source", "Útulek"))
            if keep:
                filtered.append((score, badges, d))
            elif score != -99:
                rejected.append((reason, badges, d))
        
        filtered.sort(key=lambda x: x[0], reverse=True)
        st.success(f"Bleskově přečteno **{len(raw_dogs)}** unikátních psích profilů napříč útulky. Tvým přísným filtrům vlevo vyhovuje: **{len(filtered)}**")

        if not filtered:
            st.warning("Žádný z právě načtených psů neprošel všemi filtry. Mrkni hned níže do sekce '🕵️ Vyřazení psi', proč přesně vypadli, nebo vlevo posuň počet stránek Peswebu na 10–15!")

        for score, badges, d in filtered:
            with st.container(border=True):
                col1, col2 = st.columns([1, 3])
                with col1:
                    if d["img"]:
                        st.image(d["img"], use_container_width=True)
                    else:
                        st.info("📷 Fotka je přímo v odkazu")
                with col2:
                    st.markdown(f"### [{d['title']}]({d['url']})")
                    st.markdown(" &nbsp; ".join([f"`{b}`" for b in badges]))
                    snippet = clean_dog_text(d["text"])
                    if "Popis" in snippet:
                        snippet = snippet.split("Popis", 1)[-1]
                    st.write(snippet[:420] + "...")
                    st.link_button("👉 Otevřít celý inzerát a kontakt", d["url"])

        if show_rejected and rejected:
            st.divider()
            with st.expander(f"🕵 Zobrazit vyřazené psy ({len(rejected)}) a důvod jejich vyřazení"):
                for reason, badges, d in rejected[:150]:
                    st.markdown(f"* ❌ **{d['title']}** ({d.get('source')}) – [odkaz]({d['url']}) ➡️ *Vyřazeno kvůli: {reason}*")

with tab2:
    st.subheader("Rentgen inzerátu z Facebooku nebo jiného webu")
    st.write("Našla jsi štěně na Facebooku? Zkopíruj sem popis a hned uvidíš, zda v něm není skrytá podmínka zahrady nebo velkého vzrůstu.")
    user_text = st.text_area("Vlož zkopírovaný text inzerátu:", height=140)
    if st.button("Analyzovat vložený text"):
        if user_text.strip():
            keep, score, badges, reason = analyze_dog("Pejsek z inzerátu", user_text, "Ruční kontrola")
            st.markdown(" &nbsp; ".join([f"`{b}`" for b in badges]))
            if keep and score >= 2:
                st.success("🎉 Skvělý kandidát! Text odpovídá tvým parametrům.")
            elif not keep:
                st.error(f"🛑 Pozor! Inzerát nevyhovuje filtrům v levém panelu ({reason}).")
            else:
                st.info("💡 Stojí za prověření – některé parametry nejsou v textu výslovně uvedené.")

with tab3:
    st.subheader("Přímé prokliky na 'Byt-friendly' dočaskové spolky")
    st.write("Tyto spolky fungují přes dočasné péče v bytech a nemají předsudky vůči adopci do Prahy:")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        * **[Psi bez hranic – Katalog](https://psibezhranic.com/psi-k-adopci)** (+ [Zachránění a opuštění](https://zachraneniaopusteni.cz/))
        * **[Dočasky De De – Katalog psů](https://www.docaskydede.cz/k-adopci/nabidka-psu/)** (+ [Facebook](https://www.facebook.com/DocaskyDeDe))
        * **[Tlapky na cestě – Facebook](https://www.facebook.com/tlapkynaceste)** (+ [Info k adopci](https://www.tlapkynaceste.cz/adopce/))
        * **[Voříškov (u Prahy)](https://voriskov.cz/psi-k-adopci/)**
        """)
    with c2:
        st.markdown("""
        * **[Srdcem pro psy (Vrbičany u Slaného)](https://www.facebook.com/srdcempropsy)**
        * **[Útulek AniDef (Žim - 55 min po D8)](https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci)**
        * **[Štěňata v nouzi (Celá ČR)](https://www.facebook.com/stenatavnouzi)**
        * **[Dogpoint (Okolí Prahy)](https://www.dog-point.cz/psi-k-adopci)**
        """)
    
    st.divider()
    st.subheader("✉️ Šablona první zprávy (zkopíruj kliknutím vpravo nahoře v poli)")
    st.code("""Dobrý den, moc mě zaujal/a [JMÉNO ŠTĚNĚTE] a ráda bych se zeptala, zda ještě hledá domov. 

Něco málo o mně a podmínkách:
• Bydlím v Praze v bytě (hned u parku/zeleně pro každodenní procházky).
• Se štěnětem počítám jako s plnohodnotným členem domácnosti – přes den [DOPLŇ: pracuji z domova / mám vyřešené hlídání / bude samo jen X hodin po postupném zvykání].
• Hledám parťáka v dospělosti kolem 10–15 kg na výlety i městský život, počítám s pozitivní výchovou, socializací i podmínkou kastrace v dospělosti.
• Jsem pojízdná a velmi ráda za [JMÉNO ŠTĚNĚTE] přijedu na seznamovací návštěvu kamkoliv po ČR, jak vám to bude časově vyhovovat.

Můj telefon je [TVŮJ TELEFON]. Moc děkuji za zprávu!""", language="text")
