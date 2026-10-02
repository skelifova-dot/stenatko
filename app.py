import streamlit as st
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

st.set_page_config(page_title="Hledač štěněte do bytu 🐾", page_icon="🐾", layout="wide")

st.title("🐾 Multi-útulkový hledač štěněte (Praha -> Celá ČR)")
st.markdown("Automaticky prochází **Pesweb** i **samostatné weby útulků a dočasek**, čte texty inzerátů a filtruje štěňata vhodná do bytu.")

# --- POSTRANNÍ PANEL S FILTRY PRO KAMARÁDKU ---
st.sidebar.header("⚙️ 1. Nastavení parametrů")
st.sidebar.caption("Kdykoliv cokoliv změň – seznam se hned sám přepočítá!")

max_age = st.sidebar.slider("Maximální věk (měsíce)", min_value=2, max_value=12, value=6)
min_w, max_w = st.sidebar.slider("Cílová váha v dospělosti (kg)", 3, 30, (10, 15))

gender_filter = st.sidebar.radio("Pohlaví", ["Všechna", "Jen fenky ♀️", "Jen psi ♂️"])
only_short_hair = st.sidebar.checkbox("Upřednostnit krátkosrsté / hladkosrsté", value=True)
hide_garden_only = st.sidebar.checkbox("Přísně vyřadit 'pouze na zahradu'", value=True)
hide_big_breeds = st.sidebar.checkbox("Vyřadit křížence velkých plemen (ovčák, husky...)", value=True)
custom_word = st.sidebar.text_input("Hledané slovo v textu (např. kočky, děti):", "")

st.sidebar.divider()
st.sidebar.header("🌐 2. Které útulky prohledat?")
src_pesweb = st.sidebar.checkbox("Pesweb.cz (všechny stránky 2–6 měs.)", value=True)
src_pesweb_older = st.sidebar.checkbox("Pesweb.cz (přidat i 6 měs.–2 roky)", value=False)
src_dede = st.sidebar.checkbox("Dočasky De De (Praha - 100% do bytu)", value=True)
src_anidef = st.sidebar.checkbox("Útulek AniDef (Žim - Severní Čechy)", value=True)
src_tlapky = st.sidebar.checkbox("Tlapky na cestě (Dočasky ČR)", value=True)
src_dogpoint = st.sidebar.checkbox("Dogpoint & Voříškov (Okolí Prahy)", value=True)

max_dogs_per_source = st.sidebar.slider("Max. detailů na zdroj (rychlost vs. hloubka)", 15, 70, 35)

