# 3DS-Drop Protocol v1 Specification

Protocolo binario/JSON **Little-Endian** de alta velocidad diseñado para comunicación directa sin configuración (Zero-Config) entre **PC (Linux/Windows)** y **Nintendo 3DS / New 2DS XL (ARM11)**.

---

## 1. Constantes de Red y Buffers

| Parámetro | Valor | Descripción |
| :--- | :--- | :--- |
| `UDP_PORT` | `33333` | Puerto de auto-descubrimiento (Broadcast y respuesta directa) |
| `TCP_PORT` | `33334` | Canal de control JSON + transferencia de datos en crudo |
| `DISCOVER_MAGIC` | `"3DSDROP_DISCOVER"` | Payload UDP enviado por la PC para buscar consolas |
| `HERE_MAGIC` | `"3DSDROP_HERE"` | Identificador en el JSON de anuncio/respuesta de la 3DS |
| `PC_CHUNK_SIZE` | `65536` (64 KB) | Tamaño de bloque de socket enviado por la PC |
| `3DS_SD_BUF_SIZE` | `131072` (128 KB) | Buffer acumulado en RAM antes de volcar a SDIO en la 3DS |
| `3DS_SOC_BUF_SIZE` | `1048576` (1 MB) | Buffer alineado a `0x1000` para `socInit` en la 3DS |

---

## 2. Auto-Descubrimiento por UDP (Puerto `33333`)

- La **3DS** escucha en `0.0.0.0:33333` (UDP) y además emite un broadcast cada **2 segundos** a `255.255.255.255:33333`.
- La **PC** escucha anuncios en el puerto `33333` y también puede emitir `"3DSDROP_DISCOVER"` por broadcast para forzar una respuesta inmediata.
- **Formato de respuesta JSON (UTF-8):**
```json
{
  "magic": "3DSDROP_HERE",
  "model": "New 2DS XL",
  "ip": "192.168.1.45",
  "tcp_port": 33334,
  "sd_free_bytes": 18454937600,
  "sd_total_bytes": 31914983424
}
```

---

## 3. Canal de Control y Datos Pesados por TCP (Puerto `33334`)

### Encabezado de Trama (Framing Little-Endian)
Todo mensaje JSON (tanto comando de PC como respuesta de 3DS) va precedido por un entero sin signo de 32 bits en **Little-Endian** (`uint32_t` en C / `struct.pack("<I", length)` en Python):

```text
+-------------------------------+-----------------------------------+
| uint32_t json_length (4 B LE) | JSON Payload (UTF-8, N bytes)     |
+-------------------------------+-----------------------------------+
```

### Comandos Soportados

#### `PING`
Verifica estado y espacio libre actualizado de la tarjeta microSD.
- **PC -> 3DS:** `{"cmd": "PING"}`
- **3DS -> PC:** `{"status": "OK", "model": "New 2DS XL", "sd_free_bytes": 18454937600, "sd_total_bytes": 31914983424}`

#### `LIST`
Lista los archivos y subdirectorios de una ruta de la microSD.
- **PC -> 3DS:** `{"cmd": "LIST", "path": "/"}`
- **3DS -> PC:**
```json
{
  "status": "OK",
  "path": "/",
  "entries": [
    {"name": "3ds", "is_dir": true, "size": 0, "mtime": 1729260000},
    {"name": "cias", "is_dir": true, "size": 0, "mtime": 1729260000},
    {"name": "boot.firm", "is_dir": false, "size": 245760, "mtime": 1729260000}
  ],
  "sd_free_bytes": 18454937600,
  "sd_total_bytes": 31914983424
}
```

#### `MKDIR`
Crea un directorio (y sus padres recursivamente si no existen).
- **PC -> 3DS:** `{"cmd": "MKDIR", "path": "/music/ost"}`
- **3DS -> PC:** `{"status": "OK", "path": "/music/ost"}`

#### `DELETE`
Elimina un archivo o directorio de la microSD.
- **PC -> 3DS:** `{"cmd": "DELETE", "path": "/cias/juego.cia"}`
- **3DS -> PC:** `{"status": "OK", "path": "/cias/juego.cia"}`

#### `UPLOAD` (Envío de Archivos Pesados PC -> 3DS)
1. **PC -> 3DS:** `{"cmd": "UPLOAD", "path": "/cias/juego.cia", "size": 524288000}`
2. **3DS -> PC:** Crea los directorios padre si hacen falta, abre el archivo para escritura, reserva su buffer de **128 KB** y responde:
   `{"status": "READY"}`
3. **PC -> 3DS:** Transmite exactamente `size` bytes en binario crudo en bloques de **64 KB**.
4. **3DS:** Acumula en su buffer RAM de **128 KB** antes de volcar a la microSD (`FSFILE_Write` / `fwrite`), evitando saturar el bus SDIO.
5. **3DS -> PC:** Al recibir los `size` bytes, ejecuta `FSFILE_Flush`, cierra el archivo y responde:
   `{"status": "OK", "bytes_written": 524288000, "sd_free_bytes": 17930649600}`

#### `DOWNLOAD` (Descarga de Archivos 3DS -> PC)
1. **PC -> 3DS:** `{"cmd": "DOWNLOAD", "path": "/3ds/Checkpoint/saves/save.bin", "size": 0}`
2. **3DS -> PC:** Verifica existencia y tamaño del archivo y responde:
   `{"status": "READY", "size": 524288}`
3. **3DS -> PC:** Transmite exactamente `size` bytes en binario crudo.
4. **3DS -> PC:** Al finalizar el stream binario, envía confirmación final:
   `{"status": "OK", "bytes_sent": 524288}`

---

## 4. Enrutado Inteligente (Quick Drop)

| Extensiones | Carpeta Destino en microSD |
| :--- | :--- |
| `.cia` | `/cias/` |
| `.3dsx`, `.smdh` | `/3ds/` |
| `.mp3`, `.ogg`, `.wav`, `.flac`, `.m4a` | `/music/` |
| `.nds`, `.gba`, `.snes`, `.sfc`, `.nes`, `.gb`, `.gbc` | `/roms/` |
| `.firm`, `.bak`, `.bin` (backups) | `/luma/payloads/` o ruta actual |
