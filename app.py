import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

st.set_page_config(page_title="Hledač štěněte do bytu 🐾", page_icon="🐾", layout="wide")

st.title("🐾 Chytrý hledač štěněte (Praha -> Celá ČR)")
st.markdown("Automatické prohledávání útulků a čtení inzerátů podle tvých parametrů.")

# --- POSTRANNÍ PANEL S FILTRY PRO KAMARÁDKU ---
st.sidebar.header("⚙️ Nastavení filtrů")
st.sidebar.caption("Kdykoliv cokoliv změň – seznam se hned sám přepočítá!")

max_age = st.sidebar.slider("Maximální věk (měsíce)", min_value=1, max_value=12, value=6)
min_w, max_w = st.sidebar.slider("Cílová váha v dospělosti (kg)", 3, 30, (10, 15))

gender_filter = st.sidebar.radio("Pohlaví", ["Všechna", "Jen fenky ♀️", "Jen psi ♂️"])
only_short_hair = st.sidebar.checkbox("Upřednostnit krátkosrsté / hladkosrsté", value=True)
hide_garden_only = st.sidebar.checkbox("Přísně vyřadit 'pouze na zahradu'", value=True)
hide_big_breeds = st.sidebar.checkbox("Vyřadit křížence velkých plemen (ovčák, husky...)", value=True)

custom_word = st.sidebar.text_input("Hledané slovo v textu (např. kočky, děti):", "")
pages_to_scan = st.sidebar.slider("Kolik stránek katalogu prohledat", 1, 5, 2)

