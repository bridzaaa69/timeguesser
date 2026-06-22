from __future__ import annotations

import json
import random
import socket
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HOST = "0.0.0.0"
PORT = 65432
MAX_HRACOV = 2

OTAZKY_DIR = Path(__file__).parent / "otazky"
STRELNE_PATH = OTAZKY_DIR / "strelneotazky.json"
VYNECHANE_TEMY = {"SPŠE", "strelneotazky"}

BODY_ZA_SPRAVNU_R1 = 30
POCET_OTAZOK_R1 = 20

POCET_TEM_R2 = 10
VKLAD_R2_MIN = 20
VKLAD_R2_MAX = 100
POCET_KOL_R2 = 10

POCET_TEM_R3 = 10
VKLAD_R3_MIN = 20
VKLAD_R3_MAX = 350
POCET_KOL_R3 = 10


def normalizuj(text: str) -> str:
    text = text.strip().lower()
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def je_spravna(odpoved: str, prijatelne: list[str]) -> bool:
    if not prijatelne:
        return False
    norm = normalizuj(odpoved)
    return any(normalizuj(p) == norm for p in prijatelne if p)


@dataclass
class Otazka:
    otazka: str
    odpovede: list[str]
    tema: str

    @property
    def hlavna_odpoved(self) -> str:
        return self.odpovede[0] if self.odpovede else ""


