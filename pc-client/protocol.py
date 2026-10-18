"""
3DS-Drop Protocol v1 — Implementación de Red Little-Endian (PC Client & Shared Primitives)
Compatible con Linux y Windows.
"""

from __future__ import annotations

import json
import os
import posixpath
import socket
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Constantes del Protocolo v1
UDP_PORT = 33333
TCP_PORT = 33334
DISCOVER_MAGIC = "3DSDROP_DISCOVER"
HERE_MAGIC = "3DSDROP_HERE"

PC_CHUNK_SIZE = 64 * 1024        # 64 KB bloques de envío TCP desde PC
SD_BUFFER_SIZE_3DS = 128 * 1024  # 128 KB bloque de escritura SDIO en 3DS
HEADER_STRUCT = struct.Struct("<I")  # uint32_t Little-Endian (4 bytes)
MAX_JSON_HEADER_SIZE = 4 * 1024 * 1024  # 4 MB límite de seguridad para JSON

# Reglas de enrutado inteligente (Quick Drop)
QUICK_DROP_ROUTES: Dict[str, str] = {
    ".cia": "/cias",
    ".3dsx": "/3ds",
    ".smdh": "/3ds",
    ".mp3": "/music",
    ".ogg": "/music",
    ".wav": "/music",
    ".flac": "/music",
    ".m4a": "/music",
    ".nds": "/roms",
    ".gba": "/roms",
    ".snes": "/roms",
    ".sfc": "/roms",
    ".nes": "/roms",
    ".gb": "/roms",
    ".gbc": "/roms",
    ".firm": "/luma/payloads",
}


class ProtocolError(Exception):
    """Error de comunicación o framing en el protocolo 3DS-Drop."""


@dataclass
class ConsoleInfo:
    """Información de la consola 3DS descubierta o conectada."""
    model: str
    ip: str
    tcp_port: int = TCP_PORT
    sd_free_bytes: int = 0
    sd_total_bytes: int = 0
    last_seen: float = 0.0

    @property
    def sd_used_bytes(self) -> int:
        return max(0, self.sd_total_bytes - self.sd_free_bytes)

    @property
    def sd_free_percent(self) -> float:
        if self.sd_total_bytes <= 0:
            return 0.0
        return (self.sd_free_bytes / self.sd_total_bytes) * 100.0

    @property
    def sd_used_percent(self) -> float:
        if self.sd_total_bytes <= 0:
            return 0.0
        return (self.sd_used_bytes / self.sd_total_bytes) * 100.0


@dataclass
class RemoteEntry:
    """Representa un archivo o carpeta remota en la microSD de la 3DS."""
    name: str
    path: str
    is_dir: bool
    size: int = 0
    mtime: int = 0


