"""
Workers asíncronos basados en QThread para 3DS-Drop (PC Client).
Garantiza que tanto el radar UDP como las operaciones del explorador y la cola de transferencia
de archivos pesados se ejecuten en segundo plano sin congelar la interfaz Qt6.
"""

from __future__ import annotations

import json
import os
import posixpath
import queue
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from protocol import (
     ConsoleInfo,
    DISCOVER_MAGIC,
    HERE_MAGIC,
     RemoteEntry,
    TCP_PORT,
    ThreeDSClient,
    UDP_PORT,
    get_smart_destination,
    normalize_remote_path,
)


@dataclass
class TransferItem:
    """Representa una tarea individual o carpeta en la cola de transferencia."""
    task_id: int
    direction: str  # "UPLOAD" o "DOWNLOAD"
    local_path: Path
    remote_path: str
    is_dir: bool = False
    smart_route: bool = False
    total_bytes: int = 0
    transferred_bytes: int = 0
    status: str = "En cola"  # "En cola", "Transfiriendo", "Completado", "Error", "Cancelado"
    error_msg: str = ""


class UdpDiscoveryWorker(QThread):
    """
    Hilo de radar UDP (puerto 33333).
    Escucha anuncios broadcast de la 3DS y envía paquetes '3DSDROP_DISCOVER' activos.
    """

    console_found = Signal(object)  # Emite ConsoleInfo

    def __init__(self, udp_port: int = UDP_PORT, parent=None) -> None:
        super().__init__(parent)
        self.udp_port = udp_port
        self._running = True
        self._force_ping = True

    def trigger_scan(self) -> None:
        """Fuerza el envío inmediato de un paquete mágico de descubrimiento."""
        self._force_ping = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        # Socket receptor en puerto 33333 (con SO_REUSEADDR/SO_REUSEPORT para convivir con simulador local)
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        recv_sock.settimeout(0.35)

        bound_ok = False
        try:
            recv_sock.bind(("0.0.0.0", self.udp_port))
            bound_ok = True
        except OSError:
            # Si el puerto está ocupado en Windows sin SO_REUSEPORT, usamos puerto efímero
            try:
                recv_sock.bind(("0.0.0.0", 0))
                bound_ok = True
            except OSError:
                pass

        # Socket emisor para DISCOVER_MAGIC desde puerto efímero (recibe respuestas directas unicast)
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        probe_sock.settimeout(0.15)

        # Detectar prefijo de subred local (ej. "192.168.12.") para routers que bloquean broadcast Wi-Fi
        local_prefix = ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
                parts = lan_ip.split(".")
                if len(parts) == 4 and parts[0] != "127":
                    local_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}."
        except Exception:
            pass

        last_probe = 0.0
        magic_bytes = DISCOVER_MAGIC.encode("utf-8")

        while self._running:
            now = time.time()
            if self._force_ping or (now - last_probe >= 3.0):
                self._force_ping = False
                last_probe = now
                for target in ("255.255.255.255", "127.0.0.1"):
                    try:
                        probe_sock.sendto(magic_bytes, (target, self.udp_port))
                    except OSError:
                        pass
                # Barrido unicast rápido en la subred /24 por si el router Wi-Fi filtra broadcast
                if local_prefix:
                    for host_id in range(1, 255):
                        try:
                            probe_sock.sendto(magic_bytes, (f"{local_prefix}{host_id}", self.udp_port))
                        except OSError:
                            pass

            # Revisar respuestas tanto en probe_sock como en recv_sock
            for sock in (probe_sock, recv_sock) if bound_ok else (probe_sock,):
                while True:
                    try:
                        data, addr = sock.recvfrom(4096)
                        self._parse_packet(data, addr[0])
                    except (socket.timeout, OSError):
                        break

        recv_sock.close()
        probe_sock.close()

    def _parse_packet(self, data: bytes, sender_ip: str) -> None:
        try:
            text = data.decode("utf-8", errors="ignore").strip()
            if not text.startswith("{"):
                return
            payload = json.loads(text)
            if payload.get("magic") != HERE_MAGIC:
                return

            reported_ip = payload.get("ip") or sender_ip
            if reported_ip in ("0.0.0.0", ""):
                reported_ip = sender_ip

            info = ConsoleInfo(
                model=str(payload.get("model", "Nintendo 3DS")),
                ip=str(reported_ip),
                tcp_port=int(payload.get("tcp_port", TCP_PORT)),
                sd_free_bytes=int(payload.get("sd_free_bytes", 0)),
                sd_total_bytes=int(payload.get("sd_total_bytes", 0)),
                last_seen=time.time(),
            )
            self.console_found.emit(info)
        except Exception:
            pass


