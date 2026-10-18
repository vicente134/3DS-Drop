#!/usr/bin/env python3
"""
3DS-Drop — Transferencia directa de archivos entre PC y familia Nintendo 3DS / 2DS.
Interfaz limpia de doble panel: Tus carpetas de PC a la izquierda y tu consola a la derecha.
"""

from __future__ import annotations

import datetime
import posixpath
import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QDir, Qt, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileSystemModel,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from network_worker import (
    RemoteFsWorker,
    TransferItem,
    TransferQueueWorker,
    UdpDiscoveryWorker,
)
from protocol import (
    ConsoleInfo,
    RemoteEntry,
    TCP_PORT,
    format_eta,
    format_size,
    normalize_remote_path,
)

CLEAN_DARK_STYLE = """
QMainWindow, QDialog {
    background-color: #181825;
    color: #cdd6f4;
    font-family: "Inter", "Segoe UI", "Noto Sans", sans-serif;
    font-size: 13px;
}
QWidget {
    color: #cdd6f4;
    font-size: 13px;
}
QFrame#TopBar, QFrame#PaneCard, QFrame#BottomBar {
    background-color: #1e1e2e;
    border: 1px solid #313244;
    border-radius: 8px;
}
QLabel#PaneTitle {
    font-size: 14px;
    font-weight: 700;
    color: #89b4fa;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
}
QPushButton:disabled {
    background-color: #181825;
    color: #585b70;
    border-color: #313244;
}
QPushButton#ActionPrimary {
    background-color: #89b4fa;
    color: #11111b;
    border: none;
    font-weight: 700;
    padding: 7px 14px;
}
QPushButton#ActionPrimary:hover {
    background-color: #b4befe;
}
QPushButton#ActionDanger {
    background-color: #f38ba8;
    color: #11111b;
    border: none;
    font-weight: 700;
}
QPushButton#ActionDanger:hover {
    background-color: #eba0ac;
}
QLineEdit {
    background-color: #11111b;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 5px 10px;
}
QLineEdit:focus {
    border-color: #89b4fa;
}
QProgressBar {
    background-color: #11111b;
    border: 1px solid #313244;
    border-radius: 6px;
    text-align: center;
    color: #cdd6f4;
    font-weight: 600;
    min-height: 20px;
}
QProgressBar::chunk {
    background-color: #a6e3a1;
    border-radius: 5px;
}
QTableWidget, QTreeView {
    background-color: #11111b;
    alternate-background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    gridline-color: #24273a;
    selection-background-color: #313244;
    selection-color: #89b4fa;
}
QHeaderView::section {
    background-color: #181825;
    color: #a6adc8;
    border: none;
    border-bottom: 1px solid #313244;
    padding: 6px 8px;
    font-weight: 700;
}
"""


