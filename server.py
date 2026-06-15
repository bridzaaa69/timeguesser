"""
TCP Server – vedomostná relácia DUEL pre dvoch hráčov
======================================================
Formát relácie (3 kolá, spolu 50 otázok):

  Kolo 1 – Rýchlovka:
    10 otázok za 60 sekúnd, 30 bodov za správnu odpoveď.
    Ak sú všetky správne, bonus 100 bodov (ekvivalent hotovosti v relácii).

  Kolo 2 – Vlastný výber témy a vkladu:
    10 tematických okruhov, vklad 20–100 bodov.
    Hráči sa striedajú vo výbere témy a vkladu.
    Každá otázka sa pýta obom súťažiacim (2×).

  Kolo 3 – Výber súpera:
    Súper vyberá tému a vklad (20–350 bodov).
    Otázky si súťažiaci vyberajú navzájom, každá otázka sa pýta obom (2×).

Komunikácia: JSON riadky ukončené \\n
  Server → klient: {"typ": "...", ...}
  Klient → server: {"typ": "...", ...}
"""

from __future__ import annotations

import json
import random
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from odpovede_utils import je_spravna

HOST = "0.0.0.0"
PORT = 65432
MAX_HRACOV = 2

OTAZKY_DIR = Path(__file__).parent / "otazky"
VYNECHANE_TEMY = {"SPŠE"}

# Herné konštanty
BODY_ZA_SPRAVNU_R1 = 30
BONUS_VSETKY_SPRAVNE_R1 = 100
CAS_R1_SEKUND = 60
POCET_OTAZOK_R1 = 10

POCET_TEM_R2 = 10
VKLAD_R2_MIN = 20
VKLAD_R2_MAX = 100
POCET_KOL_R2 = 20  # každá otázka sa pýta obom (2×)

VKLAD_R3_MIN = 20
VKLAD_R3_MAX = 350
POCET_KOL_R3 = 20

CELKOM_OTAZOK = POCET_OTAZOK_R1 + POCET_KOL_R2 + POCET_KOL_R3  # 50 otázok


@dataclass
class Otazka:
    otazka: str
    odpovede: list[str]
    tema: str

    @property
    def hlavna_odpoved(self) -> str:
        return self.odpovede[0] if self.odpovede else ""


