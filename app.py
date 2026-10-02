import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
from urllib.parse import urljoin, urldefrag
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="Hledač štěněte do bytu 🐾", page_icon="🐾", layout="wide")

st.title("🐾 Multi-útulkový hledač štěněte (Praha -> Celá ČR)")
st.markdown("Rychlé paralelní prohledávání **ověřených českých katalogů, útulků a dočaskových spolků** s kontrolou vhodnosti do bytu.")

# --- POSTRANNÍ PANEL S FILTRY PRO KAMARÁDKU ---
st.sidebar.header("⚙️ 1. Nastavení parametrů")
st.sidebar.caption("Kdykoliv cokoliv změň – výsledky se samy okamžitě přepočítají!")

max_age = st.sidebar.slider("Maximální věk (měsíce)", min_value=1, max_value=12, value=6)
min_w, max_w = st.sidebar.slider("Cílová váha v dospělosti (kg)", 3, 30, (10, 15))

gender_filter = st.sidebar.radio("Pohlaví", ["Všechna", "Jen fenky ♀️", "Jen psi ♂️"])
only_short_hair = st.sidebar.checkbox("Upřednostnit krátkosrsté / hladkosrsté", value=True)
hide_garden_only = st.sidebar.checkbox("Přísně vyřadit 'pouze na zahradu'", value=True)
hide_big_breeds = st.sidebar.checkbox("Vyřadit křížence velkých plemen (ovčák, husky...)", value=True)
custom_word = st.sidebar.text_input("Hledané slovo v textu (např. kočky, děti):", "")

st.sidebar.divider()
st.sidebar.header("🌐 2. Které zdroje prohledat?")
pages_pesweb = st.sidebar.slider("Kolik stránek Peswebu prohledat", 1, 8, 4)
src_pbh = st.sidebar.checkbox("Psi bez hranic & Zachránění (Mnoho štěňat!)", value=True)
src_dede = st.sidebar.checkbox("Dočasky De De (Praha - 100% do bytu)", value=True)
src_anidef = st.sidebar.checkbox("Útulek AniDef (Žim - Severní Čechy)", value=True)
src_voriskov = st.sidebar.checkbox("Voříškov & Dogpoint (Okolí Prahy)", value=True)
show_rejected = st.sidebar.checkbox("🕵️ Ukázat dole i vyřazené psy (pro kontrolu)", value=True)

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
        "Sledujte nás na"
    ]
    for phrase in stop_phrases:
        if phrase in text:
            text = text.split(phrase)[0]
    return text