# --- FUNKCE PRO ANALÝZU TEXTU INZERÁTU ---
def analyze_dog(title, text):
    full = (title + " " + text).lower()
    badges = []
    score = 0
    keep = True

    # 1. Byt vs. Zahrada
    garden_red_flags = ["pouze na zahradu", "jen k domku", "nevhodný do bytu", "ne do bytu", "výhradně k domu", "pouze k domu"]
    flat_green_flags = ["do bytu", "v bytě", "dočasné péči", "hygienické návyky", "na podložku", "čistotn"]

    if any(p in full for p in garden_red_flags):
        badges.append("❌ Podmínka: Pouze zahrada")
        score -= 5
        if hide_garden_only:
            keep = False
    elif any(p in full for p in flat_green_flags):
        badges.append("✅ Vhodné do bytu / Dočaska")
        score += 3
    else:
        badges.append("⚠️ Byt v textu nezmíněn")

    # 2. Věk (hledání měsíců / týdnů)
    age_months = None
    m_match = re.search(r'\b(\d{1,2})\s*(měsíc|měsíční|měs\.)', full)
    w_match = re.search(r'\b(\d{1,2})\s*(týdn|týden)', full)
    y_match = re.search(r'\b(\d{1,2})\s*(rok|roky|let|roční)\b', full)

    if w_match:
        age_months = max(1, int(w_match.group(1)) // 4)
        badges.append(f"🍼 Věk: ~{w_match.group(1)} týdnů")
    elif m_match:
        age_months = int(m_match.group(1))
        badges.append(f"🍼 Věk: {age_months} měs.")
    elif "štěně" in full or "štěňátko" in full or "miminko" in full:
        age_months = 4
        badges.append("🍼 Věk: Štěně")
    elif y_match and "měsíc" not in full:
        age_months = int(y_match.group(1)) * 12

    if age_months is not None and age_months > max_age:
        keep = False

    # 3. Váha a velikost v dospělosti
    big_breeds = ["ovčák", "husky", "malamut", "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman", "většího vzrůstu", "velkého vzrůstu"]
    if any(b in full for b in big_breeds):
        badges.append("⚠️ Riziko velkého plemene")
        if hide_big_breeds:
            keep = False

    weights = [int(w) for w in re.findall(r'\b(\d{1,2})\s*kg', full)]
    if weights:
        est_adult = max(weights)
        if min_w <= est_adult <= max_w:
            badges.append(f"✅ Váha v textu: {est_adult} kg")
            score += 3
        elif est_adult > max_w + 3:
            badges.append(f"❌ Zmíněna vyšší váha: {est_adult} kg")
            if hide_big_breeds:
                keep = False
        else:
            badges.append(f"⚖️ Zmíněná váha: {est_adult} kg")
    elif "menší střední" in full or "středního vzrůstu" in full or "střední velikosti" in full:
        badges.append("✅ Střední / menší střední vzrůst")
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
    if gender_filter == "Jen fenky ♀️" and not any(w in full for w in ["fenka", "fenečka", "holčička", "fena", "dáma"]):
        if any(w in full for w in ["pejsek", "kluk", "chlapeček"]):
            keep = False
    elif gender_filter == "Jen psi ♂️" and not any(w in full for w in ["pejsek", "kluk", "chlapeček", "pes"]):
        if any(w in full for w in ["fenka", "fenečka", "holčička"]):
            keep = False

    # 6. Vlastní klíčové slovo
    if custom_word.strip():
        if custom_word.strip().lower() not in full:
            keep = False
        else:
            badges.append(f"🔍 Obsahuje: '{custom_word.strip()}'")

    return keep, score, badges

# --- FUNKCE PRO STAHOVÁNÍ Z PESWEBU ---
@st.cache_data(ttl=1800)
def fetch_pesweb(pages=2):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    dogs = []
    seen_urls = set()

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
        except Exception:
            continue

    # Projdeme detaily nalezených inzerátů (max 35 na jedno načtení, ať je to rychlé)
    for link in list(seen_urls)[:35]:
        try:
            r = requests.get(link, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            title_el = soup.find("h1")
            title = title_el.get_text(strip=True) if title_el else "Pejsek k adopci"
            if "našel domov" in title.lower():
                continue

            text_content = soup.get_text(" ", strip=True)
            img_url = None
            for img in soup.find_all("img", src=True):
                src = img["src"]
                if "upload" in src or "files" in src or "pes" in src.lower():
                    if "logo" not in src.lower() and "icon" not in src.lower():
                        img_url = urljoin("https://www.pesweb.cz", src)
                        break

            dogs.append({
                "title": title,
                "url": link,
                "text": text_content,
                "img": img_url
            })
        except Exception:
            continue
    return dogs

# --- ZÁLOŽKY APLIKACE ---
tab1, tab2, tab3 = st.tabs([
    "🐶 Automatický skener inzerátů",
    "🔍 Rychlý rentgen libovolného textu",
    "🚀 Ověřené 'Byt-friendly' spolky & Šablona"
])

with tab1:
    st.subheader("Živé prohledávání katalogu útulků")
    st.write("Klikni na tlačítko níže. Appka projde aktuální inzeráty, přečte celé jejich popisy a vyfiltruje jen ty, které odpovídají nastaveným posuvníkům vlevo.")
    
    if st.button("🔄 Načíst a vyfiltrovat aktuální inzeráty", type="primary"):
        with st.spinner("Procházím útulky a čtu popisky inzerátů (trvá to cca 10–15 vteřin)..."):
            raw_dogs = fetch_pesweb(pages=pages_to_scan)
            st.session_state["raw_dogs"] = raw_dogs

    if "raw_dogs" in st.session_state:
        raw_dogs = st.session_state["raw_dogs"]
        filtered = []
        for d in raw_dogs:
            keep, score, badges = analyze_dog(d["title"], d["text"])
            if keep:
                filtered.append((score, badges, d))
        
        filtered.sort(key=lambda x: x[0], reverse=True)
        st.success(f"Zkontrolováno {len(raw_dogs)} detailních inzerátů. Podmínkám v posuvnících vyhovuje: **{len(filtered)}**")

        if not filtered:
            st.warning("Žádný z právě načtených inzerátů neprošel přísným sítem. Zkus v levém panelu zvýšit počet prohledávaných stránek nebo posunout věk/váhu!")

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
                    snippet = d["text"]
                    if "Popis" in snippet:
                        snippet = snippet.split("Popis", 1)[-1]
                    st.write(snippet[:420] + "...")
                    st.link_button("👉 Otevřít celý inzerát a kontakt", d["url"])

with tab2:
    st.subheader("Rentgen inzerátu z Facebooku nebo jiného webu")
    st.write("Našla jsi štěně na Facebooku? Vlož sem text příspěvku a hned uvidíš, jestli v něm není skrytá podmínka zahrady nebo velkého vzrůstu.")
    user_text = st.text_area("Vlož text inzerátu:", height=140, placeholder="Např.: Hledáme domov pro 4měsíční fenečku, v dospělosti cca 12 kg...")
    if st.button("Analyzovat vložený text"):
        if user_text.strip():
            keep, score, badges = analyze_dog("Inzerát", user_text)
            st.markdown(" &nbsp; ".join([f"`{b}`" for b in badges]))
            if keep and score >= 2:
                st.success("🎉 Skvělý kandidát! Text odpovídá tvým parametrům.")
            elif not keep:
                st.error("🛑 Pozor! Inzerát nevyhovuje tvým nastaveným filtrům v levém panelu (zkontroluj štítky výše).")
            else:
                st.info("💡 Stojí za prověření – některé parametry nejsou v textu výslovně uvedené, doptej se podle taháku.")

with tab3:
    st.subheader("Kam koukat o 2 dny dřív než ostatní (Dočaskové spolky)")
    st.write("Tyto spolky fungují přes **dočasné péče v bytech** a nemají předsudky vůči adopci do pražského bytu:")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        * **[Dočasky De De (Praha)](https://www.facebook.com/DocaskyDeDe)** – Top na štěňata v bytech
        * **[Tlapky na cestě (Celá ČR)](https://www.facebook.com/tlapkynaceste)** – Často krátkosrstí 10–15 kg
        * **[Srdcem pro psy (u Slaného)](https://www.facebook.com/srdcempropsy)** – Skvělý odhad dospělé váhy
        * **[Voříškov (u Prahy)](https://voriskov.cz/psi-k-adopci/)** – Moderní přístup, vítají byty
        """)
    with c2:
        st.markdown("""
        * **[Útulek AniDef (Žim - 55 min po D8)](https://www.anidef.cz/psi-k-adopci/aktualne-k-adopci)** – Menší přetlak zájemců
        * **[Dejte nám šanci (Morava)](https://www.dejtenamsanci.cz/psi-k-adopci/)** – Domácí dočasky
        * **[Štěňata v nouzi (Celá ČR)](https://www.facebook.com/stenatavnouzi)** – Čistě vrhy štěňat
        * **[Pesweb – přímý katalog](https://www.pesweb.cz/cz/psi-k-adopci)**
        """)
    
    st.divider()
    st.subheader("✉️ Šablona první zprávy (zkopíruj jedním kliknutím vpravo v rohu pole)")
    st.code("""Dobrý den, moc mě zaujal/a [JMÉNO ŠTĚNĚTE] a ráda bych se zeptala, zda ještě hledá domov. 

Něco málo o mně a podmínkách:
• Bydlím v Praze v bytě (hned u parku/zeleně pro každodenní procházky).
• Se štěnětem počítám jako s plnohodnotným členem domácnosti – přes den [DOPLŇ: pracuji z domova / mám vyřešené hlídání / bude samo jen X hodin po postupném zvykání].
• Hledám parťáka v dospělosti kolem 10–15 kg na výlety i městský život, počítám s pozitivní výchovou, socializací i podmínkou kastrace v dospělosti.
• Jsem pojízdná a velmi ráda za [JMÉNO ŠTĚNĚTE] přijedu na seznamovací návštěvu kamkoliv po ČR, jak vám to bude časově vyhovovat.

Můj telefon je [TVŮJ TELEFON]. Moc děkuji za zprávu!""", language="text")
