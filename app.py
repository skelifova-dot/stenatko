import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

st.set_page_config(page_title="Hledač štěněte do bytu 🐾", page_icon="🐾", layout="wide")

st.title("🐾 Chytrý hledač štěněte (Praha -> Celá ČR)")
st.markdown("Automatické prohledávání útulků a čtení inzerátů podle tvých parametrů.")

# --- POSTRANNÍ PANEL S FILTRY ---
st.sidebar.header("⚙️ 1. Nastavení filtrů")
st.sidebar.caption("Kdykoliv cokoliv změň – seznam se hned sám přepočítá!")

max_age = st.sidebar.slider("Maximální věk (měsíce)", min_value=1, max_value=12, value=6)
min_w, max_w = st.sidebar.slider("Cílová váha v dospělosti (kg)", 3, 30, (10, 15))

gender_filter = st.sidebar.radio("Pohlaví", ["Všechna", "Jen fenky ♀️", "Jen psi ♂️"])
only_short_hair = st.sidebar.checkbox("Upřednostnit krátkosrsté / hladkosrsté", value=True)
hide_garden_only = st.sidebar.checkbox("Přísně vyřadit 'pouze na zahradu'", value=True)
hide_big_breeds = st.sidebar.checkbox("Vyřadit křížence velkých plemen (ovčák, husky...)", value=True)
custom_word = st.sidebar.text_input("Hledané slovo v textu (např. kočky, děti):", "")

st.sidebar.divider()
st.sidebar.header("🌐 2. Rozsah hledání")
pages_to_scan = st.sidebar.slider("Kolik stránek Peswebu prohledat", 1, 6, 3)
include_shelters = st.sidebar.checkbox("Přidat přímé weby (AniDef, Dočasky De De, Voříškov)", value=True)
show_rejected = st.sidebar.checkbox("🕵️ Ukázat dole i vyřazené psy (pro kontrolu)", value=False)

# --- OČIŠTĚNÍ TEXTU OD PATIČEK A MENU ---
def clean_dog_text(raw_text):
    text = raw_text
    # Ořízneme spodní patičku Peswebu ("Podobní psi plemenem", "Další psi v útulku"), která dříve mátla filtr!
    for stop_phrase in ["Podobní psi plemenem", "Další psi v útulku", "O plemeni Každý mazlíček"]:
        if stop_phrase in text:
            text = text.split(stop_phrase)[0]
    return text