def format_size(num_bytes: float) -> str:
    """Formatea un tamaño en bytes a representación legible (KB, MB, GB)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(num_bytes)} {unit}"
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} TB"


def format_eta(seconds: float) -> str:
    """Formatea segundos restantes en formato legible mm:ss o hh:mm:ss."""
    if seconds < 0 or seconds == float("inf"):
        return "--:--"
    secs = int(round(seconds))
    if secs < 60:
        return f"00:{secs:02d}"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins:02d}:{secs:02d}"
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d}"


def normalize_remote_path(path: str) -> str:
    """Normaliza rutas remotas de microSD a formato POSIX con '/' inicial."""
    if not path:
        return "/"
    cleaned = path.replace("\\", "/").strip()
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    normalized = posixpath.normpath(cleaned)
    return "/" if normalized in ("", ".", "//") else normalized


def get_smart_destination(local_path: str | Path, fallback_dir: str = "/") -> str:
    """
    Determina la carpeta destino en la 3DS según la extensión del archivo (Quick Drop).
    Si no coincide con una extensión conocida, usa `fallback_dir`.
    """
    p = Path(local_path)
    ext = p.suffix.lower()
    target_folder = QUICK_DROP_ROUTES.get(ext, fallback_dir)
    return normalize_remote_path(posixpath.join(target_folder, p.name))


def recv_exact(sock: socket.socket, num_bytes: int) -> bytes:
    """Lee exactamente `num_bytes` desde un socket TCP o lanza ProtocolError si se corta."""
    buf = bytearray(num_bytes)
    view = memoryview(buf)
    received = 0
    while received < num_bytes:
        chunk_len = sock.recv_into(view[received:], num_bytes - received)
        if chunk_len == 0:
            raise ProtocolError(
                f"Conexión cerrada inesperadamente por la 3DS ({received}/{num_bytes} bytes leídos)."
            )
        received += chunk_len
    return bytes(buf)


def send_json_packet(sock: socket.socket, payload: dict) -> None:
    """Envía un paquete JSON precedido por su tamaño uint32_t Little-Endian."""
    raw_json = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = HEADER_STRUCT.pack(len(raw_json))
    sock.sendall(header + raw_json)


def recv_json_packet(sock: socket.socket) -> dict:
    """Recibe un encabezado uint32_t Little-Endian de 4 bytes y decodifica el JSON subsiguiente."""
    raw_header = recv_exact(sock, HEADER_STRUCT.size)
    (json_len,) = HEADER_STRUCT.unpack(raw_header)
    if json_len <= 0 or json_len > MAX_JSON_HEADER_SIZE:
        raise ProtocolError(f"Tamaño de encabezado JSON inválido: {json_len} bytes.")
    raw_json = recv_exact(sock, json_len)
    try:
        return json.loads(raw_json.decode("utf-8"))
    except Exception as exc:
        raise ProtocolError(f"JSON corrupto recibido desde la 3DS: {exc}") from exc


class ThreeDSClient:
    """
    Cliente TCP síncrono (diseñado para ejecutarse dentro de QThread / Workers)
    que implementa el protocolo 3DS-Drop v1 completo.
    """

    def __init__(self, ip: str, tcp_port: int = TCP_PORT, timeout: float = 10.0) -> None:
        self.ip = ip
        self.tcp_port = tcp_port
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        # Optimización TCP para baja latencia en comandos y alto throughput en datos
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
        except OSError:
            pass
        sock.connect((self.ip, self.tcp_port))
        return sock

    def ping(self) -> ConsoleInfo:
        """Consulta estado y espacio libre de la consola por TCP."""
        with self._connect() as sock:
            send_json_packet(sock, {"cmd": "PING"})
            resp = recv_json_packet(sock)
            if resp.get("status") != "OK":
                raise ProtocolError(resp.get("error", "Error en PING"))
            return ConsoleInfo(
                model=resp.get("model", "Nintendo 3DS"),
                ip=self.ip,
                tcp_port=self.tcp_port,
                sd_free_bytes=int(resp.get("sd_free_bytes", 0)),
                sd_total_bytes=int(resp.get("sd_total_bytes", 0)),
                last_seen=time.time(),
            )

    def list_dir(self, remote_path: str = "/") -> Tuple[List[RemoteEntry], int, int]:
        """
        Lista archivos y carpetas de `remote_path`.
        Retorna (entries, sd_free_bytes, sd_total_bytes).
        """
        norm_path = normalize_remote_path(remote_path)
        with self._connect() as sock:
            send_json_packet(sock, {"cmd": "LIST", "path": norm_path})
            resp = recv_json_packet(sock)
            if resp.get("status") != "OK":
                raise ProtocolError(resp.get("error", f"No se pudo listar {norm_path}"))

            entries: List[RemoteEntry] = []
            for item in resp.get("entries", []):
                name = item.get("name", "")
                if not name or name in (".", ".."):
                    continue
                full_entry_path = normalize_remote_path(posixpath.join(norm_path, name))
                entries.append(
                    RemoteEntry(
                        name=name,
                        path=full_entry_path,
                        is_dir=bool(item.get("is_dir", False)),
                        size=int(item.get("size", 0)),
                        mtime=int(item.get("mtime", 0)),
                    )
                )

            # Ordenar: primero carpetas alfabéticamente, luego archivos alfabéticamente
            entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
            sd_free = int(resp.get("sd_free_bytes", 0))
            sd_total = int(resp.get("sd_total_bytes", 0))
            return entries, sd_free, sd_total

    def mkdir(self, remote_path: str) -> None:
        """Crea un directorio remotamente de forma recursiva."""
        norm_path = normalize_remote_path(remote_path)
        with self._connect() as sock:
            send_json_packet(sock, {"cmd": "MKDIR", "path": norm_path})
            resp = recv_json_packet(sock)
            if resp.get("status") != "OK":
                raise ProtocolError(resp.get("error", f"Error creando directorio {norm_path}"))

    def delete(self, remote_path: str) -> None:
        """Elimina un archivo o carpeta en la microSD de la 3DS."""
        norm_path = normalize_remote_path(remote_path)
        with self._connect() as sock:
            send_json_packet(sock, {"cmd": "DELETE", "path": norm_path})
            resp = recv_json_packet(sock)
            if resp.get("status") != "OK":
                raise ProtocolError(resp.get("error", f"Error eliminando {norm_path}"))

    def upload_file(
        self,
        local_path: str | Path,
        remote_path: str,
        progress_cb: Optional[Callable[[int, int], bool]] = None,
    ) -> dict:
        """
        Sube un archivo de PC a 3DS siguiendo el flujo UPLOAD de 5 pasos:
        1. Envía encabezado JSON UPLOAD con path y size.
        2. Espera {"status": "READY"} de la 3DS.
        3. Envía `size` bytes en bloques de 64 KB.
        4. La 3DS acumula en bloques de 128 KB en RAM y vuelca a SD.
        5. Recibe {"status": "OK", "bytes_written": size}.

        Si `progress_cb(sent_bytes, total_bytes)` retorna False, se cancela la subida.
        """
        local_file = Path(local_path)
        if not local_file.is_file():
            raise ProtocolError(f"El archivo local no existe: {local_file}")

        file_size = local_file.stat().st_size
        norm_remote = normalize_remote_path(remote_path)

        with self._connect() as sock:
            sock.settimeout(30.0)
            send_json_packet(
                sock,
                {"cmd": "UPLOAD", "path": norm_remote, "size": file_size},
            )
            ready_resp = recv_json_packet(sock)
            if ready_resp.get("status") != "READY":
                raise ProtocolError(
                    ready_resp.get("error", f"La 3DS rechazó UPLOAD hacia {norm_remote}")
                )

            sent_bytes = 0
            if progress_cb is not None:
                if not progress_cb(0, file_size):
                    raise ProtocolError("Transferencia cancelada por el usuario.")

            with local_file.open("rb") as fp:
                while sent_bytes < file_size:
                    chunk = fp.read(PC_CHUNK_SIZE)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    sent_bytes += len(chunk)
                    if progress_cb is not None:
                        if not progress_cb(sent_bytes, file_size):
                            raise ProtocolError("Transferencia cancelada por el usuario.")

            final_resp = recv_json_packet(sock)
            if final_resp.get("status") != "OK":
                raise ProtocolError(
                    final_resp.get("error", "Error al cerrar el archivo en la microSD")
                )
            return final_resp

    def download_file(
        self,
        remote_path: str,
        local_path: str | Path,
        progress_cb: Optional[Callable[[int, int], bool]] = None,
    ) -> dict:
        """
        Descarga un archivo desde la microSD de la 3DS hacia la PC.
        """
        norm_remote = normalize_remote_path(remote_path)
        dest_file = Path(local_path)
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as sock:
            sock.settimeout(30.0)
            send_json_packet(
                sock,
                {"cmd": "DOWNLOAD", "path": norm_remote, "size": 0},
            )
            ready_resp = recv_json_packet(sock)
            if ready_resp.get("status") != "READY":
                raise ProtocolError(
                    ready_resp.get("error", f"No se pudo descargar {norm_remote}")
                )

            total_size = int(ready_resp.get("size", 0))
            received = 0

            if progress_cb is not None:
                if not progress_cb(0, total_size):
                    raise ProtocolError("Descarga cancelada por el usuario.")

            with dest_file.open("wb") as fp:
                while received < total_size:
                    to_read = min(PC_CHUNK_SIZE, total_size - received)
                    chunk = sock.recv(to_read)
                    if not chunk:
                        raise ProtocolError(
                            f"Conexión interrumpida durante descarga ({received}/{total_size} bytes)"
                        )
                    fp.write(chunk)
                    received += len(chunk)
                    if progress_cb is not None:
                        if not progress_cb(received, total_size):
                            raise ProtocolError("Descarga cancelada por el usuario.")
                fp.flush()

            final_resp = recv_json_packet(sock)
            if final_resp.get("status") != "OK":
                raise ProtocolError(final_resp.get("error", "Error al finalizar descarga"))
            return final_resp
