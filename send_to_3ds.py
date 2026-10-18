#!/usr/bin/env python3
"""
send_to_3ds.py — Enviador Wi-Fi directo (NetLoader / protocolo 3dslink) para 3DS-Drop
Creado por ElVichoPo (@vicente134)

Uso:
  python3 send_to_3ds.py [IP_DE_LA_3DS]

Si no indicas la IP, escanea automáticamente tu red local buscando una 3DS / 2DS
con The Homebrew Launcher abierto en modo NetLoader (botón Y, puerto 17491).
"""

import concurrent.futures
import os
import socket
import struct
import sys
import urllib.request
import zlib

PORT = 17491
CHUNK_SIZE = 16 * 1024
GITHUB_3DSX_URL = "https://github.com/vicente134/3DS-Drop/releases/latest/download/3DS-Drop.3dsx"


def find_3ds_on_lan() -> str:
    """Detecta automáticamente la IP de la 3DS / 2DS con NetLoader abierto en la subred local."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        return ""

    parts = local_ip.split(".")
    if len(parts) != 4:
        return ""
    prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
    print(f"[*] Buscando automáticamente tu 3DS / 2DS en la red {prefix}0/24...")

    def probe(host_id: int) -> str:
        ip = f"{prefix}{host_id}"
        try:
            with socket.create_connection((ip, PORT), timeout=0.35):
                return ip
        except Exception:
            return ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for result in ex.map(probe, range(1, 255)):
            if result:
                return result
    return ""


def ensure_3dsx_file(path: str) -> str:
    candidates = [
        path,
        "3DS-Drop.3dsx",
        os.path.join("3ds-homebrew", "3DS-Drop.3dsx"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    print(f"[*] Descargando 3DS-Drop.3dsx desde el GitHub oficial de ElVichoPo...")
    urllib.request.urlretrieve(GITHUB_3DSX_URL, "3DS-Drop.3dsx")
    return "3DS-Drop.3dsx"


def send_3dsx(ip: str, filepath: str) -> None:
    filename = "3DS-Drop.3dsx"
    name_bytes = filename.encode("utf-8") + b"\x00"
    filesize = os.path.getsize(filepath)

    print(f"[*] Conectando a la consola 3DS/2DS en {ip}:{PORT}...")
    with socket.create_connection((ip, PORT), timeout=10) as sock:
        sock.sendall(struct.pack("<I", len(name_bytes)))
        sock.sendall(name_bytes)
        sock.sendall(struct.pack("<I", filesize))

        resp = struct.unpack("<i", sock.recv(4))[0]
        if resp != 0:
            raise RuntimeError(f"El Homebrew Launcher rechazó la transferencia (código {resp})")

        compressor = zlib.compressobj(level=6)
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                comp = compressor.compress(chunk)
                if comp:
                    sock.sendall(struct.pack("<I", len(comp)))
                    sock.sendall(comp)

            tail = compressor.flush()
            if tail:
                sock.sendall(struct.pack("<I", len(tail)))
                sock.sendall(tail)

        cmd_arg = b"sdmc:/3ds/" + name_bytes
        sock.sendall(struct.pack("<I", len(cmd_arg)))
        sock.sendall(cmd_arg)

    print(f"[+] ¡3DS-Drop enviado con éxito a {ip}! Ya se guardó en tu SD y se está abriendo.")


def main() -> None:
    print("==========================================================")
    print("    Enviador Wi-Fi 3dslink — 3DS-Drop (By ElVichoPo)      ")
    print("==========================================================")
    ip = sys.argv[1].strip() if len(sys.argv) >= 2 else find_3ds_on_lan()
    if not ip:
        ip = input("Ingresa la IP que muestra tu 3DS/2DS al presionar [Y] en Homebrew Launcher: ").strip()
    if not ip:
        print("[!] Debes ingresar una dirección IP válida.")
        sys.exit(1)

    filepath = ensure_3dsx_file("3DS-Drop.3dsx")
    send_3dsx(ip, filepath)


if __name__ == "__main__":
    main()