class RemoteFileTable(QTableWidget):
    """Lista de carpetas y archivos de la microSD de la 3DS/2DS con soporte Drag & Drop."""

    external_paths_dropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(0, 3, parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setHorizontalHeaderLabels(["Nombre", "Tamaño", "Fecha"])

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        local_paths = [Path(u.toLocalFile()) for u in urls if u.isLocalFile()]
        if local_paths:
            event.acceptProposedAction()
            self.external_paths_dropped.emit(local_paths)


class ManualConnectDialog(QDialog):
    """Diálogo simple para conectar manualmente por IP a la 3DS / 2DS."""

    def __init__(self, current_ip: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Conectar por IP")
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("IP que muestra tu Nintendo 3DS / 2DS en pantalla:"))

        self.ip_edit = QLineEdit(current_ip)
        self.ip_edit.setPlaceholderText("Ej: 192.168.1.45")
        layout.addWidget(self.ip_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_ip(self) -> str:
        return self.ip_edit.text().strip()


class MainWindow(QMainWindow):
    """Ventana principal minimalista de 3DS-Drop (PC ⇄ Familia Nintendo 3DS / 2DS)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("3DS-Drop")
        self.resize(1060, 680)

        icon_path = Path(__file__).resolve().parent / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.console_info: Optional[ConsoleInfo] = None
        self.current_remote_path: str = "/"
        self._active_fs_workers: List[RemoteFsWorker] = []
        self._next_task_id = 1

        self._build_ui()
        self._setup_workers()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ------------------------------------------------------------------
        # 1. BARRA SUPERIOR COMPACTA (Estado Consola + Espacio SD + Conectar IP)
        # ------------------------------------------------------------------
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(12, 8, 12, 8)
        top_layout.setSpacing(12)

        self.status_dot_lbl = QLabel("◌ Buscando Nintendo 3DS / 2DS en la red local...")
        self.status_dot_lbl.setStyleSheet("color: #f9e2af; font-weight: 700; font-size: 13px;")
        top_layout.addWidget(self.status_dot_lbl)

        top_layout.addStretch(1)

        self.sd_free_lbl = QLabel("microSD: —")
        self.sd_free_lbl.setStyleSheet("color: #a6adc8; font-weight: 600;")
        top_layout.addWidget(self.sd_free_lbl)

        self.btn_connect_ip = QPushButton("Conectar por IP")
        self.btn_connect_ip.clicked.connect(self._on_manual_connect)
        top_layout.addWidget(self.btn_connect_ip)

        root.addWidget(top_bar)

        # ------------------------------------------------------------------
        # 2. DOBLE PANEL: TU PC (IZQUIERDA) ⇄ TU NINTENDO 3DS / 2DS (DERECHA)
        # ------------------------------------------------------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- PANEL IZQUIERDO: TU PC ---
        pc_card = QFrame()
        pc_card.setObjectName("PaneCard")
        pc_layout = QVBoxLayout(pc_card)
        pc_layout.setContentsMargins(10, 10, 10, 10)
        pc_layout.setSpacing(8)

        pc_header = QHBoxLayout()
        pc_title = QLabel("💻 Tu PC")
        pc_title.setObjectName("PaneTitle")
        pc_header.addWidget(pc_title)
        pc_header.addStretch(1)

        btn_pc_up = QPushButton("⬆ Subir")
        btn_pc_up.clicked.connect(self._navigate_up_local)
        btn_pc_home = QPushButton("🏠 Inicio")
        btn_pc_home.clicked.connect(lambda: self._set_local_dir(Path.home()))
        btn_pc_browse = QPushButton("📂 Elegir...")
        btn_pc_browse.clicked.connect(self._pick_local_dir)

        pc_header.addWidget(btn_pc_up)
        pc_header.addWidget(btn_pc_home)
        pc_header.addWidget(btn_pc_browse)
        pc_layout.addLayout(pc_header)

        self.local_path_edit = QLineEdit(str(Path.home()))
        self.local_path_edit.returnPressed.connect(
            lambda: self._set_local_dir(Path(self.local_path_edit.text()))
        )
        pc_layout.addWidget(self.local_path_edit)

        self.fs_model = QFileSystemModel(self)
        self.fs_model.setFilter(
            QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs
        )
        self.fs_model.setRootPath(str(Path.home()))

        self.local_tree = QTreeView()
        self.local_tree.setModel(self.fs_model)
        self.local_tree.setRootIndex(self.fs_model.index(str(Path.home())))
        self.local_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.local_tree.setAlternatingRowColors(True)
        self.local_tree.setRootIsDecorated(False)
        self.local_tree.setItemsExpandable(False)
        self.local_tree.setColumnWidth(0, 260)
        self.local_tree.setColumnHidden(2, True)
        self.local_tree.doubleClicked.connect(self._on_local_double_clicked)
        pc_layout.addWidget(self.local_tree, stretch=1)

        self.btn_send_to_3ds = QPushButton("Enviar seleccionado a la 3DS / 2DS  ➔")
        self.btn_send_to_3ds.setObjectName("ActionPrimary")
        self.btn_send_to_3ds.clicked.connect(self._send_selected_to_3ds)
        pc_layout.addWidget(self.btn_send_to_3ds)

        splitter.addWidget(pc_card)

        # --- PANEL DERECHO: NINTENDO 3DS / 2DS (microSD) ---
        ds_card = QFrame()
        ds_card.setObjectName("PaneCard")
        ds_layout = QVBoxLayout(ds_card)
        ds_layout.setContentsMargins(10, 10, 10, 10)
        ds_layout.setSpacing(8)

        ds_header = QHBoxLayout()
        self.ds_title_lbl = QLabel("🎮 Nintendo 3DS / 2DS (microSD)")
        self.ds_title_lbl.setObjectName("PaneTitle")
        ds_header.addWidget(self.ds_title_lbl)
        ds_header.addStretch(1)

        btn_ds_up = QPushButton("⬆ Subir")
        btn_ds_up.clicked.connect(self._navigate_up_remote)
        btn_ds_root = QPushButton("Raíz (/)")
        btn_ds_root.clicked.connect(lambda: self.navigate_remote("/"))
        btn_ds_refresh = QPushButton("🔄")
        btn_ds_refresh.setToolTip("Actualizar carpeta")
        btn_ds_refresh.clicked.connect(lambda: self.navigate_remote(self.current_remote_path))
        btn_ds_mkdir = QPushButton("📁 Nueva Carpeta")
        btn_ds_mkdir.clicked.connect(self._create_remote_folder)

        ds_header.addWidget(btn_ds_up)
        ds_header.addWidget(btn_ds_root)
        ds_header.addWidget(btn_ds_refresh)
        ds_header.addWidget(btn_ds_mkdir)
        ds_layout.addLayout(ds_header)

        self.remote_path_edit = QLineEdit("/")
        self.remote_path_edit.returnPressed.connect(
            lambda: self.navigate_remote(self.remote_path_edit.text())
        )
        ds_layout.addWidget(self.remote_path_edit)

        self.remote_table = RemoteFileTable()
        self.remote_table.itemDoubleClicked.connect(self._on_remote_double_clicked)
        self.remote_table.external_paths_dropped.connect(self.enqueue_uploads)
        ds_layout.addWidget(self.remote_table, stretch=1)

        ds_footer = QHBoxLayout()
        self.btn_download_to_pc = QPushButton("⬅  Guardar en tu PC")
        self.btn_download_to_pc.clicked.connect(self._download_selected_to_pc)

        self.btn_delete_remote = QPushButton("🗑️ Eliminar")
        self.btn_delete_remote.setObjectName("ActionDanger")
        self.btn_delete_remote.clicked.connect(self._delete_selected_remote)

        ds_footer.addWidget(self.btn_download_to_pc, stretch=1)
        ds_footer.addWidget(self.btn_delete_remote)
        ds_layout.addLayout(ds_footer)

        splitter.addWidget(ds_card)
        splitter.setSizes([520, 520])
        root.addWidget(splitter, stretch=1)

        # ------------------------------------------------------------------
        # 3. BARRA INFERIOR COMPACTA DE PROGRESO Y VELOCIDAD
        # ------------------------------------------------------------------
        bottom_bar = QFrame()
        bottom_bar.setObjectName("BottomBar")
        bottom_layout = QHBoxLayout(bottom_bar)
        bottom_layout.setContentsMargins(12, 8, 12, 8)
        bottom_layout.setSpacing(12)

        self.transfer_lbl = QLabel("Listo para transferir (también puedes arrastrar archivos o carpetas)")
        self.transfer_lbl.setMinimumWidth(280)
        bottom_layout.addWidget(self.transfer_lbl)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        bottom_layout.addWidget(self.progress_bar, stretch=1)

        self.speed_eta_lbl = QLabel("0.00 MB/s  •  ETA --:--")
        self.speed_eta_lbl.setStyleSheet("color: #a6e3a1; font-weight: 700;")
        bottom_layout.addWidget(self.speed_eta_lbl)

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setObjectName("ActionDanger")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_transfers)
        bottom_layout.addWidget(self.btn_cancel)

        root.addWidget(bottom_bar)

    def _setup_workers(self) -> None:
        self.transfer_worker = TransferQueueWorker(self)
        self.transfer_worker.file_progress.connect(self._on_file_progress)
        self.transfer_worker.sd_space_updated.connect(self._update_sd_space)
        self.transfer_worker.queue_finished.connect(self._on_queue_finished)
        self.transfer_worker.task_updated.connect(self._on_task_updated)

        self.udp_worker = UdpDiscoveryWorker(parent=self)
        self.udp_worker.console_found.connect(self._on_console_discovered)
        self.udp_worker.start()

    # ------------------------------------------------------------------
    # CONEXIÓN CON LA 3DS / 2DS
    # ------------------------------------------------------------------
    def _on_console_discovered(self, info: ConsoleInfo) -> None:
        first_connect = (
            self.console_info is None
            or self.console_info.ip != info.ip
            or self.console_info.tcp_port != info.tcp_port
        )
        self.console_info = info
        self.transfer_worker.set_endpoint(info.ip, info.tcp_port)

        self.status_dot_lbl.setText(f"● Conectada: {info.model} ({info.ip})")
        self.status_dot_lbl.setStyleSheet("color: #a6e3a1; font-weight: 700; font-size: 13px;")
        self.ds_title_lbl.setText(f"🎮 {info.model} (microSD)")
        self._update_sd_space(info.sd_free_bytes, info.sd_total_bytes)

        if first_connect:
            self.navigate_remote(self.current_remote_path)

    def _update_sd_space(self, free_b: int, total_b: int) -> None:
        if total_b > 0:
            self.sd_free_lbl.setText(
                f"microSD: {format_size(free_b)} libres de {format_size(total_b)}"
            )

    def _on_manual_connect(self) -> None:
        current_ip = self.console_info.ip if self.console_info else ""
        dlg = ManualConnectDialog(current_ip, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ip = dlg.get_ip()
            if ip:
                self._run_fs_worker("PING", "/", ip_override=ip)

    # ------------------------------------------------------------------
    # NAVEGACIÓN EN TU PC (PANEL IZQUIERDO)
    # ------------------------------------------------------------------
    def _current_local_dir(self) -> Path:
        return Path(self.local_path_edit.text().strip() or str(Path.home()))

    def _set_local_dir(self, path: Path) -> None:
        if path.exists() and path.is_dir():
            resolved = str(path.resolve())
            self.local_path_edit.setText(resolved)
            self.local_tree.setRootIndex(self.fs_model.index(resolved))

    def _navigate_up_local(self) -> None:
        curr = self._current_local_dir()
        if curr.parent != curr:
            self._set_local_dir(curr.parent)

    def _pick_local_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Elegir carpeta de tu PC", str(self._current_local_dir())
        )
        if chosen:
            self._set_local_dir(Path(chosen))

    def _on_local_double_clicked(self, index) -> None:
        p = Path(self.fs_model.filePath(index))
        if p.is_dir():
            self._set_local_dir(p)

    # ------------------------------------------------------------------
    # NAVEGACIÓN EN LA NINTENDO 3DS / 2DS (PANEL DERECHO)
    # ------------------------------------------------------------------
    def _run_fs_worker(
        self,
        action: str,
        remote_path: str,
        ip_override: Optional[str] = None,
    ) -> None:
        ip = ip_override or (self.console_info.ip if self.console_info else "")
        port = self.console_info.tcp_port if self.console_info else TCP_PORT
        if not ip:
            return

        worker = RemoteFsWorker(ip, port, action, remote_path, self)
        worker.directory_listed.connect(self._on_directory_listed)
        worker.operation_success.connect(self._on_fs_success)
        worker.operation_error.connect(self._on_fs_error)
        worker.ping_result.connect(self._on_console_discovered)
        worker.finished.connect(
            lambda w=worker: self._active_fs_workers.remove(w)
            if w in self._active_fs_workers
            else None
        )
        self._active_fs_workers.append(worker)
        worker.start()

    def navigate_remote(self, remote_path: str) -> None:
        self._run_fs_worker("LIST", normalize_remote_path(remote_path))

    def _navigate_up_remote(self) -> None:
        if self.current_remote_path == "/":
            return
        parent = posixpath.dirname(self.current_remote_path.rstrip("/"))
        self.navigate_remote(parent or "/")

    def _on_directory_listed(
        self, path: str, entries: List[RemoteEntry], free_b: int, total_b: int
    ) -> None:
        self.current_remote_path = path
        self.remote_path_edit.setText(path)
        self._update_sd_space(free_b, total_b)

        self.remote_table.setRowCount(len(entries))
        style = self.style()
        dir_icon = style.standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.StandardPixmap.SP_FileIcon)

        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setIcon(dir_icon if entry.is_dir else file_icon)
            name_item.setData(Qt.ItemDataRole.UserRole, entry)

            size_str = "Carpeta" if entry.is_dir else format_size(entry.size)
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            mtime_str = (
                datetime.datetime.fromtimestamp(entry.mtime).strftime("%Y-%m-%d %H:%M")
                if entry.mtime > 0
                else "—"
            )
            mtime_item = QTableWidgetItem(mtime_str)

            self.remote_table.setItem(row, 0, name_item)
            self.remote_table.setItem(row, 1, size_item)
            self.remote_table.setItem(row, 2, mtime_item)

    def _on_remote_double_clicked(self, item: QTableWidgetItem) -> None:
        first_item = self.remote_table.item(item.row(), 0)
        if not first_item:
            return
        entry: RemoteEntry = first_item.data(Qt.ItemDataRole.UserRole)
        if entry and entry.is_dir:
            self.navigate_remote(entry.path)

    def _on_fs_success(self, action: str, message: str) -> None:
        self.transfer_lbl.setText(message)
        self.navigate_remote(self.current_remote_path)

    def _on_fs_error(self, action: str, error_msg: str) -> None:
        self.transfer_lbl.setText(f"Error: {error_msg}")
        if action in ("MKDIR", "DELETE", "PING"):
            QMessageBox.warning(self, "Aviso", error_msg)

    def _create_remote_folder(self) -> None:
        if not self.console_info:
            QMessageBox.information(self, "Sin conexión", "Abre 3DS-Drop en tu consola primero.")
            return
        name, ok = QInputDialog.getText(
            self, "Nueva Carpeta", f"Crear carpeta en {self.current_remote_path}:"
        )
        if ok and name.strip():
            target = normalize_remote_path(posixpath.join(self.current_remote_path, name.strip()))
            self._run_fs_worker("MKDIR", target)

    def _delete_selected_remote(self) -> None:
        if not self.console_info:
            return
        rows = sorted({idx.row() for idx in self.remote_table.selectedIndexes()})
        if not rows:
            return

        entries = [
            self.remote_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            for r in rows
            if self.remote_table.item(r, 0)
        ]
        names = ", ".join(e.name for e in entries[:4])
        ans = QMessageBox.question(
            self,
            "Eliminar de la microSD",
            f"¿Eliminar {len(entries)} elemento(s) de la consola?\n({names})",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            for e in entries:
                self._run_fs_worker("DELETE", e.path)

    # ------------------------------------------------------------------
    # ENVIAR Y RECIBIR ARCHIVOS / CARPETAS
    # ------------------------------------------------------------------
    def _send_selected_to_3ds(self) -> None:
        indexes = self.local_tree.selectionModel().selectedRows(0)
        paths = [Path(self.fs_model.filePath(idx)) for idx in indexes]
        if not paths:
            QMessageBox.information(
                self, "Seleccionar", "Selecciona un archivo o carpeta de tu PC a la izquierda."
            )
            return
        self.enqueue_uploads(paths)

    def enqueue_uploads(self, local_paths: List[Path]) -> None:
        if not self.console_info:
            QMessageBox.warning(
                self,
                "Consola no conectada",
                "Asegúrate de tener abierto 3DS-Drop en tu Nintendo 3DS / 2DS en la misma red Wi-Fi.",
            )
            return

        items: List[TransferItem] = []
        for path in local_paths:
            if not path.exists():
                continue
            remote_dest = normalize_remote_path(
                posixpath.join(self.current_remote_path, path.name)
            )
            items.append(
                TransferItem(
                    task_id=self._next_task_id,
                    direction="UPLOAD",
                    local_path=path,
                    remote_path=remote_dest,
                    is_dir=path.is_dir(),
                    smart_route=False,
                )
            )
            self._next_task_id += 1

        if items:
            self.btn_cancel.setVisible(True)
            self.transfer_worker.enqueue_items(items)

    def _download_selected_to_pc(self) -> None:
        if not self.console_info:
            return
        rows = sorted({idx.row() for idx in self.remote_table.selectedIndexes()})
        if not rows:
            QMessageBox.information(
                self, "Seleccionar", "Selecciona un archivo o carpeta de la 3DS / 2DS a la derecha."
            )
            return

        dest_base = self._current_local_dir()
        items: List[TransferItem] = []
        for r in rows:
            it = self.remote_table.item(r, 0)
            if not it:
                continue
            entry: RemoteEntry = it.data(Qt.ItemDataRole.UserRole)
            items.append(
                TransferItem(
                    task_id=self._next_task_id,
                    direction="DOWNLOAD",
                    local_path=dest_base / entry.name,
                    remote_path=entry.path,
                    is_dir=entry.is_dir,
                    total_bytes=entry.size,
                )
            )
            self._next_task_id += 1

        if items:
            self.btn_cancel.setVisible(True)
            self.transfer_worker.enqueue_items(items)

    def _on_file_progress(
        self, filename: str, current_b: int, total_b: int, speed_mbs: float, eta_s: float
    ) -> None:
        pct = int(round((current_b / max(1, total_b)) * 100))
        self.transfer_lbl.setText(f"Transfiriendo: {filename}")
        self.progress_bar.setValue(pct)
        self.speed_eta_lbl.setText(f"{speed_mbs:.2f} MB/s  •  ETA {format_eta(eta_s)}")

    def _on_task_updated(
        self, task_id: int, status: str, transferred: int, total: int, error: str
    ) -> None:
        if status == "Error" and error:
            self.transfer_lbl.setText(f"Error: {error}")
            self.btn_cancel.setVisible(False)

    def _on_queue_finished(self) -> None:
        self.progress_bar.setValue(100)
        self.transfer_lbl.setText("✔ Transferencia completada")
        self.speed_eta_lbl.setText("Listo")
        self.btn_cancel.setVisible(False)
        self.navigate_remote(self.current_remote_path)

    def _cancel_transfers(self) -> None:
        self.transfer_worker.cancel_current_and_clear()
        self.btn_cancel.setVisible(False)
        self.transfer_lbl.setText("Transferencia cancelada")

    def closeEvent(self, event) -> None:
        self.udp_worker.stop()
        self.transfer_worker.stop()
        self.udp_worker.wait(800)
        self.transfer_worker.wait(800)
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("3DS-Drop")
    app.setStyleSheet(CLEAN_DARK_STYLE)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
