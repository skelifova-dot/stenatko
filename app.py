import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
from urllib.parse import urljoin, urldefrag
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="Hledač štěněte do bytu 🐾", page_icon="🐾", layout="wide")

st.title("🐾 Multi-útulkový hledač štěněte (Praha -> Celá ČR)")
st.markdown("Rychlé paralelní prohledávání **Peswebu** a **dočaskových spolků** s kontrolou vhodnosti do bytu.")

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
st.sidebar.header("🌐 2. Zdroje a kontrola")
pages_pesweb = st.sidebar.slider("Kolik stránek Peswebu prohledat", 1, 8, 4)
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
        "Mohlo by vás zajímat"
    ]
    for phrase in stop_phrases:
        if phrase in text:
            text = text.split(phrase)[0]
    return text

# --- FUNKCE PRO ANALÝZU TEXTU INZERÁTU ---
def analyze_dog(title, raw_text, source_name):
    t_low = title.lower().strip()

    # 0. Tvrdá pojistka proti informačním stránkám
    bad_titles = [
        "postup adopce", "podmínky adopce", "jak adoptovat", "adoptujte",
        "kontakt", "o nás", "dotazník", "jak pomoci", "psi k adopci",
        "nabídka psů", "našel domov", "v adopci", "co je dočasná péče",
        "dočasky de de", "externí inzerce", "virtuální adopce"
    ]
    if any(b == t_low or b in t_low for b in bad_titles) or len(t_low) < 2:
        return False, -99, [], "Obecná informační stránka"

    cleaned = clean_dog_text(raw_text)
    full = (title + " " + cleaned).lower()

    if any(x in full for x in ["tato inzerce již není aktuální", "stav: v adopci", "domov již nehledá"]):
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
        "na podložku", "čistotn", "bydlení uvnitř"
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

    # 2. Věk (týdny, měsíce, roky + výpočet z data narození např. "Narozen: 5/2026")
    age_months = None
    m_match = re.search(r'\b(\d{1,2})\s*(měsíc|měsíční|měs\.)', full)
    w_match = re.search(r'\b(\d{1,2})\s*(týdn|týden)', full)
    y_match = re.search(r'\b(\d{1,2})\s*(rok|roky|let|roční|letý|letá)\b', full)
    born_match = re.search(r'narozen[a-z]*:\s*(?:(\d{1,2})[./])?(20\d{2})', full)

    if w_match:
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
    elif any(k in full for k in ["věk měsíce", "měsíce", "měsíců", "štěně", "štěňátko", "štěňata", "miminko"]):
        if y_match and int(y_match.group(1)) >= 2:
            age_months = int(y_match.group(1)) * 12
            badges.append(f"📅 Věk: {y_match.group(1)} r.")
        else:
            age_months = 4
            badges.append("🍼 Věk: Štěně (Měsíce)")
            score += 2
    elif y_match:
        age_months = int(y_match.group(1)) * 12
        badges.append(f"📅 Věk: {y_match.group(1)} r.")
    elif any(k in full for k in ["věk roky", "roky", "dospělý", "senior", "psí seniorka", "starší"]):
        age_months = 36
        badges.append("📅 Věk: Dospělý / Senior")

    if age_months is not None and age_months > max_age:
        reasons_rejected.append(f"Věk ({age_months} měs. > max {max_age} měs.)")
    elif age_months is None:
        # U přímých webů útulků, kde chybí slovo štěně i věk, jde téměř vždy o dospělého psa
        if source_name != "Pesweb.cz":
            reasons_rejected.append("Chybí zmínka o štěněti (dospělý pes)")
        else:
            badges.append("❓ Věk neupřesněn")

    # 3. Váha a velikost v dospělosti
    big_breeds = [
        "německého ovčáka", "německý ovčák", "belgický ovčák", "australský ovčák", "husky",
        "malamut", "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman",
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
            badges.append(f"⚖️ Zmíněná váha: {est_adult} kg")
    elif any(k in full for k in ["menší střední", "středního vzrůstu", "velikost střední", "velikost malý"]):
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
        if not any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice", "fena"]):
            if any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec", "pes"]):
                reasons_rejected.append("Pohlaví (pes)")
    elif gender_filter == "Jen psi ♂️":
        if not any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec", "pes"]):
            if any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice", "fena"]):
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
        "virtualni-adopce", "nasli-domov", "v-leceni"
    ]
    if any(b in link.lower() for b in bad_url_parts):
        return None
    try:
        r = requests.get(link, headers=headers, timeout=7)
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else "Pejsek k adopci"

        text_content = soup.get_text(" ", strip=True)
        img_url = None
        for img in soup.find_all("img", src=True):
            src = img["src"]
            low = src.lower()
            if any(k in low for k in ["upload", "files", "wp-content", "images/zvirata", "pes", "dog", "storage"]):
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

