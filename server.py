"""
TCP Server – základ pre sieťovú hru pre dvoch hráčov
=====================================================
Postup:
  1. Server čaká na dvoch hráčov (blokujúce accept()).
  2. Po pripojení oboch spustí hernú slučku v samostatnom vlákne.
  3. Hráči sa striedajú – server prepošle ťah druhému hráčovi.
  4. Hra končí, keď niektorý hráč pošle "QUIT" alebo zatvorí spojenie.

Rozšírenie na konkrétnu hru:
  - Do funkcie `spracuj_tah()` pridajte logiku (kontrola víťaza, validácia ťahu...).
  - Správy posielajte ako JSON: json.dumps({"tah": [r, c], "hrac": 1})
"""

import socket
import threading


HOST = "0.0.0.0"   # počúvame na všetkých rozhraniach
PORT = 65432        # ľubovoľný voľný port > 1024
MAX_HRACOV = 2


def spracuj_tah(tah: str, cislo_hraca: int) -> str:
    """
    Sem príde každý ťah od hráča.
    Zatiaľ iba prepošleme ťah – sem doplňte hernú logiku.

    Návratová hodnota je správa, ktorú dostane DRUHÝ hráč.
    Ak vrátite None, hra pokračuje bez odoslania správy.
    """
    return f"HRAC{cislo_hraca}:{tah}"


def herná_slučka(spojenia: list, adresy: list):
    """Riadi striedanie hráčov a preposielanie ťahov."""
    print(f"[SERVER] Obaja hráči pripojení: {adresy[0]} a {adresy[1]}")

    # Upozorníme hráčov, kto je kto
    for i, spojenie in enumerate(spojenia):
        spojenie.sendall(f"VITAJ Hráč {i + 1}\n".encode())

    na_rade = 0  # index hráča, ktorý je na rade (0 alebo 1)

    while True:
        aktualny  = spojenia[na_rade]
        druhy     = spojenia[1 - na_rade]

        # Informujeme aktuálneho hráča, že je na rade
        try:
            aktualny.sendall(b"TVOJ TAH\n")
        except OSError:
            break

        # Prijmeme ťah
        try:
            data = aktualny.recv(1024)
        except OSError:
            break

        if not data:
            print(f"[SERVER] Hráč {na_rade + 1} sa odpojil.")
            break

        tah = data.decode().strip()
        print(f"[SERVER] Hráč {na_rade + 1} zahral: {tah}")

        if tah.upper() == "QUIT":
            druhy.sendall(f"KONIEC Súper sa odpojil.\n")
            break

        # Spracujeme ťah a pošleme druhému hráčovi
        sprava = spracuj_tah(tah, na_rade + 1)
        try:
            druhy.sendall(f"{sprava}\n".encode())
        except OSError:
            break

        # Vymeníme hráčov
        na_rade = 1 - na_rade

    # Upraceme spojenia
    for s in spojenia:
        try:
            s.close()
        except OSError:
            pass
    print("[SERVER] Hra skončila, spojenia zatvorené.")


def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Umožní opätovné spustenie servera bez čakania na TIME_WAIT
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_socket.bind((HOST, PORT))
    server_socket.listen(MAX_HRACOV)
    print(f"[SERVER] Počúvam na {HOST}:{PORT} – čakám na {MAX_HRACOV} hráčov...")

    spojenia = []
    adresy   = []

    while len(spojenia) < MAX_HRACOV:
        spojenie, adresa = server_socket.accept()
        spojenia.append(spojenie)
        adresy.append(adresa)
        print(f"[SERVER] Pripojený hráč {len(spojenia)}: {adresa}")
        spojenie.sendall(f"CAKAJ Pripojení: {len(spojenia)}/{MAX_HRACOV}\n".encode())

    # Spustíme hernú slučku v samostatnom vlákne
    vlakno = threading.Thread(target=herná_slučka, args=(spojenia, adresy), daemon=True)
    vlakno.start()
    vlakno.join()

    server_socket.close()
    print("[SERVER] Server ukončený.")


if __name__ == "__main__":
    main()