# --- FUNKCE PRO ANALÝZU TEXTU INZERÁTU ---
def analyze_dog(title, text, source_name):
    full = (title + " " + text).lower()
    badges = [f"🏠 {source_name}"]
    score = 0
    keep = True

    # 0. Vyřadit již adoptované psy
    if any(x in full for x in ["našel domov", "v adopci", "domov již nehledá", "adoptován", "rezervován"]):
        if "stav: v adopci" in full or "už domov nehledá" in full or "našel domov" in title.lower():
            return False, -99, []

    # 1. Byt vs. Zahrada
    garden_red_flags = ["pouze na zahradu", "jen k domku", "nevhodný do bytu", "ne do bytu", "výhradně k domu", "pouze k domu", "hlídat zahrádku"]
    flat_green_flags = ["do bytu", "v bytě", "dočasné péči", "hygienické návyky", "na podložku", "čistotn", "bydlení uvnitř"]

    if any(p in full for p in garden_red_flags):
        badges.append("❌ Podmínka: Pouze zahrada")
        score -= 5
        if hide_garden_only:
            keep = False
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
    elif "2–6 měsíců" in full or "2-6 měsíců" in full or "štěně" in full or "štěňátko" in full or "miminko" in full:
        if y_match and int(y_match.group(1)) >= 2:
            age_months = int(y_match.group(1)) * 12
        else:
            age_months = 4
            badges.append("🍼 Věk: Štěně")
            score += 2
    elif y_match:
        age_months = int(y_match.group(1)) * 12
        badges.append(f"📅 Věk: {y_match.group(1)} r.")

    # Pokud je z přímého webu útulku (kde jsou i dospělí psi) a nepoznali jsme, že je to štěně/mladý pes:
    if "Pesweb (2–6" not in source_name:
        if age_months is None or age_months > max_age:
            keep = False
    else:
        if age_months is not None and age_months > max_age:
            keep = False

    # 3. Váha a velikost v dospělosti
    big_breeds = ["ovčák", "husky", "malamut", "ohař", "labrador", "retrívr", "ridgeback", "rotvajler", "dobrman", "středoasiat", "čuvač", "molos", "většího vzrůstu", "velkého vzrůstu", "velký (61"]
    if any(b in full for b in big_breeds):
        badges.append("⚠️ Riziko velkého vzrůstu")
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
    elif "střední (31" in full or "malý (do 30" in full or "menší střední" in full or "středního vzrůstu" in full:
        badges.append("✅ Malý / Střední vzrůst")
        score += 1

    # 4. Srst
    if "dlouhosrst" in full or "hustý kožíšek" in full or "chlupat" in full or "delší srst" in full:
        badges.append("⚠️ Delší srst")
        if only_short_hair:
            score -= 2
    elif "krátkosrst" in full or "hladkosrst" in full:
        badges.append("✅ Krátkosrsté")
        score += 1

    # 5. Pohlaví
    if gender_filter == "Jen fenky ♀️":
        if not any(w in full for w in ["fenka", "fenečka", "holčička", "fena", "samice"]):
            keep = False
    elif gender_filter == "Jen psi ♂️":
        if any(w in full for w in ["fenka", "fenečka", "holčička", "pohlaví fena", "pohlaví: samice"]):
            keep = False

    # 6. Vlastní klíčové slovo
    if custom_word.strip():
        if custom_word.strip().lower() not in full:
            keep = False
        else:
            badges.append(f"🔍 Obsahuje: '{custom_word.strip()}'")

    return keep, score, badges