# --- SPECIÁLNÍ ČTEČKA KARET PRO DOČASKY DE DE A DOGPOINT ---
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

# --- PARALELNÍ SBĚRAČ ZE VŠECH ZDROJŮ ---
@st.cache_data(ttl=900)
def fetch_all_dogs_fast(pages_pw, use_dede, use_anidef, use_voriskov):
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

    # 2. DOČASKY DE DE
    if use_dede:
        direct_dogs.extend(scrape_dede_cards(headers))

    # 3. ÚTULEK ANIDEF (Žim)
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

    # 4. VOŘÍŠKOV (profily mají v adrese /pejsci/) & DOGPOINT
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

        # Dogpoint
        dp_url = "https://www.dog-point.cz/psi-k-adopci"
        try:
            r = requests.get(dp_url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            count_dp = 0
            for a in soup.find_all("a", href=True):
                if "#" in a["href"]:
                    continue
                href = urldefrag(urljoin(dp_url, a["href"]))[0]
                if "dog-point.cz/" in href and any(x in href for x in ["/psi-k-adopci/", "/nasi-psi/", "/pes/"]):
                    if href.rstrip("/") != dp_url.rstrip("/") and href not in seen_urls and count_dp < 15:
                        seen_urls.add(href)
                        tasks.append((href, dp_url, "Dogpoint", headers))
                        count_dp += 1
        except Exception:
            pass

    # Paralelní čtení všech nasbíraných odkazů najednou (8 vláken)
    dogs = list(direct_dogs)
    with ThreadPoolExecutor(max_workers=8) as executor:
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
    st.subheader("Živé prohledávání útulků a dočasek")
    st.write("Klikni na tlačítko níže. Appka paralelně projde inzeráty, odstřihne z nich reklamy v patičce a vyfiltruje štěňata podle posuvníků vlevo.")
    
    if st.button("🔄 Načíst a vyfiltrovat aktuální inzeráty (Zrychlený sken)", type="primary"):
        with st.spinner("Rychle čtu profily psů napříč útulky (cca 5–10 vteřin)..."):
            raw_dogs = fetch_all_dogs_fast(pages_pesweb, src_dede, src_anidef, src_voriskov)
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
        st.success(f"Bleskově přečteno **{len(raw_dogs)}** psích profilů. Tvým filtrům vlevo vyhovuje: **{len(filtered)}**")

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
                    st.link_button("👉 Otevřít celý inzerát / FB album", d["url"])

        if show_rejected and rejected:
            st.divider()
            with st.expander(f"🕵️ Zobrazit vyřazené psy ({len(rejected)}) a důvod jejich vyřazení"):
                for reason, badges, d in rejected[:60]:
                    st.markdown(f"* ❌ **{d['title']}** ({d.get('source')}) – [odkaz]({d['url']}) ➡️ *Vyřazeno kvůli: {reason}*")

with tab2:
    st.subheader("Rentgen inzerátu z Facebooku nebo Dočasek De De")
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
    st.write("U Dočasek De De a Tlapek na cestě platí, že u všech psů je **podmínkou bydlení uvnitř v bytě/domě** (nikdy ne v kotci nebo jen na zahradě):")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        * **[Dočasky De De – Katalog psů](https://www.docaskydede.cz/k-adopci/nabidka-psu/)** (+ [Facebook](https://www.facebook.com/DocaskyDeDe))
        * **[Tlapky na cestě – Facebook](https://www.facebook.com/tlapkynaceste)** (+ [Web](https://www.tlapkynaceste.cz/adopce/))
        * **[Srdcem pro psy (Vrbičany u Slaného)](https://www.facebook.com/srdcempropsy)**
        * **[Voříškov (u Prahy)](https://voriskov.cz/psi-k-adopci/)**
        """)
    with c2:
        st.markdown("""
        * **[Útulek AniDef (Žim - 55 min po D8)](https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci)**
        * **[Dejte nám šanci (Morava)](https://www.dejtenamsanci.cz/psi-k-adopci/)**
        * **[Štěňata v nouzi (Celá ČR)](https://www.facebook.com/stenatavnouzi)**
        * **[Pesweb – Katalog](https://www.pesweb.cz/cz/psi-k-adopci)**
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
