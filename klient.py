"""
TCP Klient s grafickým rozhraním pre reláciu DUEL.
Pripojí sa na server cez TCP, komunikuje JSON správami ukončenými \\n.

Kolo 1 – rýchlovka: server pošle 10 otázok naraz, hráč odpovedá na všetky
  do 60 sekúnd a odošle jednu dávku odpovedí.
Kolo 2/3: jednotlivé otázky, výber témy vpravo, vklad.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

try:
    from PIL import Image, ImageTk

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

HOST = "127.0.0.1"
PORT = 65432
ASSETS_DIR = Path(__file__).parent / "assets"
BACKGROUND = ASSETS_DIR / "background.jpg"

BG = "#1a0a2e"
PANEL = "#2d1b4e"
ACCENT = "#c9a227"
TEXT = "#f5f0e8"
SUCCESS = "#2ecc71"
ERROR = "#e74c3c"


class DuelKlient:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("DUEL – Klient")
        self.root.configure(bg=BG)
        self.root.geometry("960x680")
        self.root.minsize(800, 560)

        self.spojenie: socket.socket | None = None
        self.stop_event = threading.Event()
        self.spravy: queue.Queue[dict] = queue.Queue()
        self.buffer = ""

        self.moje_cislo = 0
        self.moje_body = 0
        self.aktualne_kolo = 0
        self.casovac_id: str | None = None
        self.zostavajuci_cas = 0
        self.rychlovka_aktivna = False
        self.rychlovka_entries: list[tk.Entry] = []

        self._vytvor_pripojenie()
        self._vytvor_hlavny_obsah()
        self._spracuj_frontu()

    def _vytvor_pripojenie(self) -> None:
        frame = tk.Frame(self.root, bg=PANEL, pady=8, padx=12)
        frame.pack(fill=tk.X)

        tk.Label(frame, text="Server:", bg=PANEL, fg=TEXT).pack(side=tk.LEFT)
        self.host_entry = tk.Entry(frame, width=14)
        self.host_entry.insert(0, HOST)
        self.host_entry.pack(side=tk.LEFT, padx=(4, 12))

        tk.Label(frame, text="Port:", bg=PANEL, fg=TEXT).pack(side=tk.LEFT)
        self.port_entry = tk.Entry(frame, width=7)
        self.port_entry.insert(0, str(PORT))
        self.port_entry.pack(side=tk.LEFT, padx=(4, 12))

        self.btn_pripoj = tk.Button(
            frame, text="Pripojiť", command=self.pripoj,
            bg=ACCENT, fg="#1a0a2e", font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, padx=12,
        )
        self.btn_pripoj.pack(side=tk.LEFT)

        self.status_label = tk.Label(
            frame, text="Nepripojený", bg=PANEL, fg=ACCENT,
            font=("Segoe UI", 10),
        )
        self.status_label.pack(side=tk.RIGHT, padx=8)

        self.body_label = tk.Label(
            frame, text="Body: 0", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 11, "bold"),
        )
        self.body_label.pack(side=tk.RIGHT, padx=16)

    def _vytvor_hlavny_obsah(self) -> None:
        paned = tk.PanedWindow(
            self.root, orient=tk.HORIZONTAL, bg=BG,
            sashwidth=4, sashrelief=tk.FLAT,
        )
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        lavy = tk.Frame(paned, bg=PANEL, padx=16, pady=12)
        paned.add(lavy, minsize=480, stretch="always")

        self.bg_label = tk.Label(lavy, bg=PANEL)
        self.bg_label.pack(fill=tk.X, pady=(0, 12))
        self._nacitaj_pozadie()

        self.kolo_label = tk.Label(
            lavy, text="", bg=PANEL, fg=ACCENT,
            font=("Segoe UI", 10, "italic"),
        )
        self.kolo_label.pack(anchor=tk.W)

        self.timer_label = tk.Label(
            lavy, text="", bg=PANEL, fg=ACCENT,
            font=("Segoe UI", 22, "bold"),
        )
        self.timer_label.pack(anchor=tk.W, pady=(0, 4))

        # ── Kolo 2/3: jedna otázka ──
        self.single_frame = tk.Frame(lavy, bg=PANEL)
        self.single_frame.pack(fill=tk.BOTH, expand=True)

        self.otazka_label = tk.Label(
            self.single_frame, text="Pripojte sa na server…",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 16, "bold"),
            wraplength=440, justify=tk.LEFT, anchor=tk.W,
        )
        self.otazka_label.pack(fill=tk.X, pady=(8, 16))

        odp_frame = tk.Frame(self.single_frame, bg=PANEL)
        odp_frame.pack(fill=tk.X, pady=(0, 8))

        self.odpoved_entry = tk.Entry(
            odp_frame, font=("Segoe UI", 14), state=tk.DISABLED,
        )
        self.odpoved_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.odpoved_entry.bind("<Return>", lambda e: self.odosli_odpoved())

        self.btn_odpoved = tk.Button(
            odp_frame, text="Odoslať", command=self.odosli_odpoved,
            bg=ACCENT, fg="#1a0a2e", font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, padx=14, state=tk.DISABLED,
        )
        self.btn_odpoved.pack(side=tk.LEFT, padx=(8, 0))

        # ── Kolo 1: 10 otázok naraz ──
        self.rychlovka_frame = tk.Frame(lavy, bg=PANEL)

        tk.Label(
            self.rychlovka_frame, text="Rýchlovka – odpovedzte na všetkých 10 otázok:",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        scroll_outer = tk.Frame(self.rychlovka_frame, bg=PANEL)
        scroll_outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(scroll_outer, bg=PANEL, highlightthickness=0)
        scroll = tk.Scrollbar(scroll_outer, command=canvas.yview)
        self.rychlovka_inner = tk.Frame(canvas, bg=PANEL)

        self.rychlovka_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=self.rychlovka_inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.btn_rychlovka = tk.Button(
            self.rychlovka_frame, text="Odoslať všetky odpovede",
            command=self.odosli_rychlovku,
            bg=ACCENT, fg="#1a0a2e", font=("Segoe UI", 11, "bold"),
            relief=tk.FLAT, state=tk.DISABLED,
        )
        self.btn_rychlovka.pack(fill=tk.X, pady=(10, 0))

        self.feedback_label = tk.Label(
            lavy, text="", bg=PANEL, fg=TEXT,
            font=("Segoe UI", 11), wraplength=440, justify=tk.LEFT,
        )
        self.feedback_label.pack(fill=tk.X, pady=(8, 0))

        # ── Pravý panel – témy ──
        pravy = tk.Frame(paned, bg=PANEL, padx=12, pady=12, width=260)
        paned.add(pravy, minsize=220, stretch="never")

        tk.Label(
            pravy, text="Tematické okruhy",
            bg=PANEL, fg=ACCENT, font=("Segoe UI", 13, "bold"),
        ).pack(anchor=tk.W, pady=(0, 8))

        self.temy_info = tk.Label(
            pravy, text="Kolo 1 – rýchlovka\n(bez výberu témy)",
            bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "italic"),
            wraplength=220, justify=tk.LEFT,
        )
        self.temy_info.pack(anchor=tk.W, pady=(0, 6))

        list_frame = tk.Frame(pravy, bg=PANEL)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scroll_t = tk.Scrollbar(list_frame)
        scroll_t.pack(side=tk.RIGHT, fill=tk.Y)

        self.temy_listbox = tk.Listbox(
            list_frame, font=("Segoe UI", 11),
            bg="#3d2a6b", fg=TEXT, selectbackground=ACCENT,
            selectforeground="#1a0a2e", activestyle=tk.NONE,
            yscrollcommand=scroll_t.set, state=tk.DISABLED,
            highlightthickness=0, bd=0,
        )
        self.temy_listbox.pack(fill=tk.BOTH, expand=True)
        scroll_t.config(command=self.temy_listbox.yview)

        self.btn_tema = tk.Button(
            pravy, text="Potvrdiť tému", command=self.odosli_temu,
            bg=ACCENT, fg="#1a0a2e", font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, state=tk.DISABLED,
        )
        self.btn_tema.pack(fill=tk.X, pady=(10, 16))

        vklad_frame = tk.LabelFrame(
            pravy, text=" Vklad (body) ", bg=PANEL, fg=ACCENT,
            font=("Segoe UI", 10, "bold"),
        )
        vklad_frame.pack(fill=tk.X)

        self.vklad_spin = tk.Spinbox(
            vklad_frame, from_=20, to=100, increment=10,
            font=("Segoe UI", 12), width=8, state=tk.DISABLED,
        )
        self.vklad_spin.pack(side=tk.LEFT, padx=8, pady=8)

        self.btn_vklad = tk.Button(
            vklad_frame, text="OK", command=self.odosli_vklad,
            bg=ACCENT, fg="#1a0a2e", font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT, state=tk.DISABLED,
        )
        self.btn_vklad.pack(side=tk.LEFT, padx=4, pady=8)

    def _nacitaj_pozadie(self) -> None:
        if HAS_PIL and BACKGROUND.exists():
            img = Image.open(BACKGROUND)
            img = img.resize((520, 160), Image.LANCZOS)
            self._bg_photo = ImageTk.PhotoImage(img)
            self.bg_label.configure(image=self._bg_photo)
        else:
            self.bg_label.configure(
                text="[ DUEL ]", fg=ACCENT, font=("Segoe UI", 28, "bold"), height=4,
            )

    # ── TCP sieť ───────────────────────────────────────────────

    def pripoj(self) -> None:
        if self.spojenie:
            return
        host = self.host_entry.get().strip()
        try:
            port = int(self.port_entry.get().strip())
        except ValueError:
            messagebox.showerror("Chyba", "Neplatný port.")
            return
        try:
            self.spojenie = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.spojenie.connect((host, port))
        except OSError as exc:
            messagebox.showerror("Chyba pripojenia", str(exc))
            self.spojenie = None
            return

        self.stop_event.clear()
        self.btn_pripoj.configure(state=tk.DISABLED)
        self.status_label.configure(text="Pripojený – čakám na hru…")
        threading.Thread(target=self._prijimaj, daemon=True).start()

    def _posli(self, sprava: dict) -> None:
        if not self.spojenie:
            return
        try:
            self.spojenie.sendall(
                (json.dumps(sprava, ensure_ascii=False) + "\n").encode()
            )
        except OSError:
            self._odpoj("Spojenie prerušené.")

    def _prijimaj(self) -> None:
        while not self.stop_event.is_set() and self.spojenie:
            try:
                data = self.spojenie.recv(4096)
            except OSError:
                break
            if not data:
                break
            self.buffer += data.decode()
            while "\n" in self.buffer:
                riadok, self.buffer = self.buffer.split("\n", 1)
                if riadok.strip():
                    try:
                        self.spravy.put(json.loads(riadok))
                    except json.JSONDecodeError:
                        pass
        self.spravy.put({"typ": "_odpoj"})

    def _odpoj(self, dovod: str = "") -> None:
        self.stop_event.set()
        if self.spojenie:
            try:
                self.spojenie.close()
            except OSError:
                pass
            self.spojenie = None
        self.btn_pripoj.configure(state=tk.NORMAL)
        self._zastav_timer()
        self._nastav_odpoved(False)
        self._nastav_temy(False)
        self._nastav_vklad(False)
        self._skry_rychlovku()
        if dovod:
            self.status_label.configure(text=dovod)

    def _spracuj_frontu(self) -> None:
        try:
            while True:
                self._spracuj_spravu(self.spravy.get_nowait())
        except queue.Empty:
            pass
        self.root.after(50, self._spracuj_frontu)

    # ── správy zo servera ──────────────────────────────────────

    def _spracuj_spravu(self, msg: dict) -> None:
        typ = msg.get("typ")

        if typ == "_odpoj":
            self._odpoj("Odpojené od servera.")
            return
        if typ == "cakaj":
            self.status_label.configure(
                text=f"Čakám… {msg.get('pripojeni')}/{msg.get('max')} hráčov"
            )
            return
        if typ == "vitaj":
            self.moje_cislo = msg.get("hrac", 0)
            self.status_label.configure(text=f"Hráč {self.moje_cislo}")
            self.otazka_label.configure(text=msg.get("sutaziaci", "Pripravte sa!"))
            return
        if typ == "start":
            self.feedback_label.configure(text="", fg=TEXT)
            return

        if typ == "zaciatok_kola":
            self.aktualne_kolo = msg.get("kolo", 0)
            self._zastav_timer()
            self._nastav_odpoved(False)
            self.kolo_label.configure(
                text=f"Kolo {self.aktualne_kolo}: {msg.get('popis', '')}"
            )
            if self.aktualne_kolo == 1:
                self._nastav_panel_tem(False, "Kolo 1 – rýchlovka\n(bez výberu témy)")
            else:
                temy = msg.get("temy", [])
                if temy:
                    self._napln_temy(temy)
                self._nastav_panel_tem(True, "Vyberte tému z ponuky vpravo.")
            return

        if typ == "rychlovka":
            self._zobraz_rychlovku(msg)
            return

        if typ == "vyber_temy":
            if msg.get("hrac") == self.moje_cislo:
                self._napln_temy(msg.get("temy", []))
                kto = (
                    "Vyberte tému"
                    if msg.get("kto_vybera") == "vlastny"
                    else "Vyberte tému pre súpera"
                )
                self.temy_info.configure(text=kto)
                self._nastav_temy(True)
                self.feedback_label.configure(text=kto + ".", fg=TEXT)
            else:
                self._nastav_temy(False)
                self.feedback_label.configure(text="Súper vyberá tému…", fg=TEXT)
            return

        if typ == "vklad":
            if msg.get("hrac") == self.moje_cislo:
                vmin, vmax = msg.get("min", 20), msg.get("max", 100)
                self.vklad_spin.configure(from_=vmin, to=vmax, state=tk.NORMAL)
                self.vklad_spin.delete(0, tk.END)
                self.vklad_spin.insert(0, str(vmin))
                self._nastav_vklad(True)
                self.feedback_label.configure(
                    text=f"Zadajte vklad ({vmin}–{vmax} bodov).", fg=TEXT,
                )
            else:
                self._nastav_vklad(False)
                self.feedback_label.configure(text="Súper určuje vklad…", fg=TEXT)
            return

        if typ == "info_kola":
            self.feedback_label.configure(
                text=(
                    f"Téma: {msg.get('tema')} | Vklad: {msg.get('vklad')} bodov"
                    f" | Vyberal: Hráč {msg.get('vyberal')}"
                ),
                fg=TEXT,
            )
            self._nastav_temy(False)
            self._nastav_vklad(False)
            return

        if typ == "otazka":
            if msg.get("hrac") == self.moje_cislo:
                self._skry_rychlovku()
                self.otazka_label.configure(text=msg.get("text", ""))
                self.feedback_label.configure(text="", fg=TEXT)
                self.odpoved_entry.configure(state=tk.NORMAL)
                self.odpoved_entry.delete(0, tk.END)
                self.odpoved_entry.focus_set()
                self._nastav_odpoved(True)
            else:
                self.feedback_label.configure(text="Súper odpovedá…", fg=TEXT)
                self._nastav_odpoved(False)
            return

        if typ == "vysledok_otazky":
            if msg.get("hrac") == self.moje_cislo:
                self._nastav_odpoved(False)
                self.odpoved_entry.configure(state=tk.DISABLED)
                if msg.get("spravne"):
                    body_txt = 30 if self.aktualne_kolo == 1 else msg.get("vklad", 0)
                    self.feedback_label.configure(
                        text=f"✓ Správne! (+{body_txt} bodov)", fg=SUCCESS,
                    )
                else:
                    self.feedback_label.configure(
                        text=f"✗ Nesprávne. Správna: {msg.get('spravna_odpoved', '')}",
                        fg=ERROR,
                    )
            if msg.get("hrac") == self.moje_cislo and "body" in msg:
                self.moje_body = msg["body"]
                self.body_label.configure(text=f"Body: {self.moje_body}")
            return

        if typ == "bonus":
            if msg.get("hrac") == self.moje_cislo:
                self.feedback_label.configure(
                    text=msg.get("sprava", "Bonus!"), fg=SUCCESS,
                )
                self.moje_body = msg.get("body", self.moje_body)
                self.body_label.configure(text=f"Body: {self.moje_body}")
            return

        if typ == "koniec_kola":
            self._zastav_timer()
            self._skry_rychlovku()
            body = msg.get("body", {})
            self.moje_body = body.get(str(self.moje_cislo), body.get(self.moje_cislo, self.moje_body))
            self.body_label.configure(text=f"Body: {self.moje_body}")
            self._nastav_odpoved(False)
            self._nastav_temy(False)
            self._nastav_vklad(False)
            self.feedback_label.configure(
                text=f"Koniec kola {msg.get('kolo')}. Vaše body: {self.moje_body}",
                fg=TEXT,
            )
            return

        if typ == "koniec_hry":
            body = msg.get("body", {})
            moje = body.get(str(self.moje_cislo), body.get(self.moje_cislo, 0))
            vitaz = msg.get("vitaz", 0)
            if vitaz == self.moje_cislo:
                vysledok = "Vyhrali ste!"
            elif vitaz == 0:
                vysledok = "Remíza!"
            else:
                vysledok = f"Prehrali ste. Víťaz: Hráč {vitaz}."
            messagebox.showinfo(
                "Koniec hry",
                f"{msg.get('sprava', '')}\n\nVaše body: {moje}\n{vysledok}",
            )
            self._odpoj("Hra skončila.")
            return

        if typ == "chyba":
            messagebox.showerror("Chyba", msg.get("sprava", "Neznáma chyba"))

    # ── rýchlovka (kolo 1) ─────────────────────────────────────

    def _zobraz_rychlovku(self, msg: dict) -> None:
        self.rychlovka_aktivna = True
        self.single_frame.pack_forget()
        self.rychlovka_frame.pack(fill=tk.BOTH, expand=True, before=self.feedback_label)

        for widget in self.rychlovka_inner.winfo_children():
            widget.destroy()
        self.rychlovka_entries.clear()

        for ot in msg.get("otazky", []):
            row = tk.Frame(self.rychlovka_inner, bg=PANEL, pady=4)
            row.pack(fill=tk.X)

            tk.Label(
                row, text=f"{ot.get('cislo', '?')}. {ot.get('text', '')}",
                bg=PANEL, fg=TEXT, font=("Segoe UI", 11),
                wraplength=420, justify=tk.LEFT, anchor=tk.W,
            ).pack(fill=tk.X)

            entry = tk.Entry(row, font=("Segoe UI", 12))
            entry.pack(fill=tk.X, ipady=4, pady=(2, 0))
            entry.bind("<Return>", lambda e: self._fokus_dalsia(entry))
            self.rychlovka_entries.append(entry)

        if self.rychlovka_entries:
            self.rychlovka_entries[0].focus_set()

        self.btn_rychlovka.configure(state=tk.NORMAL)
        self.feedback_label.configure(
            text="Odpovedzte na všetkých 10 otázok a odošlite naraz.", fg=TEXT,
        )
        self._spusti_timer(msg.get("cas_sekund", 60))

    def _fokus_dalsia(self, entry: tk.Entry) -> None:
        try:
            idx = self.rychlovka_entries.index(entry)
            if idx + 1 < len(self.rychlovka_entries):
                self.rychlovka_entries[idx + 1].focus_set()
        except ValueError:
            pass

    def _skry_rychlovku(self) -> None:
        if not self.rychlovka_aktivna:
            return
        self.rychlovka_aktivna = False
        self.rychlovka_frame.pack_forget()
        self.single_frame.pack(fill=tk.BOTH, expand=True)
        self.btn_rychlovka.configure(state=tk.DISABLED)
        self._zastav_timer()

    def odosli_rychlovku(self) -> None:
        if not self.spojenie or not self.rychlovka_aktivna:
            return
        odpovede = [e.get().strip() for e in self.rychlovka_entries]
        self._zastav_timer()
        self._posli({"typ": "odpovede_r1", "odpovede": odpovede})
        self.btn_rychlovka.configure(state=tk.DISABLED)
        for e in self.rychlovka_entries:
            e.configure(state=tk.DISABLED)
        self.feedback_label.configure(text="Odpovede odoslané, čakám na vyhodnotenie…", fg=TEXT)

    # ── ovládanie ────────────────────────────────────────────────

    def _nastav_odpoved(self, aktivne: bool) -> None:
        st = tk.NORMAL if aktivne else tk.DISABLED
        self.btn_odpoved.configure(state=st)
        if not aktivne and not self.rychlovka_aktivna:
            self.odpoved_entry.configure(state=tk.DISABLED)

    def _nastav_temy(self, aktivne: bool) -> None:
        st = tk.NORMAL if aktivne else tk.DISABLED
        self.temy_listbox.configure(state=st)
        self.btn_tema.configure(state=st)

    def _nastav_vklad(self, aktivne: bool) -> None:
        st = tk.NORMAL if aktivne else tk.DISABLED
        self.vklad_spin.configure(state=st if aktivne else tk.DISABLED)
        self.btn_vklad.configure(state=st)

    def _nastav_panel_tem(self, zobraz: bool, info: str) -> None:
        if zobraz:
            self.temy_info.configure(text=info)
        else:
            self.temy_info.configure(text="Kolo 1 – rýchlovka\n(bez výberu témy)")
            self.temy_listbox.delete(0, tk.END)
            self._nastav_temy(False)

    def _napln_temy(self, temy: list[str]) -> None:
        self.temy_listbox.configure(state=tk.NORMAL)
        self.temy_listbox.delete(0, tk.END)
        for tema in temy:
            self.temy_listbox.insert(tk.END, tema)
        if temy:
            self.temy_listbox.selection_set(0)

    def odosli_odpoved(self) -> None:
        if not self.spojenie:
            return
        self._posli({"typ": "odpoved", "odpoved": self.odpoved_entry.get().strip()})
        self._nastav_odpoved(False)
        self.odpoved_entry.configure(state=tk.DISABLED)

    def odosli_temu(self) -> None:
        sel = self.temy_listbox.curselection()
        if not sel:
            messagebox.showwarning("Téma", "Vyberte tému zo zoznamu.")
            return
        self._posli({"typ": "tema", "tema": self.temy_listbox.get(sel[0])})
        self._nastav_temy(False)

    def odosli_vklad(self) -> None:
        try:
            hodnota = int(self.vklad_spin.get())
        except ValueError:
            messagebox.showwarning("Vklad", "Zadajte platný počet bodov.")
            return
        self._posli({"typ": "vklad", "hodnota": hodnota})
        self._nastav_vklad(False)

    def _spusti_timer(self, sekund: int) -> None:
        self._zastav_timer()
        self.zostavajuci_cas = sekund
        self._tik_timer()

    def _tik_timer(self) -> None:
        self.timer_label.configure(text=f"⏱ {self.zostavajuci_cas} s")
        if self.zostavajuci_cas <= 0:
            if self.rychlovka_aktivna:
                self.odosli_rychlovku()
            else:
                self.odosli_odpoved()
            return
        self.zostavajuci_cas -= 1
        self.casovac_id = self.root.after(1000, self._tik_timer)

    def _zastav_timer(self) -> None:
        if self.casovac_id:
            self.root.after_cancel(self.casovac_id)
            self.casovac_id = None
        self.timer_label.configure(text="")

    def spusti(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._zavri)
        self.root.mainloop()

    def _zavri(self) -> None:
        if self.spojenie:
            self._posli({"typ": "quit"})
        self._odpoj()
        self.root.destroy()


def main() -> None:
    DuelKlient().spusti()


if __name__ == "__main__":
    main()