# --- FUNKCE PRO ANALÝZU TEXTU INZERÁTU ---
def analyze_dog(title, raw_text, source_name):
    t_low = title.lower().strip()

    # 0. Tvrdá pojistka proti informačním stránkám a menu
    bad_titles = [
        "postup adopce", "podmínky adopce", "jak adoptovat", "adoptujte",
        "kontakt", "o nás", "dotazník", "jak pomoci", "psi k adopci",
        "nabídka psů", "našel domov", "v adopci", "co je dočasná péče",
        "dočasky de de", "externí inzerce", "virtuální adopce", "slovník",
        "odchyt psů", "naši psi", "informace k adopci", "zachránění a opuštění"
    ]
    if any(b == t_low or b in t_low for b in bad_titles) or len(t_low) < 2:
        return False, -99, [], "Obecná informační stránka"

    cleaned = clean_dog_text(raw_text)
    full = (title + " " + cleaned).lower()

    if any(x in full for x in ["tato inzerce již není aktuální", "stav: v adopci", "domov již nehledá", "našel nový domov"]):
        return False, -99, [], "Pejsek už našel domov (v adopci)"

    badges = [f"🏠 {source_name}"]
    score = 0
    reasons_rejected = []

    # 1. Byt vs. Zahrada
    garden_red_flags = [
        "pouze na zahradu", "jen k domku", "nevhodný do bytu", "nehodí se do bytu",
        "ne do bytu", "výhradně k domu", "pouze k domu", "do bytu se nehodí",
        "vhodná do domečku se zahradou", "vhodný do domečku se zahradou"
    ]
    flat_green_flags = [
        "do bytu", "v bytě", "dočasné péči", "hygienické návyky",
        "na podložku", "čistotn", "bydlení uvnitř", "vhodný domů"
    ]

    if any(p in full for p in garden_red_flags):
        badges.append("❌ Podmínka: Dům se zahradou")
        score -= 5
        if hide_garden_only:
            reasons_rejected.append("Podmínka zahrady")
    elif any(p in full for p in flat_green_flags) or "Dočasky De De" in source_name:
        badges.append("✅ Vhodné do bytu / Dočaska")
        score += 3
    else:
        badges.append("⚠️ Byt v textu nezmíněn")

    # 2. Věk (týdny, měsíce, roky + výpočet z data narození)
    age_months = None
    # Kontrola "1 rok a X měsíců", aby se nepletlo se samotnými měsíci
    yr_and_m = re.search(r'(\d{1,2})\s*(?:rok|roky)\s*(?:a\s*)?(\d{1,2})\s*měs', full)
    m_match = re.search(r'\b(\d{1,2})\s*(měsíc|měsíční|měs\.|měs\b)', full)
    w_match = re.search(r'\b(\d{1,2})\s*(týdn|týden)', full)
    y_match = re.search(r'\b(\d{1,2})\s*(rok|roky|let|roční|letý|letá)\b', full)
    born_match = re.search(r'narozen[a-z]*:\s*(?:(\d{1,2})[./])?(20\d{2})', full)

    if yr_and_m:
        age_months = int(yr_and_m.group(1)) * 12 + int(yr_and_m.group(2))
        badges.append(f"📅 Věk: {age_months} měs.")
    elif w_match:
        age_months = max(1, int(w_match.group(1)) // 4)
        badges.append(f"🍼 Věk: ~{w_match.group(1)} týdnů")
        score += 3
    elif m_match:
        age_months = int(m_match.group(1))
        badges.append(f"🍼 Věk: {age_months} měs.")
        score += 3
    elif born_match:
        now = datetime.now()
        b_month = int(born_match.group(1)) if born_match.group(1) else 6
        b_year = int(born_match.group(2))
        calc_m = max(1, (now.year - b_year) * 12 + (now.month - b_month))
        age_months = calc_m
        badges.append(f"📅 Věk (dle nar.): ~{calc_m} měs.")
    elif any(k in full for k in ["věk měsíce", "měsíce", "měsíců", "štěně", "štěňátko", "štěňata", "miminko", "prcek"]):
        if y_match and int(y_match.group(1)) >= 1:
            age_months = int(y_match.group(1)) * 12
            badges.append(f"📅 Věk: {y_match.group(1)} r.")
        else:
            age_months = 4
            badges.append("🍼 Věk: Štěně (Měsíce)")
            score += 2
    elif y_match:
        age_months = int(y_match.group(1)) * 12
        badges.append(f"📅 Věk: {y_match.group(1)} r.")
    elif any(k in full for k in ["věk roky", "roky", "rok", "let", "dospělý", "senior", "psí seniorka", "starší", "stařík"]):
        age_months = 36
        badges.append("📅 Věk: Dospělý / Senior")

    if age_months is not None and age_months > max_age:
        reasons_rejected.append(f"Věk ({age_months} měs. > max {max_age} měs.)")
    elif age_months is None:
        if source_name != "Pesweb.cz":
            reasons_rejected.append("Chybí zmínka o štěněti (dospělý pes)")
        else:
            badges.append("❓ Věk neupřesněn")

    # 3. Váha a velikost v dospělosti
    big_breeds = [
        "německého ovčáka", "německý ovčák", "belgický ovčák", "australský ovčák", "mladý ovčák",
        "husky", "malamut", "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman",
        "středoasiat", "čuvač", "doga", "většího vzrůstu", "velkého vzrůstu", "velikost velký"
    ]
    if any(b in full for b in big_breeds):
        badges.append("⚠️ Riziko velkého plemene/vzrůstu")
        if hide_big_breeds:
            reasons_rejected.append("Velké plemeno nebo velký vzrůst")

    weights = [int(w) for w in re.findall(r'\b(\d{1,2})\s*kg', full)]
    if weights:
        est_adult = max(weights)
        if min_w <= est_adult <= max_w:
            badges.append(f"✅ Váha v textu: {est_adult} kg")
            score += 3
        elif est_adult > max_w + 4:
            badges.append(f"❌ Vyšší váha: {est_adult} kg")
            if hide_big_breeds:
                reasons_rejected.append(f"Váha {est_adult} kg")
        else:
            badges.append(f"⚖️️ Zmíněná váha: {est_adult} kg")
    elif any(k in full for k in ["menší střední", "středního vzrůstu", "střední velikosti", "velikost střední", "velikost malý", "střední ·", "malý ·", "drobná"]):
        badges.append("✅ Malý / Střední vzrůst")
        score += 1

    # 4. Srst
    if any(k in full for k in ["dlouhosrst", "hustý kožíšek", "chlupat"]):
        badges.append("⚠️ Delší srst")
        if only_short_hair:
            score -= 2
    elif any(k in full for k in ["krátkosrst", "hladkosrst"]):
        badges.append("✅ Krátkosrsté")
        score += 1

    # 5. Pohlaví
    if gender_filter == "Jen fenky ♀️":
        if not any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice", "fena", "slečna", "princezna", "sestřička"]):
            if any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec", "pes", "klučík", "bratříček"]):
                reasons_rejected.append("Pohlaví (pes)")
    elif gender_filter == "Jen psi ♂️":
        if not any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec", "pes", "klučík"]):
            if any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice", "fena", "slečna", "princezna", "sestřička"]):
                reasons_rejected.append("Pohlaví (fena)")

    # 6. Vlastní klíčové slovo
    if custom_word.strip():
        if custom_word.strip().lower() not in full:
            reasons_rejected.append(f"Chybí slovo '{custom_word.strip()}'")
        else:
            badges.append(f"🔍 Obsahuje: '{custom_word.strip()}'")

    keep = (len(reasons_rejected) == 0)
    return keep, score, badges, ", ".join(reasons_rejected)

# --- RYCHLÉ STAŽENÍ JEDNOHO PROFILU PSA ---
def scrape_single_dog(task):
    link, base_domain, source_label, headers = task
    if "#" in link:
        return None
    bad_url_parts = [
        "postup-adopce", "podminky-adopce", "kontakt", "o-nas", "jak-pomoci",
        "darujte", "smlouva", "dotaznik", "co-je-docasna-pece", "nabidka-kocky",
        "virtualni-adopce", "nasli-domov", "v-leceni", "slovnik-psich-plemen",
        "organizace", "odchyt-psu", "nasi-psi", "adopce"
    ]
    # Povolíme /psi-k-adopci/jmeno, ale zakážeme samotné /adopce
    path_end = link.rstrip("/").split("/")[-1].lower()
    if path_end in bad_url_parts or any(b in link.lower() for b in ["postup-adopce", "podminky-adopce", "dotaznik", "virtualni-adopce"]):
        return None

    try:
        r = requests.get(link, headers=headers, timeout=7)
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else "Pejsek k adopci"
        # Očištění titulku u Psi bez hranic a Dogpointu
        title = title.replace("— Pes k adopci", "").replace("🐾 Soukromý útulek pro opuštěné a týrané psy", "").strip()

        text_content = soup.get_text(" ", strip=True)
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

# --- SPECIÁLNÍ ČTEČKA KARET PRO DOČASKY DE DE ---
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

# --- SPECIÁLNÍ ČTEČKA PRO ZACHRÁNĚNÍ A OPUŠTĚNÍ & PSI BEZ HRANIC ---
def scrape_pbh_and_zachraneni(headers):
    results = []
    # 1. Karty na hlavní stránce psibezhranic.com (např. Neria 5 měs., Jamie 7 měs.)
    pbh_home = "https://psibezhranic.com/"
    try:
        r = requests.get(pbh_home, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/psi-k-adopci/" in href and not href.endswith("/psi-k-adopci") and "#" not in href:
                full_u = urldefrag(urljoin(pbh_home, href))[0]
                card_text = a.get_text(" ", strip=True)
                img_el = a.find("img", src=True)
                img_url = urljoin(pbh_home, img_el["src"]) if img_el else None
                if len(card_text) > 5:
                    # Vytáhneme jméno pejska z karty nebo z URL
                    slug_name = full_u.rstrip("/").split("/")[-1].capitalize()
                    results.append({
                        "title": f"{slug_name} (Psi bez hranic)",
                        "url": full_u,
                        "text": card_text + " Do bytu, rodinné prostředí (Psi bez hranic).",
                        "img": img_url,
                        "source": "Psi bez hranic",
                        "needs_detail": True
                    })
    except Exception:
        pass

    # 2. Výpis psů na zachraneniaopusteni.cz (Elara 5 měs., Holly 5 měs., Silvi 4 měs., Tarti 4 měs.)
    z_url = "https://zachraneniaopusteni.cz/"
    try:
        r = requests.get(z_url, headers=headers, timeout=8)
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
        pass

    unique = {d["title"]: d for d in results}
    return list(unique.values())

# --- PARALELNÍ SBĚRAČ ZE VŠECH ZDROJŮ ---
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
                if "#" in href:
                    continue
                if ("/psi-k-adopci/" in href or "objid=" in href) and not href.endswith("psi-k-adopci"):
                    full_u = urldefrag(urljoin("https://www.pesweb.cz", href))[0]
                    if full_u not in seen_urls and "page=" not in full_u and "link-utulek" not in full_u:
                        seen_urls.add(full_u)
                        tasks.append((full_u, "https://www.pesweb.cz", "Pesweb.cz", headers))
        except Exception:
            continue

    # 2. PSI BEZ HRANIC & ZACHRÁNĚNÍ A OPUŠTĚNÍ
    if use_pbh:
        pbh_items = scrape_pbh_and_zachraneni(headers)
        for item in pbh_items:
            if item.get("needs_detail") and item["url"] not in seen_urls:
                seen_urls.add(item["url"])
                tasks.append((item["url"], "https://psibezhranic.com", "Psi bez hranic", headers))
            else:
                direct_dogs.append(item)

    # 3. DOČASKY DE DE
    if use_dede:
        direct_dogs.extend(scrape_dede_cards(headers))

    # 4. ÚTULEK ANIDEF (Žim)
    if use_anidef:
        try:
            r = requests.get("https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci", headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            count_a = 0
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "#" in href:
                    continue
                if "/psi-k-adopci/" in href and re.search(r'/\d+-', href):
                    full_u = urldefrag(urljoin("https://www.anidef.cz", href))[0]
                    if full_u not in seen_urls and count_a < 18:
                        seen_urls.add(full_u)
                        tasks.append((full_u, "https://www.anidef.cz", "Útulek AniDef", headers))
                        count_a += 1
        except Exception:
            pass

    # 5. VOŘÍŠKOV (/pejsci/) & DOGPOINT (/obsah/)
    if use_voriskov:
        for v_url in ["https://voriskov.cz/psi-k-adopci/", "https://voriskov.cz/psi-k-adopci-externi/"]:
            try:
                r = requests.get(v_url, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                count_v = 0
                for a in soup.find_all("a", href=True):
                    if "#" in a["href"]:
                        continue
                    href = urldefrag(urljoin(v_url, a["href"]))[0]
                    if "voriskov.cz/pejsci/" in href and href not in seen_urls and count_v < 15:
                        seen_urls.add(href)
                        tasks.append((href, "https://voriskov.cz", "Voříškov", headers))
                        count_v += 1
            except Exception:
                continue

        # Dogpoint – profily psů mají na webu cestu /obsah/jmeno-psa
        dp_url = "https://www.dog-point.cz/psi-k-adopci"
        try:
            r = requests.get(dp_url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            count_dp = 0
            for a in soup.find_all("a", href=True):
                if "#" in a["href"]:
                    continue
                href = urldefrag(urljoin(dp_url, a["href"]))[0]
                if "dog-point.cz/obsah/" in href and href not in seen_urls and count_dp < 15:
                    seen_urls.add(href)
                    tasks.append((href, "https://www.dog-point.cz", "Dogpoint", headers))
                    count_dp += 1
        except Exception:
            pass

    # Paralelní čtení všech nasbíraných odkazů najednou (10 vláken)
    dogs = list(direct_dogs)
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(scrape_single_dog, t) for t in tasks]
        for f in as_completed(futures):
            res = f.result()
            if res:
                dogs.append(res)

    return dogs

# --- ZÁLOŽKY APLIKACE ---
tab1, tab2, tab3 = st.tabs([
    "🐶 Automatický skener útulků",
    "🔍 Rychlý rentgen libovolného textu",
    "🚀 Ověřené 'Byt-friendly' spolky & Šablona"
])

with tab1:
    st.subheader("Živé prohledávání ověřených útulků a dočasek")
    st.write("Klikni na tlačítko níže. Appka paralelně projde Pesweb, Psi bez hranic, Zachránění a opuštění, Dočasky De De, AniDef, Voříškov i Dogpoint.")
    
    if st.button("🔄 Načíst a vyfiltrovat aktuální inzeráty ze všech zdrojů", type="primary"):
        with st.spinner("Rychle čtu profily psů napříč útulky (cca 6–12 vteřin)..."):
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
        st.success(f"Bleskově přečteno **{len(raw_dogs)}** psích profilů napříč útulky. Tvým filtrům vlevo vyhovuje: **{len(filtered)}**")

        if not filtered:
            st.warning("Žádný z právě načtených psů neprošel všemi filtry. Mrkni hned níže do sekce '🕵️ Vyřazení psi', proč přesně vypadli, nebo vlevo posuň počet stránek Peswebu na 6–8!")

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
            with st.expander(f"🕵️ Zobrazit vyřazené psy ({len(rejected)}) a důvod jejich vyřazení"):
                for reason, badges, d in rejected[:80]:
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
        * **[Psi bez hranic – Katalog](https://psibezhranic.com/)** (+ [Zachránění a opuštění](https://zachraneniaopusteni.cz/))
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