# --- UNIVERZÁLNÍ ČTEČKA DETAILU INZERÁTU ---
def scrape_detail_page(link, base_domain, source_label, headers):
    try:
        r = requests.get(link, headers=headers, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        title_el = soup.find("h1") or soup.find("h2")
        title = title_el.get_text(strip=True) if title_el else "Pejsek k adopci"

        text_content = soup.get_text(" ", strip=True)
        img_url = None
        for img in soup.find_all("img", src=True):
            src = img["src"]
            low = src.lower()
            if any(k in low for k in ["upload", "files", "wp-content", "images/zvirata", "pes", "dog", "fena"]):
                if not any(bad in low for bad in ["logo", "icon", "banner", "avatar", "sponsor", "svg"]):
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

# --- HLAVNÍ SBĚRAČ ZE VŠECH ZDROJŮ ---
@st.cache_data(ttl=900)
def fetch_all_sources(use_pw, use_pw_old, use_dede, use_anidef, use_tlapky, use_dp, limit_per_src):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    all_dogs = []
    seen_urls = set()

    # 1. PESWEB (2-6 měsíců - až 5 stránek)
    if use_pw:
        pw_links = []
        for p in range(1, 6):
            url = f"https://www.pesweb.cz/cz/psi-k-adopci?list_type=&vek=2m-6m&page={p}"
            try:
                r = requests.get(url, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if ("/psi-k-adopci/" in href or "objid=" in href) and not href.endswith("psi-k-adopci"):
                        if "list_type=" not in href and "page=" not in href and "link-utulek" not in href:
                            full_u = urljoin("https://www.pesweb.cz", href)
                            if full_u not in seen_urls:
                                seen_urls.add(full_u)
                                pw_links.append(full_u)
            except Exception:
                continue
        for link in pw_links[:limit_per_src]:
            d = scrape_detail_page(link, "https://www.pesweb.cz", "Pesweb (2–6 měs.)", headers)
            if d:
                all_dogs.append(d)

    # 2. PESWEB (6m - 2 roky, pokud tam útulek заřadil 5-6měsíční štěně)
    if use_pw_old:
        pw_old_links = []
        for p in range(1, 3):
            url = f"https://www.pesweb.cz/cz/psi-k-adopci?list_type=&vek=6m-2r&page={p}"
            try:
                r = requests.get(url, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if ("/psi-k-adopci/" in href or "objid=" in href) and not href.endswith("psi-k-adopci"):
                        if "list_type=" not in href and "page=" not in href and "link-utulek" not in href:
                            full_u = urljoin("https://www.pesweb.cz", href)
                            if full_u not in seen_urls:
                                seen_urls.add(full_u)
                                pw_old_links.append(full_u)
            except Exception:
                continue
        for link in pw_old_links[:limit_per_src // 2]:
            d = scrape_detail_page(link, "https://www.pesweb.cz", "Pesweb (Mladí)", headers)
            if d:
                all_dogs.append(d)

    # 3. DOČASKY DE DE
    if use_dede:
        dede_links = []
        for url in ["https://www.docaskydede.cz/k-adopci/psi-k-adopci/", "https://www.docaskydede.cz/psi-k-adopci/"]:
            try:
                r = requests.get(url, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "docaskydede.cz" in href and any(x in href for x in ["/pes/", "/psi-k-adopci/", "/k-adopci/"]):
                        if href not in [url, "https://www.docaskydede.cz/k-adopci/podminky-adopce/"] and href not in seen_urls:
                            seen_urls.add(href)
                            dede_links.append(href)
            except Exception:
                continue
        for link in dede_links[:15]:
            d = scrape_detail_page(link, "https://www.docaskydede.cz", "Dočasky De De", headers)
            if d:
                all_dogs.append(d)

    # 4. ÚTULEK ANIDEF (Žim)
    if use_anidef:
        anidef_links = []
        try:
            url = "https://www.anidef.cz/nase-zvirata/k-adopci/psi-k-adopci"
            r = requests.get(url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/psi-k-adopci/" in href and re.search(r'/\d+-', href):
                    full_u = urljoin("https://www.anidef.cz", href)
                    if full_u not in seen_urls:
                        seen_urls.add(full_u)
                        anidef_links.append(full_u)
        except Exception:
            pass
        for link in anidef_links[:20]:
            d = scrape_detail_page(link, "https://www.anidef.cz", "Útulek AniDef", headers)
            if d:
                all_dogs.append(d)

    # 5. TLAPKY NA CESTĚ
    if use_tlapky:
        tlapky_links = []
        try:
            url = "https://www.tlapkynaceste.cz/moznosti-adopce/pejsci-k-adopci/"
            r = requests.get(url, headers=headers, timeout=8)
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "tlapkynaceste.cz" in href and "/pejsci-k-adopci/" not in href and "/adopce/" not in href:
                    if len(href.strip("/").split("/")) >= 4 and href not in seen_urls:
                        seen_urls.add(href)
                        tlapky_links.append(href)
        except Exception:
            pass
        for link in tlapky_links[:15]:
            d = scrape_detail_page(link, "https://www.tlapkynaceste.cz", "Tlapky na cestě", headers)
            if d:
                all_dogs.append(d)

    # 6. DOGPOINT & VOŘÍŠKOV
    if use_dp:
        dp_links = []
        for base_u, label in [("https://www.dog-point.cz/psi-k-adopci", "Dogpoint"), ("https://voriskov.cz/psi-k-adopci/", "Voříškov")]:
            try:
                r = requests.get(base_u, headers=headers, timeout=8)
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = urljoin(base_u, a["href"])
                    if ("dog-point.cz/psi-k-adopci/" in href or "voriskov.cz/psi-k-adopci/" in href) and href != base_u:
                        if href not in seen_urls:
                            seen_urls.add(href)
                            dp_links.append((href, base_u, label))
            except Exception:
                continue
        for link, base_u, label in dp_links[:15]:
            d = scrape_detail_page(link, base_u, label, headers)
            if d:
                all_dogs.append(d)

    return all_dogs

# --- ZÁLOŽKY APLIKACE ---
tab1, tab2, tab3 = st.tabs([
    "🐶 Multi-útulkový skener štěňat",
    "🔍 Rychlý rentgen libovolného textu",
    "🚀 Další 'Byt-friendly' spolky & Šablona"
])

with tab1:
    st.subheader("Živé prohledávání útulků a dočaskových spolků")
    st.write("Appka projde všechny zaškrtnuté útulky v levém panelu, přečte celé texty inzerátů a vybere štěňata vhodná do bytu.")
    
    if st.button("🔄 Spustit velký sken vybraných útulků", type="primary"):
        with st.spinner("Procházím Pesweb, Dočasky De De, AniDef, Tlapky na cestě a další (trvá cca 15–25 vteřin)..."):
            raw_dogs = fetch_all_sources(
                src_pesweb, src_pesweb_older, src_dede, src_anidef, src_tlapky, src_dogpoint, max_dogs_per_source
            )
            st.session_state["raw_dogs"] = raw_dogs

    if "raw_dogs" in st.session_state:
        raw_dogs = st.session_state["raw_dogs"]
        filtered = []
        for d in raw_dogs:
            keep, score, badges = analyze_dog(d["title"], d["text"], d.get("source", "Útulek"))
            if keep:
                filtered.append((score, badges, d))
        
        filtered.sort(key=lambda x: x[0], reverse=True)
        st.success(f"Celkem přečteno **{len(raw_dogs)}** profilů napříč útulky. Tvým nastaveným filtrům vlevo vyhovuje: **{len(filtered)}**")

        if not filtered:
            st.warning("Žádné štěně teď neprošlo všemi filtry. Zkus v levém panelu odškrtnout 'Vyřadit křížence velkých plemen', zvýšit věk na 8 měsíců nebo zapnout 'Pesweb (6 měs.–2 roky)'!")

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
                    snippet = d["text"]
                    if "Popis" in snippet:
                        snippet = snippet.split("Popis", 1)[-1]
                    st.write(snippet[:420] + "...")
                    st.link_button("👉 Otevřít celý inzerát a kontakt", d["url"])

with tab2:
    st.subheader("Rentgen inzerátu z Facebooku nebo jiného webu")
    user_text = st.text_area("Vlož zkopírovaný text inzerátu:", height=140)
    if st.button("Analyzovat vložený text"):
        if user_text.strip():
            keep, score, badges = analyze_dog("Inzerát", user_text, "Ruční vložení")
            st.markdown(" &nbsp; ".join([f"`{b}`" for b in badges]))
            if keep and score >= 2:
                st.success("🎉 Skvělý kandidát! Text odpovídá tvým parametrům.")
            elif not keep:
                st.error("🛑 Pozor! Inzerát nevyhovuje nastaveným filtrům v levém panelu.")
            else:
                st.info("💡 Stojí za prověření – některé parametry nejsou v textu výslovně uvedené.")

with tab3:
    st.subheader("Kam koukat na Facebooku (kde se štěňata objeví o 2 dny dřív)")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        * **[Dočasky De De (Praha)](https://www.facebook.com/DocaskyDeDe)** – Top na štěňata v bytech
        * **[Tlapky na cestě (Celá ČR)](https://www.facebook.com/tlapkynaceste)** – Často krátkosrstí 10–15 kg
        * **[Srdcem pro psy (u Slaného)](https://www.facebook.com/srdcempropsy)** – Skvělý odhad dospělé váhy
        * **[Psí štěstí (Dočasky ČR)](https://www.facebook.com/psistesti)** – Menší a střední pejsci
        """)
    with c2:
        st.markdown("""
        * **[Útulek AniDef (Žim - 55 min po D8)](https://www.facebook.com/utulekanidef)** – Menší přetlak zájemců
        * **[Dejte nám šanci (Morava)](https://www.dejtenamsanci.cz/psi-k-adopci/)** – Domácí dočasky
        * **[Štěňata v nouzi (Celá ČR)](https://www.facebook.com/stenatavnouzi)** – Čistě vrhy štěňat
        * **[Běhejme a pomáhejme útulkům](https://www.behproutulky.cz/psi-k-adopci)**
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
