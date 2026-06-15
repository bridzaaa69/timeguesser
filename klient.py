"""
TCP Klient – základ pre sieťovú hru pre dvoch hráčov
=====================================================
Postup:
  1. Klient sa pripojí na server.
  2. Čaká na správu "TVOJ TAH" – vtedy môže zadať ťah.
  3. Keď nie je na rade, čaká na ťah súpera.
  4. Hra končí správou "KONIEC ..." alebo zadaním "quit".

Rozšírenie na konkrétnu hru:
  - Namiesto vstupu z klávesnice zobrazte hernú plochu (napr. mriežku piškvoriek).
  - Ťahy posielajte ako JSON: json.dumps({"row": r, "col": c})
"""

import socket
import threading


HOST = "127.0.0.1"   # IP adresa servera (localhost pre testovanie)
PORT = 65432


def prijimaj_spravy(spojenie: socket.socket, stop_event: threading.Event):
    """
    Beží v samostatnom vlákne – neustále číta správy zo servera
    a vypisuje ich na obrazovku.
    """
    while not stop_event.is_set():
        try:
            data = spojenie.recv(1024)
        except OSError:
            break

        if not data:
            print("\n[KLIENT] Server ukončil spojenie.")
            stop_event.set()
            break

        sprava = data.decode().strip()
        print(f"\n[SERVER] {sprava}")

        if sprava.startswith("KONIEC"):
            stop_event.set()
            break


def main():
    spojenie = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        spojenie.connect((HOST, PORT))
        print(f"[KLIENT] Pripojený na {HOST}:{PORT}")
    except ConnectionRefusedError:
        print("[KLIENT] Nepodarilo sa pripojiť. Je server spustený?")
        return

    stop_event = threading.Event()

    # Vlákno na prijímanie správ (neblokuje zadávanie ťahov)
    vlakno = threading.Thread(
        target=prijimaj_spravy,
        args=(spojenie, stop_event),
        daemon=True
    )
    vlakno.start()

    # Hlavná slučka – zadávanie ťahov
    while not stop_event.is_set():
        try:
            tah = input()   # napr. "A1", "3 2", alebo JSON ťah
        except EOFError:
            break

        if not tah.strip():
            continue

        try:
            spojenie.sendall(f"{tah}\n".encode())
        except OSError:
            print("[KLIENT] Spojenie prerušené.")
            break

        if tah.strip().upper() == "QUIT":
            break

    stop_event.set()
    spojenie.close()
    print("[KLIENT] Spojenie ukončené.")


if __name__ == "__main__":
    main()