class RemoteFsWorker(QThread):
    """
    Ejecuta operaciones rápidas del explorador remoto (LIST, MKDIR, DELETE, PING)
    sin bloquear el hilo principal de la GUI.
    """

    directory_listed = Signal(str, list, object, object)  # path, entries, free_bytes, total_bytes
    operation_success = Signal(str, str)                  # action_name, message
    operation_error = Signal(str, str)                    # action_name, error_msg
    ping_result = Signal(object)                          # ConsoleInfo

    def __init__(
        self,
        ip: str,
        tcp_port: int,
        action: str,
        path: str = "/",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ip = ip
        self.tcp_port = tcp_port
        self.action = action.upper()
        self.path = path

    def run(self) -> None:
        client = ThreeDSClient(self.ip, self.tcp_port, timeout=8.0)
        try:
            if self.action == "LIST":
                entries, free_b, total_b = client.list_dir(self.path)
                self.directory_listed.emit(
                    normalize_remote_path(self.path), entries, free_b, total_b
                )
            elif self.action == "MKDIR":
                client.mkdir(self.path)
                self.operation_success.emit("MKDIR", f"Carpeta creada: {self.path}")
            elif self.action == "DELETE":
                client.delete(self.path)
                self.operation_success.emit("DELETE", f"Eliminado: {self.path}")
            elif self.action == "PING":
                info = client.ping()
                self.ping_result.emit(info)
        except Exception as exc:
            self.operation_error.emit(self.action, str(exc))


class TransferQueueWorker(QThread):
    """
    Worker de cola de transferencias asíncronas de alta velocidad (UPLOAD / DOWNLOAD).
    Soporta archivos individuales y carpetas recursivas completas.
    Calcula velocidad en tiempo real (MB/s), progreso por archivo, progreso total y ETA.
    """

    task_updated = Signal(int, str, object, object, str)       # task_id, status, transferred, total, error
    file_progress = Signal(str, object, object, float, float)  # filename, current_b, total_b, speed_mbs, eta_s
    queue_progress = Signal(int, int, object, object)          # done_files, total_files, done_bytes, total_bytes
    sd_space_updated = Signal(object, object)                  # free_bytes, total_bytes
    queue_finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: "queue.Queue[Optional[TransferItem]]" = queue.Queue()
        self._running = True
        self._cancel_current = False
        self._ip: str = ""
        self._tcp_port: int = TCP_PORT

        self._total_queue_bytes = 0
        self._completed_queue_bytes = 0
        self._total_queue_tasks = 0
        self._completed_queue_tasks = 0

    def set_endpoint(self, ip: str, tcp_port: int = TCP_PORT) -> None:
        self._ip = ip
        self._tcp_port = tcp_port

    def enqueue_items(self, items: List[TransferItem]) -> None:
        """Agrega uno o más elementos a la cola de transferencia."""
        for item in items:
            item.total_bytes = self._calculate_local_size(item) if item.direction == "UPLOAD" else item.total_bytes
            self._total_queue_bytes += item.total_bytes
            self._total_queue_tasks += 1
            self._queue.put(item)

        if not self.isRunning():
            self.start()

    def cancel_current_and_clear(self) -> None:
        """Cancela la transferencia en curso y vacía los pendientes."""
        self._cancel_current = True
        while not self._queue.empty():
            try:
                discarded = self._queue.get_nowait()
                if discarded is not None:
                    self.task_updated.emit(
                        discarded.task_id, "Cancelado", 0, discarded.total_bytes, "Cancelado"
                    )
                self._queue.task_done()
            except queue.Empty:
                break

    def stop(self) -> None:
        self._running = False
        self._cancel_current = True
        self._queue.put(None)

    def _calculate_local_size(self, item: TransferItem) -> int:
        p = item.local_path
        if not p.exists():
            return 0
        if p.is_file():
            return p.stat().st_size
        total = 0
        for child in p.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
        return total

    def run(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=0.3)
            except queue.Empty:
                if self._total_queue_tasks > 0 and self._completed_queue_tasks >= self._total_queue_tasks:
                    self._total_queue_bytes = 0
                    self._completed_queue_bytes = 0
                    self._total_queue_tasks = 0
                    self._completed_queue_tasks = 0
                    self.queue_finished.emit()
                continue

            if item is None:
                break

            self._cancel_current = False
            if not self._ip:
                self.task_updated.emit(
                    item.task_id,
                    "Error",
                    0,
                    item.total_bytes,
                    "No hay consola 3DS conectada.",
                )
                self._completed_queue_tasks += 1
                self._queue.task_done()
                continue

            client = ThreeDSClient(self._ip, self._tcp_port, timeout=30.0)
            try:
                self.task_updated.emit(
                    item.task_id, "Transfiriendo", 0, item.total_bytes, ""
                )
                if item.direction == "UPLOAD":
                    self._execute_upload(client, item)
                else:
                    self._execute_download(client, item)

                self.task_updated.emit(
                    item.task_id,
                    "Completado",
                    item.total_bytes,
                    item.total_bytes,
                    "",
                )
            except Exception as exc:
                status_label = "Cancelado" if self._cancel_current else "Error"
                self.task_updated.emit(
                    item.task_id,
                    status_label,
                    item.transferred_bytes,
                    item.total_bytes,
                    str(exc),
                )
            finally:
                self._completed_queue_tasks += 1
                self.queue_progress.emit(
                    self._completed_queue_tasks,
                    self._total_queue_tasks,
                    self._completed_queue_bytes,
                    max(1, self._total_queue_bytes),
                )
                self._queue.task_done()

    def _execute_upload(self, client: ThreeDSClient, item: TransferItem) -> None:
        """Sube un archivo individual o una carpeta completa recursivamente."""
        local_path = item.local_path
        if local_path.is_file():
            files_to_upload = [(local_path, item.remote_path)]
        elif local_path.is_dir():
            base_remote = normalize_remote_path(item.remote_path)
            client.mkdir(base_remote)
            files_to_upload = []
            for root, dirs, files in os.walk(local_path):
                rel_dir = Path(root).relative_to(local_path).as_posix()
                remote_dir = (
                    base_remote
                    if rel_dir == "."
                    else normalize_remote_path(posixpath.join(base_remote, rel_dir))
                )
                if rel_dir != ".":
                    client.mkdir(remote_dir)
                for fname in sorted(files):
                    f_local = Path(root) / fname
                    if item.smart_route and rel_dir == ".":
                        f_remote = get_smart_destination(f_local, fallback_dir=base_remote)
                    else:
                        f_remote = normalize_remote_path(posixpath.join(remote_dir, fname))
                    files_to_upload.append((f_local, f_remote))
        else:
            raise FileNotFoundError(f"No existe la ruta local: {local_path}")

        item_done_before_file = 0
        for f_local, f_remote in files_to_upload:
            if self._cancel_current:
                raise RuntimeError("Cancelado por el usuario")

            f_size = f_local.stat().st_size
            start_time = time.perf_counter()
            last_emit_time = 0.0

            def on_chunk(sent: int, total: int) -> bool:
                nonlocal last_emit_time
                if self._cancel_current:
                    return False
                now = time.perf_counter()
                # Limitar emisiones a ~20 Hz para fluidez máxima en la GUI
                if sent < total and (now - last_emit_time) < 0.05:
                    return True
                last_emit_time = now

                elapsed = max(0.001, now - start_time)
                speed_mbs = (sent / (1024.0 * 1024.0)) / elapsed
                remaining_bytes = max(0, self._total_queue_bytes - (self._completed_queue_bytes + sent))
                eta_s = (
                    (remaining_bytes / (1024.0 * 1024.0)) / speed_mbs
                    if speed_mbs > 0.01
                    else -1.0
                )

                item.transferred_bytes = item_done_before_file + sent
                self.file_progress.emit(f_local.name, sent, total, speed_mbs, eta_s)
                self.task_updated.emit(
                    item.task_id,
                    "Transfiriendo",
                    item.transferred_bytes,
                    max(1, item.total_bytes),
                    "",
                )
                self.queue_progress.emit(
                    self._completed_queue_tasks,
                    self._total_queue_tasks,
                    self._completed_queue_bytes + sent,
                    max(1, self._total_queue_bytes),
                )
                return True

            resp = client.upload_file(f_local, f_remote, progress_cb=on_chunk)
            item_done_before_file += f_size
            self._completed_queue_bytes += f_size

            if "sd_free_bytes" in resp and "sd_total_bytes" in resp:
                self.sd_space_updated.emit(
                    int(resp["sd_free_bytes"]), int(resp["sd_total_bytes"])
                )

    def _execute_download(self, client: ThreeDSClient, item: TransferItem) -> None:
        """Descarga un archivo o carpeta remota de la 3DS hacia la PC."""
        if not item.is_dir:
            start_time = time.perf_counter()
            last_emit = 0.0

            def on_dl_chunk(recd: int, total: int) -> bool:
                nonlocal last_emit
                if self._cancel_current:
                    return False
                now = time.perf_counter()
                if recd < total and (now - last_emit) < 0.05:
                    return True
                last_emit = now
                elapsed = max(0.001, now - start_time)
                speed_mbs = (recd / (1024.0 * 1024.0)) / elapsed
                remaining = max(0, total - recd)
                eta_s = (remaining / (1024.0 * 1024.0)) / speed_mbs if speed_mbs > 0.01 else -1.0
                item.total_bytes = max(item.total_bytes, total)
                item.transferred_bytes = recd
                self.file_progress.emit(item.local_path.name, recd, total, speed_mbs, eta_s)
                self.task_updated.emit(
                    item.task_id, "Transfiriendo", recd, max(1, total), ""
                )
                return True

            client.download_file(item.remote_path, item.local_path, progress_cb=on_dl_chunk)
            self._completed_queue_bytes += item.transferred_bytes
        else:
            # Descarga recursiva de directorio remoto
            self._download_dir_recursive(client, item.remote_path, item.local_path, item)

    def _download_dir_recursive(
        self,
        client: ThreeDSClient,
        remote_dir: str,
        local_dir: Path,
        item: TransferItem,
    ) -> None:
        local_dir.mkdir(parents=True, exist_ok=True)
        entries, _, _ = client.list_dir(remote_dir)
        for entry in entries:
            if self._cancel_current:
                raise RuntimeError("Cancelado por el usuario")
            target_local = local_dir / entry.name
            if entry.is_dir:
                self._download_dir_recursive(client, entry.path, target_local, item)
            else:
                client.download_file(entry.path, target_local)
                item.transferred_bytes += entry.size
                self._completed_queue_bytes += entry.size
                self.task_updated.emit(
                    item.task_id,
                    "Transfiriendo",
                    item.transferred_bytes,
                    max(item.total_bytes, item.transferred_bytes),
                    "",
                )