def nacitaj_strelne() -> dict[str, list[Otazka]]:
    """Načítaj strelne otázky a rozdelí ich do kategórií (po 2-3 otázky v každej)."""
    if not STRELNE_PATH.exists():
        return {}
    raw = json.loads(STRELNE_PATH.read_text(encoding="utf-8"))
    if not raw:
        return {}
    
    otazky = [
        Otazka(p["otazka"], p.get("odpovede", [p.get("odpoved", "")]), "strelneotazky")
        for p in raw
    ]
    
    # Rozdelenie otázok do kategórií (Strelne 1, Strelne 2, ...)
    pocet_kategorii = max(2, len(otazky) // 3)  # ~3 otázky na kategóriu
    banka: dict[str, list[Otazka]] = {}
    
    for i, otazka in enumerate(otazky):
        kategorija = f"Strelne {(i % pocet_kategorii) + 1}"
        if kategorija not in banka:
            banka[kategorija] = []
        banka[kategorija].append(otazka)
    
    return banka


def nacitaj_otazky() -> dict[str, list[Otazka]]:
    banka: dict[str, list[Otazka]] = {}
    for path in sorted(OTAZKY_DIR.glob("*.json")):
        tema = path.stem
        if tema in VYNECHANE_TEMY:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not raw:
            continue
        otazky: list[Otazka] = []
        for polozka in raw:
            if isinstance(polozka, str):
                otazky.append(Otazka(polozka, [], tema))
            elif "odpovede" in polozka:
                otazky.append(Otazka(polozka["otazka"], polozka["odpovede"], tema))
            else:
                otazky.append(Otazka(polozka["otazka"], [polozka.get("odpoved", "")], tema))
        if otazky:
            banka[tema] = otazky
    return banka


def vyber_otazku(banka: dict[str, list[Otazka]], tema: str, pouzite: set[str]) -> Otazka | None:
    dostupne = [o for o in banka.get(tema, []) if o.otazka not in pouzite]
    if not dostupne:
        return None
    otazka = random.choice(dostupne)
    pouzite.add(otazka.otazka)
    return otazka


def posli(spojenie: socket.socket, sprava: dict[str, Any]) -> None:
    spojenie.sendall((json.dumps(sprava, ensure_ascii=False) + "\n").encode())


@dataclass
class Hrac:
    spojenie: socket.socket
    cislo: int
    body: int = 0
    buffer: str = ""


def prijmi_od_hraca(hrac: Hrac) -> dict[str, Any] | None:
    while "\n" not in hrac.buffer:
        data = hrac.spojenie.recv(4096)
        if not data:
            return None
        hrac.buffer += data.decode()
    riadok, hrac.buffer = hrac.buffer.split("\n", 1)
    return json.loads(riadok)


@dataclass
class DuelHra:
    hraci: list[Hrac]
    banka: dict[str, list[Otazka]]
    strelne: dict[str, list[Otazka]]
    temy_r1: list[str] = field(default_factory=list)
    temy_r2: list[str] = field(default_factory=list)
    temy_r3: list[str] = field(default_factory=list)
    pouzite_otazky: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        # Kategórie pre Kolo 1 (strelne)
        self.temy_r1 = list(self.strelne.keys())
        
        # Kategórie pre Kolo 2
        dostupne = list(self.banka.keys())
        self.temy_r2 = random.sample(dostupne, min(POCET_TEM_R2, len(dostupne)))
        
        # Kategórie pre Kolo 3
        zvysok = [t for t in dostupne if t not in self.temy_r2]
        pool = zvysok if len(zvysok) >= POCET_TEM_R3 else dostupne
        self.temy_r3 = random.sample(pool, min(POCET_TEM_R3, len(pool)))

    def odosli_vsetkym(self, sprava: dict[str, Any]) -> None:
        for hrac in self.hraci:
            posli(hrac.spojenie, sprava)

    def odosli_hracovi(self, hrac: Hrac, sprava: dict[str, Any]) -> None:
        posli(hrac.spojenie, sprava)

    def cakaj_odpoved(self, hrac: Hrac, ocakavany_typ: str) -> dict[str, Any] | None:
        while True:
            msg = prijmi_od_hraca(hrac)
            if msg is None:
                return None
            if msg.get("typ") == "quit":
                return msg
            if msg.get("typ") == ocakavany_typ:
                return msg

    def cakaj_vklad(self, hrac: Hrac, min_vklad: int, max_vklad: int) -> int | None:
        self.odosli_hracovi(hrac, {
            "typ": "vklad",
            "min": min_vklad,
            "max": max_vklad,
            "hrac": hrac.cislo,
        })
        msg = self.cakaj_odpoved(hrac, "vklad")
        if msg is None or msg.get("typ") == "quit":
            return None
        vklad = int(msg["hodnota"])
        return max(min_vklad, min(max_vklad, vklad))

    def cakaj_temu(self, hrac: Hrac, temy: list[str], kto_vybera: str) -> str | None:
        self.odosli_hracovi(hrac, {
            "typ": "vyber_temy",
            "temy": temy,
            "hrac": hrac.cislo,
            "kto_vybera": kto_vybera,
        })
        msg = self.cakaj_odpoved(hrac, "tema")
        if msg is None or msg.get("typ") == "quit":
            return None
        tema = msg["tema"]
        return tema if tema in temy else temy[0]

    def cakaj_vyber_otazky(self, hrac: Hrac, otazky: list[Otazka]) -> Otazka | None:
        self.odosli_hracovi(hrac, {
            "typ": "vyber_otazky",
            "hrac": hrac.cislo,
            "otazky": [{"id": i, "text": o.otazka} for i, o in enumerate(otazky)],
        })
        msg = self.cakaj_odpoved(hrac, "vyber_otazku")
        if msg is None or msg.get("typ") == "quit":
            return None
        idx = int(msg.get("id", 0))
        if 0 <= idx < len(otazky):
            return otazky[idx]
        return otazky[0]

    def pytaj_hraca(
        self, hrac: Hrac, otazka: Otazka, vklad: int, kolo: int, cislo_kola: int,
    ) -> bool:
        self.odosli_hracovi(hrac, {
            "typ": "otazka",
            "kolo": kolo,
            "cislo": cislo_kola,
            "tema": otazka.tema,
            "text": otazka.otazka,
            "vklad": vklad,
            "hrac": hrac.cislo,
        })
        for ostatny in self.hraci:
            if ostatny.cislo != hrac.cislo:
                self.odosli_hracovi(ostatny, {
                    "typ": "caka_sa",
                    "kolo": kolo,
                    "hrac": hrac.cislo,
                    "text": f"Hráč {hrac.cislo} odpovedá…",
                })

        msg = self.cakaj_odpoved(hrac, "odpoved")
        if msg is None or msg.get("typ") == "quit":
            return False

        spravne = je_spravna(msg.get("odpoved", ""), otazka.odpovede)
        if kolo == 1:
            if spravne:
                hrac.body += BODY_ZA_SPRAVNU_R1
        elif spravne:
            hrac.body += vklad
        else:
            hrac.body -= vklad

        self.odosli_vsetkym({
            "typ": "vysledok_otazky",
            "kolo": kolo,
            "cislo": cislo_kola,
            "hrac": hrac.cislo,
            "spravne": spravne,
            "spravna_odpoved": otazka.hlavna_odpoved,
            "vklad": vklad if kolo != 1 else BODY_ZA_SPRAVNU_R1,
            "body": hrac.body,
        })
        return True

    def kolo_1_strelne(self) -> bool:
        """Kolo 1: Automatické strelne otázky - server si vybiera, hráči len odpovedajú."""
        # Zbierame všetky strelne otázky do jedného zoznamu
        vsetky_strelne: list[Otazka] = []
        for otazky_list in self.strelne.values():
            vsetky_strelne.extend(otazky_list)
        
        dostupne = [o for o in vsetky_strelne if o.otazka not in self.pouzite_otazky]
        if len(dostupne) < POCET_OTAZOK_R1:
            self.odosli_vsetkym({
                "typ": "chyba",
                "sprava": f"Nedostatok strelných otázok (potrebných {POCET_OTAZOK_R1}).",
            })
            return False

        self.odosli_vsetkym({
            "typ": "zaciatok_kola",
            "kolo": 1,
            "popis": "Hráč 1 odpovie na 10 otázok, následne hráč 2 odpovie na 10 otázok",
            "pocet_otazok": POCET_OTAZOK_R1,
        })

        poradie = [self.hraci[0]] * 10 + [self.hraci[1]] * 10

        for k, vyberajuci in enumerate(poradie):
            dostupne = [o for o in vsetky_strelne if o.otazka not in self.pouzite_otazky]
            
            if not dostupne:
                break

            # Server si automaticky vyberie náhodnu otázku
            otazka = random.choice(dostupne)
            self.pouzite_otazky.add(otazka.otazka)

            self.odosli_vsetkym({
                "typ": "info_kola",
                "kolo": 1,
                "cislo": k + 1,
                "vyberal": vyberajuci.cislo,
                "pre_hraca": vyberajuci.cislo,
                "tema": "Strelne",
            })

            if not self.pytaj_hraca(vyberajuci, otazka, BODY_ZA_SPRAVNU_R1, kolo=1, cislo_kola=k + 1):
                return False

        self.odosli_vsetkym({
            "typ": "koniec_kola",
            "kolo": 1,
            "body": {h.cislo: h.body for h in self.hraci},
        })
        return True

    def kolo_2_vlastny_vyber(self) -> bool:
        volne_temy = self.temy_r2 * 2

        self.odosli_vsetkym({
            "typ": "zaciatok_kola",
            "kolo": 2,
            "popis": "Vlastný výber témy a vkladu (20–100 bodov) pre seba",
            "temy": volne_temy,
            "pocet_kol": POCET_KOL_R2,
            "vklad_min": VKLAD_R2_MIN,
            "vklad_max": VKLAD_R2_MAX,
        })

        for k in range(POCET_KOL_R2):
            hrac = self.hraci[k % 2]
            if not volne_temy:
                volne_temy = self.temy_r2 * 2

            tema = self.cakaj_temu(hrac, volne_temy, "vlastny")
            if tema is None:
                return False
            if tema in volne_temy:
                volne_temy.remove(tema)

            vklad = self.cakaj_vklad(hrac, VKLAD_R2_MIN, VKLAD_R2_MAX)
            if vklad is None:
                return False

            otazka = vyber_otazku(self.banka, tema, self.pouzite_otazky)
            if otazka is None:
                continue

            self.odosli_vsetkym({
                "typ": "info_kola",
                "kolo": 2,
                "cislo": k + 1,
                "vyberal": hrac.cislo,
                "pre_hraca": hrac.cislo,
                "tema": tema,
                "vklad": vklad,
            })

            if not self.pytaj_hraca(hrac, otazka, vklad, kolo=2, cislo_kola=k + 1):
                return False

        self.odosli_vsetkym({
            "typ": "koniec_kola",
            "kolo": 2,
            "body": {h.cislo: h.body for h in self.hraci},
        })
        return True

    def kolo_3_vyber_supera(self) -> bool:
        volne_temy = list(self.temy_r3)

        self.odosli_vsetkym({
            "typ": "zaciatok_kola",
            "kolo": 3,
            "popis": "Výber témy a vkladu (20–350 bodov) pre súpera",
            "temy": volne_temy,
            "pocet_kol": POCET_KOL_R3,
            "vklad_min": VKLAD_R3_MIN,
            "vklad_max": VKLAD_R3_MAX,
        })

        for k in range(POCET_KOL_R3):
            vyberajuci = self.hraci[k % 2]
            super = self.hraci[1 - (k % 2)]
            if not volne_temy:
                break

            tema = self.cakaj_temu(vyberajuci, volne_temy, "super")
            if tema is None:
                return False
            if tema in volne_temy:
                volne_temy.remove(tema)

            vklad = self.cakaj_vklad(super, VKLAD_R3_MIN, VKLAD_R3_MAX)
            if vklad is None:
                return False

            otazka = vyber_otazku(self.banka, tema, self.pouzite_otazky)
            if otazka is None:
                continue

            self.odosli_vsetkym({
                "typ": "info_kola",
                "kolo": 3,
                "cislo": k + 1,
                "vyberal": vyberajuci.cislo,
                "pre_hraca": super.cislo,
                "tema": tema,
                "vklad": vklad,
            })

            if not self.pytaj_hraca(super, otazka, vklad, kolo=3, cislo_kola=k + 1):
                return False

        self.odosli_vsetkym({
            "typ": "koniec_kola",
            "kolo": 3,
            "body": {h.cislo: h.body for h in self.hraci},
        })
        return True

    def ukoncenie(self) -> None:
        body = {h.cislo: h.body for h in self.hraci}
        if self.hraci[0].body > self.hraci[1].body:
            vitaz = 1
        elif self.hraci[1].body > self.hraci[0].body:
            vitaz = 2
        else:
            vitaz = 0

        self.odosli_vsetkym({
            "typ": "koniec_hry",
            "body": body,
            "vitaz": vitaz,
            "sprava": f"Víťaz: Hráč {vitaz}!" if vitaz else "Remíza!",
        })

    def spusti(self) -> None:
        self.odosli_vsetkym({
            "typ": "start",
            "pravidla": {
                "kolo1": f"{POCET_OTAZOK_R1} otázok, výber pre seba, {BODY_ZA_SPRAVNU_R1} bodov/odpoveď",
                "kolo2": f"Vklad {VKLAD_R2_MIN}–{VKLAD_R2_MAX}, {POCET_KOL_R2} otázok pre seba",
                "kolo3": f"Vklad {VKLAD_R3_MIN}–{VKLAD_R3_MAX}, {POCET_KOL_R3} otázok pre súpera",
            },
            "temy_kola2": self.temy_r2,
            "temy_kola3": self.temy_r3,
        })

        if not self.kolo_1_strelne():
            return
        if not self.kolo_2_vlastny_vyber():
            return
        if not self.kolo_3_vyber_supera():
            return
        self.ukoncenie()


def herna_slucka(spojenia: list[socket.socket], adresy: list) -> None:
    print(f"[SERVER] Obaja hráči pripojení: {adresy[0]} a {adresy[1]}")

    banka = nacitaj_otazky()
    strelne = nacitaj_strelne()
    if not banka:
        for s in spojenia:
            posli(s, {"typ": "chyba", "sprava": "Nenačítané žiadne otázky."})
        return
    if not strelne:
        for s in spojenia:
            posli(s, {"typ": "chyba", "sprava": "Chýba súbor otazky/strelneotazky.json."})
        return

    hraci = [Hrac(spojenia[i], i + 1) for i in range(2)]

    for hrac in hraci:
        posli(hrac.spojenie, {
            "typ": "vitaj",
            "hrac": hrac.cislo,
            "sutaziaci": "Hráči stoja oproti sebe – pripravte sa na DUEL!",
        })

    hra = DuelHra(hraci=hraci, banka=banka, strelne=strelne)
    hra.spusti()

    for s in spojenia:
        try:
            s.close()
        except OSError:
            pass
    print("[SERVER] Hra skončila, spojenia zatvorené.")


def main() -> None:
    # Zisti lokálnu IP adresu
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lokalna_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lokalna_ip = "127.0.0.1"
    
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(MAX_HRACOV)
    print(f"[SERVER] DUEL – počúvam na {HOST}:{PORT}, čakám na {MAX_HRACOV} hráčov...")
    print(f"[SERVER] Klienti sa môžu pripojiť na: {lokalna_ip}:{PORT}")

    spojenia: list[socket.socket] = []
    adresy: list = []

    while len(spojenia) < MAX_HRACOV:
        spojenie, adresa = server_socket.accept()
        spojenia.append(spojenie)
        adresy.append(adresa)
        print(f"[SERVER] Pripojený hráč {len(spojenia)}: {adresa}")
        posli(spojenie, {
            "typ": "cakaj",
            "pripojeni": len(spojenia),
            "max": MAX_HRACOV,
        })

    vlakno = threading.Thread(target=herna_slucka, args=(spojenia, adresy), daemon=True)
    vlakno.start()
    vlakno.join()

    server_socket.close()
    print("[SERVER] Server ukončený.")


if __name__ == "__main__":
    main()
