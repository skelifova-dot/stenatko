import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin
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
st.sidebar.header("🌐 2. Zdroje a rychlost")
pages_pesweb = st.sidebar.slider("Kolik stránek Peswebu prohledat", 1, 8, 4)
src_dede = st.sidebar.checkbox("Dočasky De De (Praha - 100% do bytu)", value=True)
src_anidef = st.sidebar.checkbox("Útulek AniDef (Žim - Severní Čechy)", value=True)
src_voriskov = st.sidebar.checkbox("Voříškov & Dogpoint (Okolí Prahy)", value=True)
show_rejected = st.sidebar.checkbox("🕵️ Ukázat dole i vyřazené psy (pro kontrolu)", value=True)

# --- OČIŠTĚNÍ TEXTU OD PATIČEK A REKLAM NA JINÉ PSY ---
def clean_dog_text(raw_text):
    text = raw_text
    # Odstřihneme spodní patičku Peswebu a dalších webů, aby slova z jiných inzerátů nemátla filtr
    stop_phrases = [
        "Podobní psi plemenem",
        "Další psi v útulku",
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

    # 0. Tvrdá pojistka proti informačním článkům (Postup adopce, Podmínky apod.)
    bad_titles = [
        "postup adopce", "podmínky adopce", "jak adoptovat", "adoptujte",
        "kontakt", "o nás", "dotazník", "jak pomoci", "psi k adopci",
        "nabídka psů", "našel domov", "v adopci", "co je dočasná péče"
    ]
    if any(b in t_low for b in bad_titles) or len(t_low) < 2:
        return False, -99, [], "Obecná informační stránka (není profil psa)"

    cleaned = clean_dog_text(raw_text)
    full = (title + " " + cleaned).lower()

    # Vyřazení již adoptovaných psů (Pesweb + AniDef)
    if any(x in full for x in ["tato inzerce již není aktuální", "stav: v adopci", "domov již nehledá"]):
        return False, -99, [], "Pejsek už našel domov (v adopci)"

    badges = [f"🏠 {source_name}"]
    score = 0
    reasons_rejected = []

    # 1. Byt vs. Zahrada
    garden_red_flags = [
        "pouze na zahradu", "jen k domku", "nevhodný do bytu",
        "ne do bytu", "výhradně k domu", "pouze k domu", "do bytu se nehodí"
    ]
    flat_green_flags = [
        "do bytu", "v bytě", "dočasné péči", "hygienické návyky",
        "na podložku", "čistotn", "bydlení uvnitř"
    ]

    if any(p in full for p in garden_red_flags):
        badges.append("❌ Podmínka: Pouze zahrada")
        score -= 5
        if hide_garden_only:
            reasons_rejected.append("Podmínka zahrady")
    elif any(p in full for p in flat_green_flags) or "Dočasky De De" in source_name:
        badges.append("✅ Vhodné do bytu / Dočaska")
        score += 3
    else:
        badges.append("⚠️ Byt v textu nezmíněn")

    # 2. Věk (hledání týdnů, měsíců, let a štítků Peswebu/Dočasek)
    age_months = None
    m_match = re.search(r'\b(\d{1,2})\s*(měsíc|měsíční|měs\.)', full)
    w_match = re.search(r'\b(\d{1,2})\s*(týdn|týden)', full)
    y_match = re.search(r'\b(\d{1,2})\s*(rok|roky|let|roční|letý|letá)\b', full)

    if w_match:
        age_months = max(1, int(w_match.group(1)) // 4)
        badges.append(f"🍼 Věk: ~{w_match.group(1)} týdnů")
        score += 3
    elif m_match:
        age_months = int(m_match.group(1))
        badges.append(f"🍼 Věk: {age_months} měs.")
        score += 3
    elif any(k in full for k in ["věk měsíce", "štěně", "štěňátko", "štěňata", "miminko"]):
        # Pozor, aby v textu nebylo "odrostlé štěně (2 roky)"
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
    elif "věk roky" in full or "dospělý" in full or "senior" in full:
        age_months = 24
        badges.append("📅 Věk: Dospělý (Roky)")

    if age_months is not None and age_months > max_age:
        reasons_rejected.append(f"Věk ({age_months} měs. > max {max_age} měs.)")
    elif age_months is None:
        badges.append("❓ Věk v textu nerozpoznán")

    # 3. Váha a velikost v dospělosti (kontrolujeme už jen očištěný text!)
    big_breeds = [
        "německého ovčáka", "německý ovčák", "belgický ovčák", "husky", "malamut",
        "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman",
        "středoasiat", "čuvač", "většího vzrůstu", "velkého vzrůstu", "velikost velký"
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
        if not any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice"]):
            if any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec"]):
                reasons_rejected.append("Pohlaví (pes)")
    elif gender_filter == "Jen psi ♂️":
        if not any(w in full for w in ["pejsek", "kluk", "chlapeček", "pohlaví pes", "pohlaví: samec"]):
            if any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice"]):
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
    bad_url_parts = [
        "postup-adopce", "podminky-adopce", "kontakt", "o-nas", "jak-pomoci",
        "darujte", "smlouva", "dotaznik", "co-je-docasna-pece", "nabidka-kocky"
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
            if any(k in low for k in ["upload", "files", "wp-content", "images/zvirata", "pes", "dog"]):
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

# --- PARALELNÍ SBĚRAČ ZE VŠECH ZDROJŮ (3x RYCHLEJŠÍ) ---
@st.cache_data(ttl=900)
def fetch_all_dogs_fast(pages_pw, use_dede, use_anidef, use_voriskov):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    tasks = []
    seen_urls = set()

    # 1. PESWEB.CZ (ověřené stránkování katalogu)
    for page in range(1, pages_pw + 1):
        url = f"https://www.pesweb.cz/cz/psi-k-adopci?page={page}"
        try:
            r = requests.get(url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ("/psi-k-adopci/" in href or "objid=" in href) and not href.endswith("psi-k-adopci"):
                    full_u = urljoin("https://www.pesweb.cz", href)
                    if full_u not in seen_urls and "page=" not in full_u and "link-utulek" not in full_u:
                        seen_urls.add(full_u)
                        tasks.append((full_u, "https://www.pesweb.cz", "Pesweb.cz", headers))
        except Exception:
            continue

    # 2. DOČASKY DE DE (opravená skutečná URL nabídky psů!)
    if use_dede:
        dede_catalog = "https://www.docaskydede.cz/k-adopci/nabidka-psu/"
        try:
            r = requests.get(dede_catalog, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = urljoin(dede_catalog, a["href"])
                if "docaskydede.cz" in href and href != dede_catalog:
                    # Profily psů na Dočasky De De mají specifickou cestu mimo obecné menu
                    if "/k-adopci/nabidka-psu/" in href or "/pes/" in href or "/nabidka-psu/" in href:
                        if href not in seen_urls:
                            seen_urls.add(href)
                            tasks.append((href, "https://www.docaskydede.cz", "Dočasky De De", headers))
        except Exception:
            pass

    # 3. ÚTULEK ANIDEF (Žim)
    if use_anidef:
        try:
            r = requests.get("https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci", headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            count_a = 0
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/psi-k-adopci/" in href and re.search(r'/\d+-', href):
                    full_u = urljoin("https://www.anidef.cz", href)
                    if full_u not in seen_urls and count_a < 18:
                        seen_urls.add(full_u)
                        tasks.append((full_u, "https://www.anidef.cz", "Útulek AniDef", headers))
                        count_a += 1
        except Exception:
            pass

    # 4. VOŘÍŠKOV & DOGPOINT
    if use_voriskov:
        for base_u, label in [("https://voriskov.cz/psi-k-adopci/", "Voříškov"), ("https://www.dog-point.cz/psi-k-adopci", "Dogpoint")]:
            try:
                r = requests.get(base_u, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                count_v = 0
                for a in soup.find_all("a", href=True):
                    href = urljoin(base_u, a["href"])
                    if ("voriskov.cz/psi-k-adopci/" in href or "dog-point.cz/psi-k-adopci/" in href) and href != base_u:
                        if href not in seen_urls and "page" not in href and count_v < 12:
                            seen_urls.add(href)
                            tasks.append((href, base_u, label, headers))
                            count_v += 1
            except Exception:
                continue

    # Paralelní čtení všech nasbíraných odkazů najednou (8 vláken)
    dogs = []
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
                        st.info("📷 Fotka je přímo v inzerátu")
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
                for reason, badges, d in rejected[:40]:
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