def nacitaj_otazky() -> dict[str, list[Otazka]]:
    """Načíta otázky z JSON súborov vo formáte {otazka, odpovede}."""
    banka: dict[str, list[Otazka]] = {}
    for path in sorted(OTAZKY_DIR.glob("*.json")):
        tema = path.stem
        if tema in VYNECHANE_TEMY:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
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
    spravne_r1: int = 0
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
    temy_r2: list[str] = field(default_factory=list)
    pouzite_otazky: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.temy_r2 = random.sample(list(self.banka.keys()), min(POCET_TEM_R2, len(self.banka)))

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

    def cakaj_odpovede_r1(self, hrac: Hrac, pocet: int) -> list[str] | None:
        msg = self.cakaj_odpoved(hrac, "odpovede_r1")
        if msg is None or msg.get("typ") == "quit":
            return None
        odpovede = list(msg.get("odpovede", []))
        while len(odpovede) < pocet:
            odpovede.append("")
        return odpovede[:pocet]

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

    def pytaj_oboch(self, otazka: Otazka, vklad: int, kolo: int) -> bool:
        """Položí otázku obom hráčom (2×) a pripočíta/odpočíta body podľa vkladu."""
        vysledky: dict[int, bool] = {}

        for hrac in self.hraci:
            self.odosli_hracovi(hrac, {
                "typ": "otazka",
                "kolo": kolo,
                "tema": otazka.tema,
                "text": otazka.otazka,
                "vklad": vklad,
                "hrac": hrac.cislo,
            })
            msg = self.cakaj_odpoved(hrac, "odpoved")
            if msg is None or msg.get("typ") == "quit":
                return False

            spravne = je_spravna(msg.get("odpoved", ""), otazka.odpovede)
            vysledky[hrac.cislo] = spravne
            if spravne:
                hrac.body += vklad
            else:
                hrac.body -= vklad

            self.odosli_vsetkym({
                "typ": "vysledok_otazky",
                "kolo": kolo,
                "hrac": hrac.cislo,
                "spravne": spravne,
                "spravna_odpoved": otazka.hlavna_odpoved,
                "body": hrac.body,
            })

        return True

    def kolo_1_rychlovka(self) -> bool:
        """10 otázok naraz obom hráčom, 60 s spoločne, 30 bodov za správnu."""
        self.odosli_vsetkym({
            "typ": "zaciatok_kola",
            "kolo": 1,
            "popis": "Rýchlovka – 10 otázok naraz za 60 sekúnd",
            "cas_sekund": CAS_R1_SEKUND,
            "pocet_otazok": POCET_OTAZOK_R1,
        })

        vsetky_temy = list(self.banka.keys())
        otazky_r1: list[Otazka] = []
        for _ in range(POCET_OTAZOK_R1):
            tema = random.choice(vsetky_temy)
            otazka = vyber_otazku(self.banka, tema, self.pouzite_otazky)
            if otazka:
                otazky_r1.append(otazka)

        if not otazky_r1:
            return False

        self.odosli_vsetkym({
            "typ": "rychlovka",
            "kolo": 1,
            "cas_sekund": CAS_R1_SEKUND,
            "otazky": [
                {"cislo": i + 1, "text": o.otazka}
                for i, o in enumerate(otazky_r1)
            ],
        })

        odpovede_map: dict[int, list[str] | None] = {}
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(self.cakaj_odpovede_r1, hrac, len(otazky_r1)): hrac
                for hrac in self.hraci
            }
            for future in as_completed(futures):
                hrac = futures[future]
                odpovede_map[hrac.cislo] = future.result()

        for hrac in self.hraci:
            if odpovede_map.get(hrac.cislo) is None:
                return False

        for i, otazka in enumerate(otazky_r1):
            for hrac in self.hraci:
                odp = odpovede_map[hrac.cislo][i]
                spravne = je_spravna(odp, otazka.odpovede)
                if spravne:
                    hrac.body += BODY_ZA_SPRAVNU_R1
                    hrac.spravne_r1 += 1

                self.odosli_vsetkym({
                    "typ": "vysledok_otazky",
                    "kolo": 1,
                    "cislo": i + 1,
                    "hrac": hrac.cislo,
                    "spravne": spravne,
                    "spravna_odpoved": otazka.hlavna_odpoved,
                    "body": hrac.body,
                })

        for hrac in self.hraci:
            if hrac.spravne_r1 == len(otazky_r1):
                hrac.body += BONUS_VSETKY_SPRAVNE_R1
                self.odosli_vsetkym({
                    "typ": "bonus",
                    "kolo": 1,
                    "hrac": hrac.cislo,
                    "body_bonus": BONUS_VSETKY_SPRAVNE_R1,
                    "body": hrac.body,
                    "sprava": "Všetky odpovede správne – bonus 100 bodov!",
                })

        self.odosli_vsetkym({
            "typ": "koniec_kola",
            "kolo": 1,
            "body": {h.cislo: h.body for h in self.hraci},
        })
        return True

    def kolo_2_vlastny_vyber(self) -> bool:
        """Hráči striedavo vyberajú tému a vklad (20–100), otázka sa pýta obom."""
        volne_temy = list(self.temy_r2)

        self.odosli_vsetkym({
            "typ": "zaciatok_kola",
            "kolo": 2,
            "popis": "Vlastný výber témy a vkladu (20–100 bodov), každá otázka sa pýta obom",
            "temy": volne_temy,
            "pocet_kol": POCET_KOL_R2,
            "vklad_min": VKLAD_R2_MIN,
            "vklad_max": VKLAD_R2_MAX,
        })

        for k in range(POCET_KOL_R2):
            hrac = self.hraci[k % 2]
            if not volne_temy:
                volne_temy = list(self.temy_r2)

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
                "tema": tema,
                "vklad": vklad,
            })

            if not self.pytaj_oboch(otazka, vklad, kolo=2):
                return False

        self.odosli_vsetkym({
            "typ": "koniec_kola",
            "kolo": 2,
            "body": {h.cislo: h.body for h in self.hraci},
        })
        return True

    def kolo_3_vyber_supera(self) -> bool:
        """Súper vyberá tému a vklad (20–350), otázka sa pýta obom."""
        vsetky_temy = list(self.banka.keys())

        self.odosli_vsetkym({
            "typ": "zaciatok_kola",
            "kolo": 3,
            "popis": "Súper vyberá tému a vklad (20–350 bodov), každá otázka sa pýta obom",
            "pocet_kol": POCET_KOL_R3,
            "vklad_min": VKLAD_R3_MIN,
            "vklad_max": VKLAD_R3_MAX,
        })

        for k in range(POCET_KOL_R3):
            vyberajuci = self.hraci[k % 2]
            super = self.hraci[1 - (k % 2)]

            tema = self.cakaj_temu(vyberajuci, vsetky_temy, "super")
            if tema is None:
                return False

            vklad = self.cakaj_vklad(vyberajuci, VKLAD_R3_MIN, VKLAD_R3_MAX)
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

            if not self.pytaj_oboch(otazka, vklad, kolo=3):
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
            "sprava": (
                f"Víťaz: Hráč {vitaz}!" if vitaz else "Remíza!"
            ),
        })

    def spusti(self) -> None:
        self.odosli_vsetkym({
            "typ": "start",
            "pravidla": {
                "kolo1": f"{POCET_OTAZOK_R1} otázok, {BODY_ZA_SPRAVNU_R1} bodov/odpoveď, bonus {BONUS_VSETKY_SPRAVNE_R1}",
                "kolo2": f"Vklad {VKLAD_R2_MIN}–{VKLAD_R2_MAX}, {POCET_KOL_R2} otázok (každá 2×)",
                "kolo3": f"Vklad {VKLAD_R3_MIN}–{VKLAD_R3_MAX}, súper vyberá tému",
            },
            "temy_kola2": self.temy_r2,
        })

        if not self.kolo_1_rychlovka():
            return
        if not self.kolo_2_vlastny_vyber():
            return
        if not self.kolo_3_vyber_supera():
            return
        self.ukoncenie()


def herna_slucka(spojenia: list[socket.socket], adresy: list) -> None:
    print(f"[SERVER] Obaja hráči pripojení: {adresy[0]} a {adresy[1]}")

    banka = nacitaj_otazky()
    if not banka:
        for s in spojenia:
            posli(s, {"typ": "chyba", "sprava": "Nenačítané žiadne otázky."})
        return

    hraci = [Hrac(spojenia[i], i + 1) for i in range(2)]

    for hrac in hraci:
        posli(hrac.spojenie, {
            "typ": "vitaj",
            "hrac": hrac.cislo,
            "sutaziaci": "Hráči stoja oproti sebe – pripravte sa na DUEL!",
        })

    hra = DuelHra(hraci=hraci, banka=banka)
    hra.spusti()

    for s in spojenia:
        try:
            s.close()
        except OSError:
            pass
    print("[SERVER] Hra skončila, spojenia zatvorené.")


def main() -> None:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((HOST, PORT))
    server_socket.listen(MAX_HRACOV)
    print(f"[SERVER] DUEL – počúvam na {HOST}:{PORT}, čakám na {MAX_HRACOV} hráčov...")

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