# --- FUNKCE PRO ANALÝZU TEXTU INZERÁTU ---
def analyze_dog(title, raw_text, source_name):
    t_low = title.lower()
    # 0. Pojistka proti informačním stránkám (Postup adopce, Podmínky apod.)
    bad_titles = ["postup adopce", "podmínky adopce", "adoptujte", "jak adoptovat", "kontakt", "o nás", "našel domov", "v adopci"]
    if any(b in t_low for b in bad_titles):
        return False, -99, [], "Informační stránka / Již adoptován"

    cleaned = clean_dog_text(raw_text)
    full = (title + " " + cleaned).lower()

    if "tato inzerce již není aktuální" in full or "stav: v adopci" in full:
        return False, -99, [], "Již v adopci (neaktuální)"

    badges = [f"🏠 {source_name}"]
    score = 0
    reasons_rejected = []

    # 1. Byt vs. Zahrada
    garden_red_flags = ["pouze na zahradu", "jen k domku", "nevhodný do bytu", "ne do bytu", "výhradně k domu", "pouze k domu"]
    flat_green_flags = ["do bytu", "v bytě", "dočasné péči", "hygienické návyky", "na podložku", "čistotn"]

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

    # 2. Věk (hledání měsíců / týdnů / let)
    age_months = None
    m_match = re.search(r'\b(\d{1,2})\s*(měsíc|měsíční|měs\.)', full)
    w_match = re.search(r'\b(\d{1,2})\s*(týdn|týden)', full)
    y_match = re.search(r'\b(\d{1,2})\s*(rok|roky|let|roční|letý|letá)\b', full)

    if w_match:
        age_months = max(1, int(w_match.group(1)) // 4)
        badges.append(f"🍼 Věk: ~{w_match.group(1)} týdnů")
        score += 2
    elif m_match:
        age_months = int(m_match.group(1))
        badges.append(f"🍼 Věk: {age_months} měs.")
        score += 2
    elif "štěně" in full or "štěňátko" in full or "miminko" in full or "měsíce" in full:
        if y_match and int(y_match.group(1)) >= 1 and "měsíc" not in full:
            age_months = int(y_match.group(1)) * 12
            badges.append(f"📅 Věk: {y_match.group(1)} r.")
        else:
            age_months = 4
            badges.append("🍼 Věk: Štěně")
            score += 2
    elif y_match:
        age_months = int(y_match.group(1)) * 12
        badges.append(f"📅 Věk: {y_match.group(1)} r.")
    elif "roky" in full:
        age_months = 24
        badges.append("📅 Věk: Dospělý (Roky)")

    if age_months is not None and age_months > max_age:
        reasons_rejected.append(f"Věk ({age_months} měs. > max {max_age} měs.)")
    elif age_months is None:
        badges.append("❓ Věk neupřesněn")

    # 3. Váha a velikost v dospělosti (hledáme jen v očištěném textu psa!)
    big_breeds = ["ovčák", "husky", "malamut", "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman", "středoasiat", "většího vzrůstu", "velkého vzrůstu"]
    if any(b in full for b in big_breeds):
        badges.append("⚠️ Zmíněno velké plemeno")
        if hide_big_breeds:
            reasons_rejected.append("Velké plemeno v popisu")

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
    elif "menší střední" in full or "středního vzrůstu" in full or "střední (31" in full or "malý (do 30" in full:
        badges.append("✅ Malý / Střední vzrůst")
        score += 1

    # 4. Srst
    if "dlouhosrst" in full or "hustý kožíšek" in full or "chlupat" in full:
        badges.append("⚠️ Delší srst")
        if only_short_hair:
            score -= 2
    elif "krátkosrst" in full or "hladkosrst" in full:
        badges.append("✅ Krátkosrsté")
        score += 1

    # 5. Pohlaví
    if gender_filter == "Jen fenky ♀️" and not any(w in full for w in ["fenka", "fenečka", "holčička", "fena", "samice"]):
        if any(w in full for w in ["pejsek", "kluk", "chlapeček", "samec"]):
            reasons_rejected.append("Pohlaví (pes)")
    elif gender_filter == "Jen psi ♂️" and not any(w in full for w in ["pejsek", "kluk", "chlapeček", "pes", "samec"]):
        if any(w in full for w in ["fenka", "fenečka", "holčička", "samice"]):
            reasons_rejected.append("Pohlaví (fena)")

    # 6. Vlastní klíčové slovo
    if custom_word.strip():
        if custom_word.strip().lower() not in full:
            reasons_rejected.append(f"Chybí slovo '{custom_word.strip()}'")
        else:
            badges.append(f"🔍 Obsahuje: '{custom_word.strip()}'")

    keep = (len(reasons_rejected) == 0)
    return keep, score, badges, ", ".join(reasons_rejected)

# --- ČTENÍ DETAILU JEDNOHO PEJSKA ---
def scrape_dog_page(link, base_domain, source_label, headers):
    # Vynecháme obecné podstránky, které nejsou profily psů
    bad_url_parts = ["postup-adopce", "podminky-adopce", "kontakt", "o-nas", "jak-pomoci", "darujte", "smlouva", "dotaznik"]
    if any(b in link.lower() for b in bad_url_parts):
        return None
    try:
        r = requests.get(link, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else "Pejsek k adopci"

        text_content = soup.get_text(" ", strip=True)
        img_url = None
        for img in soup.find_all("img", src=True):
            src = img["src"]
            low = src.lower()
            if any(k in low for k in ["upload", "files", "wp-content", "images/zvirata", "pes", "dog"]):
                if not any(bad in low for bad in ["logo", "icon", "banner", "avatar", "svg", "button"]):
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

# --- STAHOVÁNÍ INZERÁTŮ ---
@st.cache_data(ttl=900)
def fetch_dogs(pages=3, add_shelters=True):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    dogs = []
    seen_urls = set()

    # 1. PESWEB (ověřená funkční cesta přes hlavní stránky katalogu)
    pw_links = []
    for page in range(1, pages + 1):
        url = f"https://www.pesweb.cz/cz/psi-k-adopci?page={page}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if ("/psi-k-adopci/" in href or "objid=" in href) and not href.endswith("psi-k-adopci"):
                    full_url = urljoin("https://www.pesweb.cz", href)
                    if full_url not in seen_urls and "page=" not in full_url and "link-utulek" not in full_url:
                        seen_urls.add(full_url)
                        pw_links.append(full_url)
        except Exception:
            continue

    for link in pw_links[:pages * 18]:
        d = scrape_dog_page(link, "https://www.pesweb.cz", "Pesweb.cz", headers)
        if d:
            dogs.append(d)

    # 2. PŘÍMÉ WEBY ÚTULKŮ (AniDef, Voříškov)
    if add_shelters:
        # AniDef Žim (jasná struktura URL s číslem psa: /psi-k-adopci/1234-jmeno)
        try:
            r = requests.get("https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci", headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            anidef_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/psi-k-adopci/" in href and re.search(r'/\d+-', href):
                    full_u = urljoin("https://www.anidef.cz", href)
                    if full_u not in seen_urls:
                        seen_urls.add(full_u)
                        anidef_links.append(full_u)
            for link in anidef_links[:15]:
                d = scrape_dog_page(link, "https://www.anidef.cz", "Útulek AniDef", headers)
                if d:
                    dogs.append(d)
        except Exception:
            pass

    return dogs

# --- ZÁLOŽKY APLIKACE ---
tab1, tab2, tab3 = st.tabs([
    "🐶 Automatický skener inzerátů",
    "🔍 Rychlý rentgen libovolného textu",
    "🚀 Ověřené 'Byt-friendly' spolky & Šablona"
])

with tab1:
    st.subheader("Živé prohledávání útulků")
    st.write("Klikni na tlačítko níže. Appka projde inzeráty, ořízne z nich rušivé reklamy v patičce a vyfiltruje štěňata podle posuvníků vlevo.")
    
    if st.button("🔄 Načíst a vyfiltrovat aktuální inzeráty", type="primary"):
        with st.spinner("Procházím inzeráty a čtu popisky (trvá cca 15–20 vteřin)..."):
            raw_dogs = fetch_dogs(pages=pages_to_scan, add_shelters=include_shelters)
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
        st.success(f"Zkontrolováno **{len(raw_dogs)}** reálných psích profilů. Tvým filtrům vlevo vyhovuje: **{len(filtered)}**")

        if not filtered:
            st.warning("Žádný z právě načtených psů neprošel všemi filtry. Zkus vlevo zvýšit věk, posunout počet stránek na 5 nebo zaškrtni dole '🕵️ Ukázat i vyřazené psy', ať vidíš, proč vypadli!")

        for score, badges, d in filtered:
            with st.container(border=True):
                col1, col2 = st.columns([1, 3])
                with col1:
                    if d["img"]:
                        st.image(d["img"], use_container_width=True)
                    else:
                        st.info("📷 Fotka je v detailu inzerátu")
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
            st.subheader(f"🕵️️ Vyřazení psi ({len(rejected)}) – pro tvou kontrolu, že filtr funguje správně:")
            for reason, badges, d in rejected[:20]:
                st.markdown(f"* ❌ **{d['title']}** ([odkaz]({d['url']})) – *Důvod vyřazení: {reason}*")

with tab2:
    st.subheader("Rentgen inzerátu z Facebooku nebo jiného webu")
    user_text = st.text_area("Vlož zkopírovaný text inzerátu:", height=140)
    if st.button("Analyzovat vložený text"):
        if user_text.strip():
            keep, score, badges, reason = analyze_dog("Inzerát", user_text, "Ruční vložení")
            st.markdown(" &nbsp; ".join([f"`{b}`" for b in badges]))
            if keep and score >= 2:
                st.success("🎉 Skvělý kandidát! Text odpovídá tvým parametrům.")
            elif not keep:
                st.error(f"🛑 Pozor! Inzerát nevyhovuje filtrům v levém panelu ({reason}).")
            else:
                st.info("💡 Stojí za prověření – některé parametry nejsou v textu výslovně uvedené.")

with tab3:
    st.subheader("Kam koukat o 2 dny dřív než ostatní (Dočaskové spolky)")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        * **[Dočasky De De – Katalog psů](https://www.docaskydede.cz/k-adopci/psi-k-adopci/)** (+ [Facebook](https://www.facebook.com/DocaskyDeDe))
        * **[Tlapky na cestě (Celá ČR)](https://www.facebook.com/tlapkynaceste)** – Často krátkosrstí 10–15 kg
        * **[Srdcem pro psy (u Slaného)](https://www.facebook.com/srdcempropsy)** – Skvělý odhad dospělé váhy
        * **[Voříškov (u Prahy)](https://voriskov.cz/psi-k-adopci/)** – Moderní přístup, vítají byty
        """)
    with c2:
        st.markdown("""
        * **[Útulek AniDef (Žim - 55 min po D8)](https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci)**
        * **[Dejte nám šanci (Morava)](https://www.dejtenamsanci.cz/psi-k-adopci/)** – Domácí dočasky
        * **[Štěňata v nouzi (Celá ČR)](https://www.facebook.com/stenatavnouzi)** – Čistě vrhy štěňat
        * **[Pesweb – celý katalog](https://www.pesweb.cz/cz/psi-k-adopci)**
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
