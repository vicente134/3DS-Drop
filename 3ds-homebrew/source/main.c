/**
 * 3DS-Drop — Servidor Gráfico de Alta Velocidad (PC ⇄ Familia Nintendo 3DS y 2DS)
 * Creado por ElVichoPo
 *
 * Incluye:
 *  - Interfaz Gráfica 2D/3D Moderna (citro2d + citro3d) en ambas pantallas
 *  - Animación visual en tiempo real de paquetes viajando entre PC y 3DS/2DS
 *  - Música bonita y relajante durante las transferencias (NDSP + fallback CSND)
 *  - Efectos de sonido al iniciar, conectar y completar transferencias
 *  - Compatible con Old 3DS, Old 3DS XL, Old 2DS, New 3DS, New 3DS XL y New 2DS XL
 */

#include <3ds.h>
#include <citro2d.h>
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <malloc.h>
#include <math.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

/* Dimensiones de pantallas de 3DS / 2DS */
#define TOP_WIDTH          400.0f
#define TOP_HEIGHT         240.0f
#define BOT_WIDTH          320.0f
#define BOT_HEIGHT         240.0f

/* Red y Buffers de Alta Velocidad */
#define UDP_PORT           33333
#define TCP_PORT           33334
#define SOC_ALIGN          0x1000
#define SOC_BUFFERSIZE     0x100000          /* 1 MB buffer para soc:U */
#define SD_RAM_BUF_SIZE    (128 * 1024)      /* 128 KB buffer en RAM para SDIO */
#define NET_CHUNK_SIZE     (64 * 1024)       /* 64 KB bloque de recepción TCP */
#define MAX_JSON_LEN       (256 * 1024)
#define MAX_HISTORY_ITEMS  5

/* Paleta de Colores Estilo Dashboard Moderno (RGBA) */
#define COL_BG_DARK        C2D_Color32(13,  18,  30,  255)
#define COL_BG_HEADER      C2D_Color32(20,  28,  46,  255)
#define COL_CARD_BG        C2D_Color32(23,  34,  56,  255)
#define COL_CARD_ALT       C2D_Color32(30,  44,  70,  255)
#define COL_CARD_BORDER    C2D_Color32(48,  70,  108, 255)
#define COL_TEXT_MAIN      C2D_Color32(240, 246, 255, 255)
#define COL_TEXT_MUTED     C2D_Color32(150, 170, 202, 255)
#define COL_ACCENT_CYAN    C2D_Color32(0,   214, 245, 255)
#define COL_CYAN_SHADOW    C2D_Color32(0,   88,  118, 255)
#define COL_GREEN_OK       C2D_Color32(32,  224, 136, 255)
#define COL_GREEN_DARK     C2D_Color32(14,  92,  56,  255)
#define COL_YELLOW_WARN    C2D_Color32(255, 198, 54,  255)
#define COL_PURPLE_ACCENT  C2D_Color32(186, 135, 255, 255)
#define COL_RED_CRIT       C2D_Color32(255, 86,  102, 255)

/* Coordenadas de Botones Táctiles en Pantalla Inferior (320x240) */
#define BTN_MUSIC_X        8.0f
#define BTN_MUSIC_Y        194.0f
#define BTN_MUSIC_W        148.0f
#define BTN_MUSIC_H        38.0f

#define BTN_LIGHT_X        164.0f
#define BTN_LIGHT_Y        194.0f
#define BTN_LIGHT_W        148.0f
#define BTN_LIGHT_H        38.0f

/*---------------------------------------------------------------------------
 * Subsistema de Audio: Música Bonita de Transferencia + Efectos Agradables
 *---------------------------------------------------------------------------*/
#define AUDIO_SAMPLERATE   22050
#define SFX_SAMPLES        8820              /* 0.40s por efecto */
#define BGM_DURATION_SEC   8                 /* Bucle armónico de 8 segundos */
#define BGM_SAMPLES        (AUDIO_SAMPLERATE * BGM_DURATION_SEC)

typedef enum {
    SFX_STARTUP = 0,
    SFX_TRANSFER_START = 1,
    SFX_TRANSFER_DONE = 2,
    SFX_TAP = 3,
    SFX_COUNT = 4
} AppSfx;

typedef struct {
    bool ndspActive;
    bool csndActive;
    bool musicAllowed;      /* Controlado por el usuario con [Y] o pantalla táctil */
    bool bgmPlaying;        /* Estado actual del canal BGM */
    s16 *sfxBuf[SFX_COUNT];
    ndspWaveBuf sfxWave[SFX_COUNT];
    s16 *bgmBuf;
    ndspWaveBuf bgmWave;
} AudioEngine;

static AudioEngine g_audio;

/**
 * Sintetiza una música bonita, cristalina y relajante para acompañar las transferencias
 * (Progresión Dmaj7 -> Bm7 -> Gmaj7 -> A6 estilo Nintendo eShop / DSi Download).
 */
static void synthesizeTransferBgm(s16 *buf, u32 numSamples, bool stereo) {
    const float melodyNotes[16] = {
        587.33f, 739.99f, 880.00f, 1108.73f, /* Dmaj7: D5, F#5, A5, C#6 */
        493.88f, 587.33f, 739.99f, 987.77f,  /* Bm7  : B4, D5,  F#5, B5 */
        392.00f, 493.88f, 587.33f, 739.99f,  /* Gmaj7: G4, B4,  D5,  F#5 */
        440.00f, 554.37f, 659.25f, 880.00f   /* A6   : A4, C#5, E5,  A5 */
    };
    const float padRoots[4] = {
        146.83f, /* D3 */
        123.47f, /* B2 */
        196.00f, /* G3 */
        220.00f  /* A3 */
    };

    for (u32 i = 0; i < numSamples; i++) {
        float t = (float)i / (float)AUDIO_SAMPLERATE;
        int step = ((int)(t * 2.0f)) % 16;
        int chord = (step / 4) % 4;
        float noteTime = fmodf(t, 0.5f);

        float melFreq = melodyNotes[step];
        float padFreq = padRoots[chord];

        /* Campanita cristalina con armónico de octava */
        float bellEnv = expf(-noteTime * 4.8f) * (noteTime < 0.015f ? (noteTime / 0.015f) : 1.0f);
        float bellSig = 0.72f * sinf(2.0f * (float)M_PI * melFreq * t) +
                        0.28f * sinf(2.0f * (float)M_PI * (melFreq * 2.0f) * t);

        /* Pad cálido respirando debajo */
        float chordTime = fmodf(t, 2.0f);
        float padEnv = sinf((chordTime / 2.0f) * (float)M_PI);
        float padSig = 0.55f * sinf(2.0f * (float)M_PI * padFreq * t) +
                       0.45f * sinf(2.0f * (float)M_PI * (padFreq * 1.5f) * t);

        float mix = (0.58f * bellEnv * bellSig) + (0.34f * padEnv * padSig);
        s16 sample = (s16)(mix * 6000.0f);

        if (stereo) {
            buf[i * 2]     = sample;
            buf[i * 2 + 1] = sample;
        } else {
            buf[i] = sample;
        }
    }
}

static void synthesizeSfxBuffer(s16 *buf, u32 numSamples, AppSfx type, bool stereo) {
    for (u32 i = 0; i < numSamples; i++) {
        float t = (float)i / (float)AUDIO_SAMPLERATE;
        float progress = (float)i / (float)numSamples;
        float env = 1.0f;
        if (progress < 0.05f) env = progress / 0.05f;
        else if (progress > 0.65f) env = (1.0f - progress) / 0.35f;

        float freq = 523.25f;
        if (type == SFX_STARTUP) {
            freq = (progress < 0.25f) ? 587.33f :
                   (progress < 0.50f) ? 739.99f :
                   (progress < 0.75f) ? 880.00f : 1174.66f;
        } else if (type == SFX_TRANSFER_START) {
            /* Sonido ascendente suave de inicio de transferencia */
            freq = 440.0f + 520.0f * (progress * progress);
        } else if (type == SFX_TRANSFER_DONE) {
            /* Acorde alegre de transferencia completada */
            freq = (progress < 0.35f) ? 659.25f :
                   (progress < 0.68f) ? 880.00f : 1318.51f;
        } else {
            /* Toque de botón suave */
            freq = 520.0f + 360.0f * progress;
            env *= expf(-progress * 4.0f);
        }

        float phase = 2.0f * (float)M_PI * freq * t;
        float wave = 0.78f * sinf(phase) + 0.22f * sinf(phase * 2.0f);
        s16 sample = (s16)(env * 14500.0f * wave);

        if (stereo) {
            buf[i * 2]     = sample;
            buf[i * 2 + 1] = sample;
        } else {
            buf[i] = sample;
        }
    }
}

static void audioSetTransferMusic(bool playNow) {
    bool shouldPlay = playNow && g_audio.musicAllowed;
    if (shouldPlay == g_audio.bgmPlaying) return;
    g_audio.bgmPlaying = shouldPlay;

    if (g_audio.ndspActive && g_audio.bgmBuf) {
        if (shouldPlay) {
            ndspChnWaveBufClear(1);
            ndspChnWaveBufAdd(1, &g_audio.bgmWave);
        } else {
            ndspChnWaveBufClear(1);
        }
    } else if (g_audio.csndActive && g_audio.bgmBuf) {
        if (shouldPlay) {
            csndPlaySound(9, SOUND_FORMAT_16BIT | SOUND_REPEAT, AUDIO_SAMPLERATE,
                          0.60f, 0.0f, g_audio.bgmBuf, g_audio.bgmBuf,
                          BGM_SAMPLES * sizeof(s16));
        } else {
            CSND_SetPlayState(9, 0);
            csndExecCmds(false);
        }
    }
}

static void audioPlaySfx(AppSfx sfx) {
    if (sfx >= SFX_COUNT || !g_audio.sfxBuf[sfx]) return;
    if (g_audio.ndspActive) {
        ndspChnWaveBufClear(0);
        ndspChnWaveBufAdd(0, &g_audio.sfxWave[sfx]);
    } else if (g_audio.csndActive) {
        csndPlaySound(8, SOUND_FORMAT_16BIT | SOUND_ONE_SHOT, AUDIO_SAMPLERATE,
                      0.90f, 0.0f, g_audio.sfxBuf[sfx], g_audio.sfxBuf[sfx],
                      SFX_SAMPLES * sizeof(s16));
    }
}

static void audioInit(void) {
    memset(&g_audio, 0, sizeof(g_audio));
    g_audio.musicAllowed = true;
    g_audio.bgmPlaying   = false;

    if (R_SUCCEEDED(ndspInit())) {
        g_audio.ndspActive = true;
        ndspSetOutputMode(NDSP_OUTPUT_STEREO);

        float mix[12];
        memset(mix, 0, sizeof(mix));
        mix[0] = 1.0f;
        mix[1] = 1.0f;

        ndspChnSetInterp(0, NDSP_INTERP_LINEAR);
        ndspChnSetRate(0, AUDIO_SAMPLERATE);
        ndspChnSetFormat(0, NDSP_FORMAT_STEREO_PCM16);
        ndspChnSetMix(0, mix);

        ndspChnSetInterp(1, NDSP_INTERP_LINEAR);
        ndspChnSetRate(1, AUDIO_SAMPLERATE);
        ndspChnSetFormat(1, NDSP_FORMAT_STEREO_PCM16);
        ndspChnSetMix(1, mix);

        for (int s = 0; s < SFX_COUNT; s++) {
            u32 byteSize = SFX_SAMPLES * 2 * sizeof(s16);
            g_audio.sfxBuf[s] = (s16 *)linearAlloc(byteSize);
            if (g_audio.sfxBuf[s]) {
                synthesizeSfxBuffer(g_audio.sfxBuf[s], SFX_SAMPLES, (AppSfx)s, true);
                DSP_FlushDataCache(g_audio.sfxBuf[s], byteSize);
                g_audio.sfxWave[s].data_vaddr = g_audio.sfxBuf[s];
                g_audio.sfxWave[s].nsamples   = SFX_SAMPLES;
                g_audio.sfxWave[s].looping    = false;
            }
        }

        u32 bgmBytes = BGM_SAMPLES * 2 * sizeof(s16);
        g_audio.bgmBuf = (s16 *)linearAlloc(bgmBytes);
        if (g_audio.bgmBuf) {
            synthesizeTransferBgm(g_audio.bgmBuf, BGM_SAMPLES, true);
            DSP_FlushDataCache(g_audio.bgmBuf, bgmBytes);
            g_audio.bgmWave.data_vaddr = g_audio.bgmBuf;
            g_audio.bgmWave.nsamples   = BGM_SAMPLES;
            g_audio.bgmWave.looping    = true;
        }
    } else if (R_SUCCEEDED(csndInit())) {
        g_audio.csndActive = true;
        for (int s = 0; s < SFX_COUNT; s++) {
            u32 byteSize = SFX_SAMPLES * sizeof(s16);
            g_audio.sfxBuf[s] = (s16 *)linearAlloc(byteSize);
            if (g_audio.sfxBuf[s]) {
                synthesizeSfxBuffer(g_audio.sfxBuf[s], SFX_SAMPLES, (AppSfx)s, false);
                GSPGPU_FlushDataCache(g_audio.sfxBuf[s], byteSize);
            }
        }
        u32 bgmBytes = BGM_SAMPLES * sizeof(s16);
        g_audio.bgmBuf = (s16 *)linearAlloc(bgmBytes);
        if (g_audio.bgmBuf) {
            synthesizeTransferBgm(g_audio.bgmBuf, BGM_SAMPLES, false);
            GSPGPU_FlushDataCache(g_audio.bgmBuf, bgmBytes);
        }
    }
}

static void audioExit(void) {
    audioSetTransferMusic(false);
    for (int s = 0; s < SFX_COUNT; s++) {
        if (g_audio.sfxBuf[s]) {
            linearFree(g_audio.sfxBuf[s]);
            g_audio.sfxBuf[s] = NULL;
        }
    }
    if (g_audio.bgmBuf) {
        linearFree(g_audio.bgmBuf);
        g_audio.bgmBuf = NULL;
    }
    if (g_audio.ndspActive) ndspExit();
    else if (g_audio.csndActive) csndExit();
}

/*---------------------------------------------------------------------------
 * Estado Global de Consola, Red e Interfaz Gráfica
 *---------------------------------------------------------------------------*/
typedef struct {
    char name[40];
    double sizeMb;
    bool isUpload;
} TransferRecord;

static u32 *g_soc_buffer = NULL;
static char *g_sd_ram_buf = NULL;
static uint8_t *g_net_chunk_buf = NULL;

static C3D_RenderTarget *g_top_target = NULL;
static C3D_RenderTarget *g_bot_target = NULL;
static C2D_TextBuf g_text_buf;

static char g_model_name[32] = "Nintendo 3DS";
static bool g_is_new_3ds = false;
static bool g_backlight_on = true;
static bool g_transferring = false;
static char g_ip_str[32] = "0.0.0.0";
static char g_status_line[64] = "Esperando conexion de tu PC...";
static char g_current_file[64] = "Ningun archivo en curso";
static int  g_current_pct = 0;
static float g_current_speed_mbs = 0.0f;
static u32  g_anim_tick = 0;

static TransferRecord g_history[MAX_HISTORY_ITEMS];
static int g_history_count = 0;
static u32 g_total_files_transferred = 0;
static double g_total_mb_transferred = 0.0;

static void add_history_item(const char *filename, double size_mb, bool is_upload) {
    if (g_history_count < MAX_HISTORY_ITEMS) {
        g_history_count++;
    } else {
        for (int i = 0; i < MAX_HISTORY_ITEMS - 1; i++) {
            g_history[i] = g_history[i + 1];
        }
    }
    int idx = g_history_count - 1;
    snprintf(g_history[idx].name, sizeof(g_history[idx].name), "%s", filename);
    g_history[idx].sizeMb = size_mb;
    g_history[idx].isUpload = is_upload;

    g_total_files_transferred++;
    g_total_mb_transferred += size_mb;
}

/*---------------------------------------------------------------------------
 * Detección del modelo exacto de la familia 2DS / 3DS y activación de 804 MHz
 *---------------------------------------------------------------------------*/
static void init_console_hardware(void) {
    APT_CheckNew3DS(&g_is_new_3ds);
    if (g_is_new_3ds) {
        osSetSpeedupEnable(true);
    }

    u8 model_id = 0;
    if (R_SUCCEEDED(cfguInit())) {
        if (R_SUCCEEDED(CFGU_GetSystemModel(&model_id))) {
            switch (model_id) {
                case 0: snprintf(g_model_name, sizeof(g_model_name), "Nintendo 3DS"); break;
                case 1: snprintf(g_model_name, sizeof(g_model_name), "Nintendo 3DS XL"); break;
                case 2: snprintf(g_model_name, sizeof(g_model_name), "New Nintendo 3DS"); break;
                case 3: snprintf(g_model_name, sizeof(g_model_name), "Nintendo 2DS"); break;
                case 4: snprintf(g_model_name, sizeof(g_model_name), "New Nintendo 3DS XL"); break;
                case 5: snprintf(g_model_name, sizeof(g_model_name), "New 2DS XL"); break;
                default: snprintf(g_model_name, sizeof(g_model_name), "Familia 3DS/2DS"); break;
            }
        }
        cfguExit();
    }
}

static void get_sd_space(uint64_t *free_bytes, uint64_t *total_bytes) {
    FS_ArchiveResource res;
    if (R_SUCCEEDED(FSUSER_GetArchiveResource(&res, SYSTEM_MEDIATYPE_SD))) {
        *free_bytes  = (uint64_t)res.freeClusters * (uint64_t)res.clusterSize;
        *total_bytes = (uint64_t)res.totalClusters * (uint64_t)res.clusterSize;
    } else {
        *free_bytes  = 0;
        *total_bytes = 0;
    }
}

static void update_local_ip(void) {
    u32 ip = gethostid();
    struct in_addr addr;
    addr.s_addr = ip;
    const char *s = inet_ntoa(addr);
    if (s) {
        snprintf(g_ip_str, sizeof(g_ip_str), "%s", s);
    }
}

/*---------------------------------------------------------------------------
 * Primitivas de Dibujo 2D/3D (citro2d)
 *---------------------------------------------------------------------------*/
static void drawCard(float x, float y, float w, float h, u32 bgCol, u32 borderCol) {
    C2D_DrawRectSolid(x, y, 0.0f, w, h, borderCol);
    C2D_DrawRectSolid(x + 1.5f, y + 1.5f, 0.0f, w - 3.0f, h - 3.0f, bgCol);
}

static void drawTextFmt(float x, float y, float scale, u32 color, u32 alignFlags, const char *fmt, ...) {
    char str[256];
    va_list args;
    va_start(args, fmt);
    vsnprintf(str, sizeof(str), fmt, args);
    va_end(args);

    C2D_Text text;
    C2D_TextParse(&text, g_text_buf, str);
    C2D_TextOptimize(&text);
    C2D_DrawText(&text, C2D_WithColor | alignFlags, x, y, 0.0f, scale, scale, color);
}

static void draw3DLogoText(float x, float y, float scale, u32 frontCol, u32 depthCol, const char *str) {
    C2D_Text text;
    C2D_TextParse(&text, g_text_buf, str);
    C2D_TextOptimize(&text);

    C2D_DrawText(&text, C2D_WithColor | C2D_AlignLeft, x + 3.0f, y + 3.0f, 0.0f, scale, scale, C2D_Color32(5, 10, 20, 255));
    C2D_DrawText(&text, C2D_WithColor | C2D_AlignLeft, x + 1.8f, y + 1.8f, 0.0f, scale, scale, depthCol);
    C2D_DrawText(&text, C2D_WithColor | C2D_AlignLeft, x,        y,        0.0f, scale, scale, frontCol);
}

/**
 * Dibuja un ícono gráfico de PC (Monitor + Base) y un ícono de 3DS/2DS con paquetes viajando entre ellos.
 */
static void drawTransferBridge(float x, float y, float w, bool active, u32 tick) {
    /* Ícono PC (Izquierda) */
    float pcX = x + 10.0f;
    float pcY = y + 4.0f;
    C2D_DrawRectSolid(pcX, pcY, 0.0f, 34.0f, 22.0f, COL_ACCENT_CYAN);
    C2D_DrawRectSolid(pcX + 2.0f, pcY + 2.0f, 0.0f, 30.0f, 18.0f, COL_BG_DARK);
    C2D_DrawRectSolid(pcX + 12.0f, pcY + 22.0f, 0.0f, 10.0f, 4.0f, COL_TEXT_MUTED);
    C2D_DrawRectSolid(pcX + 6.0f, pcY + 26.0f, 0.0f, 22.0f, 3.0f, COL_TEXT_MAIN);
    drawTextFmt(pcX + 17.0f, pcY + 4.0f, 0.38f, COL_ACCENT_CYAN, C2D_AlignCenter, "PC");

    /* Ícono Consola 3DS / 2DS (Derecha) */
    float dsX = x + w - 44.0f;
    float dsY = y + 2.0f;
    C2D_DrawRectSolid(dsX, dsY, 0.0f, 34.0f, 14.0f, COL_GREEN_OK);
    C2D_DrawRectSolid(dsX + 3.0f, dsY + 2.0f, 0.0f, 28.0f, 10.0f, COL_BG_DARK);
    C2D_DrawRectSolid(dsX + 2.0f, dsY + 16.0f, 0.0f, 30.0f, 14.0f, COL_GREEN_OK);
    C2D_DrawRectSolid(dsX + 5.0f, dsY + 18.0f, 0.0f, 24.0f, 10.0f, COL_BG_DARK);
    drawTextFmt(dsX + 17.0f, dsY + 1.0f, 0.34f, COL_GREEN_OK, C2D_AlignCenter, "3DS");

    /* Puente de datos en el centro */
    float bridgeStart = pcX + 44.0f;
    float bridgeEnd   = dsX - 10.0f;
    float bridgeLen   = bridgeEnd - bridgeStart;
    float midY        = y + 16.0f;

    C2D_DrawRectSolid(bridgeStart, midY - 1.0f, 0.0f, bridgeLen, 2.0f, COL_CARD_BORDER);

    for (int i = 0; i < 4; i++) {
        float speed = active ? 0.045f : 0.012f;
        float phase = fmodf(((float)tick * speed) + (float)i * 0.25f, 1.0f);
        float px = bridgeStart + phase * (bridgeLen - 10.0f);
        u32 pktCol = active ? ((i % 2 == 0) ? COL_GREEN_OK : COL_ACCENT_CYAN) : COL_TEXT_MUTED;
        float pktH = active ? 7.0f : 4.0f;
        C2D_DrawRectSolid(px, midY - pktH * 0.5f, 0.0f, 10.0f, pktH, pktCol);
    }
}

static void render_ui_frame(void) {
    g_anim_tick++;
    uint64_t free_b = 0, total_b = 0;
    get_sd_space(&free_b, &total_b);
    double free_gb  = (double)free_b / (1024.0 * 1024.0 * 1024.0);
    double total_gb = (double)total_b / (1024.0 * 1024.0 * 1024.0);
    double used_gb  = (total_gb > free_gb) ? (total_gb - free_gb) : 0.0;
    float used_ratio = (total_b > 0) ? (float)((double)(total_b - free_b) / (double)total_b) : 0.0f;
    if (used_ratio < 0.0f) used_ratio = 0.0f;
    if (used_ratio > 1.0f) used_ratio = 1.0f;

    C2D_TextBufClear(g_text_buf);
    C3D_FrameBegin(C3D_FRAME_SYNCDRAW);

    /*=======================================================================
     * PANTALLA SUPERIOR (400x240) — DASHBOARD PRINCIPAL
     *=======================================================================*/
    C2D_TargetClear(g_top_target, COL_BG_DARK);
    C2D_SceneBegin(g_top_target);

    /* Header Superior */
    C2D_DrawRectSolid(0.0f, 0.0f, 0.0f, TOP_WIDTH, 28.0f, COL_BG_HEADER);
    C2D_DrawRectSolid(0.0f, 27.0f, 0.0f, TOP_WIDTH, 1.5f, COL_ACCENT_CYAN);
    draw3DLogoText(12.0f, 3.0f, 0.62f, COL_ACCENT_CYAN, COL_CYAN_SHADOW, "3DS-Drop");

    drawCard(134.0f, 4.0f, 112.0f, 20.0f, COL_CARD_ALT, COL_CARD_BORDER);
    drawTextFmt(190.0f, 6.0f, 0.40f, COL_YELLOW_WARN, C2D_AlignCenter, "By: ElVichoPo");

    drawTextFmt(392.0f, 6.0f, 0.42f, g_is_new_3ds ? COL_GREEN_OK : COL_ACCENT_CYAN,
                C2D_AlignRight, "%s", g_is_new_3ds ? "804 MHz TURBO" : "268 MHz STD");

    /* Tarjeta Superior Izquierda: Consola y Dirección IP Wi-Fi */
    drawCard(8.0f, 34.0f, 214.0f, 82.0f, COL_CARD_BG, COL_CARD_BORDER);
    drawTextFmt(16.0f, 39.0f, 0.42f, COL_ACCENT_CYAN, C2D_AlignLeft, "CONSOLA Y RED LOCAL");
    drawTextFmt(16.0f, 56.0f, 0.50f, COL_TEXT_MAIN, C2D_AlignLeft, "%s", g_model_name);
    drawTextFmt(16.0f, 75.0f, 0.56f, COL_GREEN_OK, C2D_AlignLeft, "IP: %s", g_ip_str);
    drawTextFmt(16.0f, 96.0f, 0.39f, COL_TEXT_MUTED, C2D_AlignLeft, "UDP 33333 / TCP 33334 (128KB SD)");

    /* Tarjeta Superior Derecha: Medidor Gráfico de microSD */
    drawCard(228.0f, 34.0f, 164.0f, 82.0f, COL_CARD_BG, COL_CARD_BORDER);
    drawTextFmt(236.0f, 39.0f, 0.42f, COL_PURPLE_ACCENT, C2D_AlignLeft, "TARJETA MICROSD");
    drawTextFmt(236.0f, 56.0f, 0.52f, COL_TEXT_MAIN, C2D_AlignLeft, "%.2f GB libres", free_gb);
    drawTextFmt(236.0f, 75.0f, 0.40f, COL_TEXT_MUTED, C2D_AlignLeft, "Usado: %.1f / %.1f GB", used_gb, total_gb);

    /* Barra de capacidad microSD */
    float sdBarX = 236.0f, sdBarY = 94.0f, sdBarW = 148.0f, sdBarH = 13.0f;
    C2D_DrawRectSolid(sdBarX, sdBarY, 0.0f, sdBarW, sdBarH, COL_BG_DARK);
    u32 sdCol = (used_ratio > 0.90f) ? COL_RED_CRIT : ((used_ratio > 0.75f) ? COL_YELLOW_WARN : COL_PURPLE_ACCENT);
    C2D_DrawRectSolid(sdBarX + 2.0f, sdBarY + 2.0f, 0.0f, (sdBarW - 4.0f) * used_ratio, sdBarH - 4.0f, sdCol);

    /* Tarjeta Inferior Grande: Centro de Transferencia en Vivo */
    u32 panelBorder = g_transferring ? COL_GREEN_OK : COL_CARD_BORDER;
    drawCard(8.0f, 122.0f, 384.0f, 110.0f, COL_CARD_BG, panelBorder);

    drawTransferBridge(18.0f, 128.0f, 364.0f, g_transferring, g_anim_tick);

    drawTextFmt(18.0f, 162.0f, 0.44f, g_transferring ? COL_GREEN_OK : COL_ACCENT_CYAN,
                C2D_AlignLeft, "%s", g_status_line);

    if (g_audio.bgmPlaying) {
        drawTextFmt(382.0f, 162.0f, 0.42f, COL_YELLOW_WARN, C2D_AlignRight, "♪ Musica Activa");
    }

    drawTextFmt(18.0f, 180.0f, 0.46f, COL_TEXT_MAIN, C2D_AlignLeft, "Archivo: %s", g_current_file);

    /* Barra de Progreso de Transferencia */
    float pBarX = 18.0f, pBarY = 201.0f, pBarW = 258.0f, pBarH = 22.0f;
    C2D_DrawRectSolid(pBarX, pBarY, 0.0f, pBarW, pBarH, COL_BG_DARK);
    float fillW = (pBarW - 4.0f) * ((float)(g_current_pct > 100 ? 100 : g_current_pct) / 100.0f);
    if (fillW > 0.0f) {
        C2D_DrawRectSolid(pBarX + 2.0f, pBarY + 2.0f, 0.0f, fillW, pBarH - 4.0f, COL_GREEN_OK);
    }
    drawTextFmt(pBarX + pBarW * 0.5f, pBarY + 3.0f, 0.46f, COL_TEXT_MAIN, C2D_AlignCenter,
                "%d%%", g_current_pct);

    /* Indicador de velocidad en vivo */
    drawCard(284.0f, 199.0f, 98.0f, 26.0f, COL_CARD_ALT, COL_ACCENT_CYAN);
    drawTextFmt(333.0f, 204.0f, 0.46f, COL_ACCENT_CYAN, C2D_AlignCenter,
                "%.2f MB/s", g_current_speed_mbs);

    /*=======================================================================
     * PANTALLA INFERIOR TÁCTIL (320x240) — HISTORIAL Y CONTROLES
     *=======================================================================*/
    C2D_TargetClear(g_bot_target, COL_BG_DARK);
    C2D_SceneBegin(g_bot_target);

    /* Header Inferior */
    C2D_DrawRectSolid(0.0f, 0.0f, 0.0f, BOT_WIDTH, 25.0f, COL_BG_HEADER);
    drawTextFmt(8.0f, 4.0f, 0.44f, COL_ACCENT_CYAN, C2D_AlignLeft, "HISTORIAL DE SESION");
    drawTextFmt(312.0f, 4.0f, 0.41f, COL_GREEN_OK, C2D_AlignRight,
                "%lu arch. (%.1f MB)", (unsigned long)g_total_files_transferred, g_total_mb_transferred);

    /* Tarjeta de Historial de Archivos */
    drawCard(8.0f, 31.0f, 304.0f, 156.0f, COL_CARD_BG, COL_CARD_BORDER);

    if (g_history_count == 0) {
        drawTextFmt(160.0f, 82.0f, 0.46f, COL_TEXT_MUTED, C2D_AlignCenter,
                    "Aun no se han transferido archivos");
        drawTextFmt(160.0f, 104.0f, 0.40f, COL_ACCENT_CYAN, C2D_AlignCenter,
                    "Abre 3DS-Drop en tu PC para empezar");
        drawTextFmt(160.0f, 126.0f, 0.38f, COL_YELLOW_WARN, C2D_AlignCenter,
                    "♪ La musica sonara al transferir archivos");
    } else {
        for (int i = 0; i < g_history_count; i++) {
            float rowY = 37.0f + (float)i * 29.0f;
            drawCard(14.0f, rowY, 292.0f, 25.0f, COL_CARD_ALT,
                     g_history[i].isUpload ? COL_GREEN_DARK : COL_CYAN_SHADOW);
            drawTextFmt(20.0f, rowY + 4.0f, 0.41f,
                        g_history[i].isUpload ? COL_GREEN_OK : COL_ACCENT_CYAN,
                        C2D_AlignLeft, "%s %s",
                        g_history[i].isUpload ? "[RECIBIDO]" : "[ENVIADO]",
                        g_history[i].name);
            drawTextFmt(300.0f, rowY + 4.0f, 0.40f, COL_TEXT_MAIN, C2D_AlignRight,
                        "%.2f MB", g_history[i].sizeMb);
        }
    }

    /* Botón Táctil Izquierdo: Música de Transferencia ON/OFF [Y] */
    drawCard(BTN_MUSIC_X, BTN_MUSIC_Y, BTN_MUSIC_W, BTN_MUSIC_H,
             g_audio.musicAllowed ? COL_GREEN_DARK : COL_CARD_ALT,
             g_audio.musicAllowed ? COL_GREEN_OK : COL_CARD_BORDER);
    drawTextFmt(BTN_MUSIC_X + BTN_MUSIC_W * 0.5f, BTN_MUSIC_Y + 5.0f, 0.43f,
                g_audio.musicAllowed ? COL_GREEN_OK : COL_TEXT_MUTED, C2D_AlignCenter,
                "♪ MUSICA: %s [Y]", g_audio.musicAllowed ? "ON" : "OFF");
    drawTextFmt(BTN_MUSIC_X + BTN_MUSIC_W * 0.5f, BTN_MUSIC_Y + 21.0f, 0.36f,
                COL_TEXT_MAIN, C2D_AlignCenter, "Suena al transferir");

    /* Botón Táctil Derecho: Ahorro de Pantalla [SELECT] y Salir [START] */
    drawCard(BTN_LIGHT_X, BTN_LIGHT_Y, BTN_LIGHT_W, BTN_LIGHT_H, COL_CARD_ALT, COL_ACCENT_CYAN);
    drawTextFmt(BTN_LIGHT_X + BTN_LIGHT_W * 0.5f, BTN_LIGHT_Y + 5.0f, 0.42f,
                COL_ACCENT_CYAN, C2D_AlignCenter, "[SEL] PANTALLA OFF");
    drawTextFmt(BTN_LIGHT_X + BTN_LIGHT_W * 0.5f, BTN_LIGHT_Y + 21.0f, 0.36f,
                COL_YELLOW_WARN, C2D_AlignCenter, "[START] Salir al Menu");

    C3D_FrameEnd(0);
}

/*---------------------------------------------------------------------------
 * Utilidades JSON minimalistas y seguras en C
 *---------------------------------------------------------------------------*/
static bool json_extract_string(const char *json, const char *key, char *out, size_t out_sz) {
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return false;
    p += strlen(pattern);
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    if (*p != '"') return false;
    p++;

    size_t i = 0;
    while (*p && *p != '"' && i + 1 < out_sz) {
        if (*p == '\\' && *(p + 1)) {
            p++;
        }
        out[i++] = *p++;
    }
    out[i] = '\0';
    return true;
}

static bool json_extract_int64(const char *json, const char *key, int64_t *out) {
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return false;
    p += strlen(pattern);
    while (*p && (*p == ' ' || *p == '\t' || *p == ':')) p++;
    *out = strtoll(p, NULL, 10);
    return true;
}

static void json_escape_string(const char *in, char *out, size_t out_sz) {
    size_t j = 0;
    for (size_t i = 0; in[i] != '\0' && j + 2 < out_sz; i++) {
        char c = in[i];
        if (c == '"' || c == '\\') {
            out[j++] = '\\';
            out[j++] = c;
        } else if ((unsigned char)c >= 0x20) {
            out[j++] = c;
        }
    }
    out[j] = '\0';
}

/*---------------------------------------------------------------------------
 * Funciones de Sistema de Archivos en sdmc:/
 *---------------------------------------------------------------------------*/
static void build_sdmc_path(const char *remote_path, char *out, size_t out_sz) {
    if (!remote_path || remote_path[0] == '\0' || strcmp(remote_path, "/") == 0) {
        snprintf(out, out_sz, "sdmc:/");
    } else if (remote_path[0] == '/') {
        snprintf(out, out_sz, "sdmc:%s", remote_path);
    } else {
        snprintf(out, out_sz, "sdmc:/%s", remote_path);
    }
}

static void mkdir_recursive(const char *sd_path) {
    char tmp[512];
    snprintf(tmp, sizeof(tmp), "%s", sd_path);
    size_t len = strlen(tmp);
    if (len > 6 && tmp[len - 1] == '/') {
        tmp[len - 1] = '\0';
    }
    for (char *p = tmp + 6; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            mkdir(tmp, 0777);
            *p = '/';
        }
    }
    mkdir(tmp, 0777);
}

static void ensure_parent_dirs(const char *sd_file_path) {
    char tmp[512];
    snprintf(tmp, sizeof(tmp), "%s", sd_file_path);
    char *last_slash = strrchr(tmp, '/');
    if (last_slash && last_slash > tmp + 5) {
        *last_slash = '\0';
        mkdir_recursive(tmp);
    }
}

static int delete_recursive(const char *sd_path) {
    struct stat st;
    if (stat(sd_path, &st) != 0) {
        return -1;
    }
    if (S_ISDIR(st.st_mode)) {
        DIR *dir = opendir(sd_path);
        if (!dir) return -1;
        struct dirent *ent;
        while ((ent = readdir(dir)) != NULL) {
            if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
                continue;
            char child[512];
            snprintf(child, sizeof(child), "%s/%s", sd_path, ent->d_name);
            delete_recursive(child);
        }
        closedir(dir);
        return rmdir(sd_path);
    } else {
        return unlink(sd_path);
    }
}

/*---------------------------------------------------------------------------
 * Primitivas de Red TCP (Little-Endian Framing)
 *---------------------------------------------------------------------------*/
static bool tcp_recv_exact(int sock, void *buf, size_t len) {
    uint8_t *p = (uint8_t *)buf;
    size_t received = 0;
    while (received < len) {
        ssize_t n = recv(sock, p + received, len - received, 0);
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                svcSleepThread(1000000ULL);
                continue;
            }
            return false;
        }
        received += (size_t)n;
    }
    return true;
}

static bool tcp_send_exact(int sock, const void *buf, size_t len) {
    const uint8_t *p = (const uint8_t *)buf;
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(sock, p + sent, len - sent, 0);
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                svcSleepThread(1000000ULL);
                continue;
            }
            return false;
        }
        sent += (size_t)n;
    }
    return true;
}

static bool send_json_packet(int sock, const char *json_str) {
    uint32_t len_le = (uint32_t)strlen(json_str);
    if (!tcp_send_exact(sock, &len_le, sizeof(uint32_t))) return false;
    return tcp_send_exact(sock, json_str, len_le);
}

static char *recv_json_packet(int sock) {
    uint32_t len_le = 0;
    if (!tcp_recv_exact(sock, &len_le, sizeof(uint32_t))) return NULL;
    if (len_le == 0 || len_le > MAX_JSON_LEN) return NULL;

    char *buf = (char *)malloc(len_le + 1);
    if (!buf) return NULL;
    if (!tcp_recv_exact(sock, buf, len_le)) {
        free(buf);
        return NULL;
    }
    buf[len_le] = '\0';
    return buf;
}

/*---------------------------------------------------------------------------
 * Manejadores de Comandos TCP (PING, LIST, MKDIR, DELETE, UPLOAD, DOWNLOAD)
 *---------------------------------------------------------------------------*/
static void handle_list_cmd(int sock, const char *req_path) {
    char sd_path[512];
    build_sdmc_path(req_path, sd_path, sizeof(sd_path));

    DIR *dir = opendir(sd_path);
    if (!dir) {
        send_json_packet(sock, "{\"status\":\"ERROR\",\"error\":\"No se pudo abrir la carpeta en la microSD\"}");
        return;
    }

    size_t cap = 64 * 1024;
    char *json = (char *)malloc(cap);
    if (!json) {
        closedir(dir);
        return;
    }

    uint64_t free_b = 0, total_b = 0;
    get_sd_space(&free_b, &total_b);

    char escaped_path[512];
    json_escape_string(req_path, escaped_path, sizeof(escaped_path));

    size_t used = snprintf(
        json, cap,
        "{\"status\":\"OK\",\"path\":\"%s\",\"sd_free_bytes\":%llu,\"sd_total_bytes\":%llu,\"entries\":[",
        escaped_path, (unsigned long long)free_b, (unsigned long long)total_b
    );

    bool first = true;
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0)
            continue;

        char full_item[768];
        size_t slen = strlen(sd_path);
        if (slen > 0 && sd_path[slen - 1] == '/')
            snprintf(full_item, sizeof(full_item), "%s%s", sd_path, ent->d_name);
        else
            snprintf(full_item, sizeof(full_item), "%s/%s", sd_path, ent->d_name);

        struct stat st;
        bool is_dir = (ent->d_type == DT_DIR);
        uint64_t fsize = 0;
        uint64_t fmtime = 0;
        if (stat(full_item, &st) == 0) {
            is_dir = S_ISDIR(st.st_mode);
            fsize  = is_dir ? 0 : (uint64_t)st.st_size;
            fmtime = (uint64_t)st.st_mtime;
        }

        char esc_name[512];
        json_escape_string(ent->d_name, esc_name, sizeof(esc_name));

        if (used + 1024 >= cap) {
            cap *= 2;
            char *new_json = (char *)realloc(json, cap);
            if (!new_json) break;
            json = new_json;
        }

        used += snprintf(
            json + used, cap - used,
            "%s{\"name\":\"%s\",\"is_dir\":%s,\"size\":%llu,\"mtime\":%llu}",
            first ? "" : ",",
            esc_name,
            is_dir ? "true" : "false",
            (unsigned long long)fsize,
            (unsigned long long)fmtime
        );
        first = false;
    }
    closedir(dir);

    snprintf(json + used, cap - used, "]}");
    send_json_packet(sock, json);
    free(json);
}

static void handle_upload_cmd(int sock, const char *remote_path, int64_t total_size) {
    char sd_path[512];
    build_sdmc_path(remote_path, sd_path, sizeof(sd_path));
    ensure_parent_dirs(sd_path);

    FILE *fp = fopen(sd_path, "wb");
    if (!fp) {
        send_json_packet(sock, "{\"status\":\"ERROR\",\"error\":\"No se pudo crear el archivo en la microSD\"}");
        return;
    }

    if (g_sd_ram_buf) {
        setvbuf(fp, g_sd_ram_buf, _IOFBF, SD_RAM_BUF_SIZE);
    }

    aptSetSleepAllowed(false);
    g_transferring = true;
    audioPlaySfx(SFX_TRANSFER_START);
    audioSetTransferMusic(true);

    const char *base = strrchr(remote_path, '/');
    snprintf(g_current_file, sizeof(g_current_file), "%s", base ? base + 1 : remote_path);
    snprintf(g_status_line, sizeof(g_status_line), "Recibiendo archivo desde tu PC...");
    g_current_pct = 0;
    g_current_speed_mbs = 0.0f;
    render_ui_frame();

    if (!send_json_packet(sock, "{\"status\":\"READY\"}")) {
        fclose(fp);
        g_transferring = false;
        audioSetTransferMusic(false);
        aptSetSleepAllowed(true);
        return;
    }

    int64_t received = 0;
    u64 start_ms = osGetTime();
    u64 last_ui_ms = start_ms;
    bool ok = true;

    while (received < total_size) {
        size_t to_read = NET_CHUNK_SIZE;
        if ((int64_t)to_read > (total_size - received)) {
            to_read = (size_t)(total_size - received);
        }

        ssize_t n = recv(sock, g_net_chunk_buf, to_read, 0);
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                svcSleepThread(1000000ULL);
                continue;
            }
            ok = false;
            break;
        }

        if (fwrite(g_net_chunk_buf, 1, (size_t)n, fp) != (size_t)n) {
            ok = false;
            break;
        }
        received += n;

        u64 now_ms = osGetTime();
        if (now_ms - last_ui_ms >= 50 || received == total_size) {
            last_ui_ms = now_ms;
            u64 elapsed_ms = (now_ms > start_ms) ? (now_ms - start_ms) : 1;
            g_current_pct = (total_size > 0) ? (int)((received * 100) / total_size) : 100;
            g_current_speed_mbs = ((float)received / (1024.0f * 1024.0f)) / ((float)elapsed_ms / 1000.0f);
            render_ui_frame();
        }
    }

    fflush(fp);
    fclose(fp);
    g_transferring = false;
    audioSetTransferMusic(false);
    aptSetSleepAllowed(true);

    if (!ok) {
        unlink(sd_path);
        snprintf(g_status_line, sizeof(g_status_line), "Transferencia cancelada o cortada");
        render_ui_frame();
        return;
    }

    uint64_t free_b = 0, total_b = 0;
    get_sd_space(&free_b, &total_b);

    char resp[256];
    snprintf(
        resp, sizeof(resp),
        "{\"status\":\"OK\",\"bytes_written\":%lld,\"sd_free_bytes\":%llu,\"sd_total_bytes\":%llu}",
        (long long)received, (unsigned long long)free_b, (unsigned long long)total_b
    );
    send_json_packet(sock, resp);

    double mb = (double)received / (1024.0 * 1024.0);
    add_history_item(g_current_file, mb, true);
    audioPlaySfx(SFX_TRANSFER_DONE);

    snprintf(g_status_line, sizeof(g_status_line), "¡Transferencia completada con exito!");
    g_current_pct = 100;
    render_ui_frame();
}

static void handle_download_cmd(int sock, const char *remote_path) {
    char sd_path[512];
    build_sdmc_path(remote_path, sd_path, sizeof(sd_path));

    struct stat st;
    if (stat(sd_path, &st) != 0 || S_ISDIR(st.st_mode)) {
        send_json_packet(sock, "{\"status\":\"ERROR\",\"error\":\"Archivo no encontrado en la microSD\"}");
        return;
    }

    FILE *fp = fopen(sd_path, "rb");
    if (!fp) {
        send_json_packet(sock, "{\"status\":\"ERROR\",\"error\":\"No se pudo leer el archivo\"}");
        return;
    }

    if (g_sd_ram_buf) {
        setvbuf(fp, g_sd_ram_buf, _IOFBF, SD_RAM_BUF_SIZE);
    }

    aptSetSleepAllowed(false);
    g_transferring = true;
    audioPlaySfx(SFX_TRANSFER_START);
    audioSetTransferMusic(true);

    int64_t fsize = (int64_t)st.st_size;
    char ready_msg[128];
    snprintf(ready_msg, sizeof(ready_msg), "{\"status\":\"READY\",\"size\":%lld}", (long long)fsize);
    if (!send_json_packet(sock, ready_msg)) {
        fclose(fp);
        g_transferring = false;
        audioSetTransferMusic(false);
        aptSetSleepAllowed(true);
        return;
    }

    const char *base = strrchr(remote_path, '/');
    snprintf(g_current_file, sizeof(g_current_file), "%s", base ? base + 1 : remote_path);
    snprintf(g_status_line, sizeof(g_status_line), "Enviando archivo hacia tu PC...");

    int64_t sent = 0;
    u64 start_ms = osGetTime();
    u64 last_ui_ms = start_ms;
    bool ok = true;

    while (sent < fsize) {
        size_t n = fread(g_net_chunk_buf, 1, NET_CHUNK_SIZE, fp);
        if (n == 0) break;
        if (!tcp_send_exact(sock, g_net_chunk_buf, n)) {
            ok = false;
            break;
        }
        sent += (int64_t)n;

        u64 now_ms = osGetTime();
        if (now_ms - last_ui_ms >= 50 || sent == fsize) {
            last_ui_ms = now_ms;
            u64 elapsed_ms = (now_ms > start_ms) ? (now_ms - start_ms) : 1;
            g_current_pct = (fsize > 0) ? (int)((sent * 100) / fsize) : 100;
            g_current_speed_mbs = ((float)sent / (1024.0f * 1024.0f)) / ((float)elapsed_ms / 1000.0f);
            render_ui_frame();
        }
    }

    fclose(fp);
    g_transferring = false;
    audioSetTransferMusic(false);
    aptSetSleepAllowed(true);

    if (ok) {
        char done_msg[128];
        snprintf(done_msg, sizeof(done_msg), "{\"status\":\"OK\",\"bytes_sent\":%lld}", (long long)sent);
        send_json_packet(sock, done_msg);

        double mb = (double)sent / (1024.0 * 1024.0);
        add_history_item(g_current_file, mb, false);
        audioPlaySfx(SFX_TRANSFER_DONE);

        snprintf(g_status_line, sizeof(g_status_line), "¡Archivo enviado a tu PC con exito!");
        render_ui_frame();
    }
}

static void handle_client_connection(int client_sock) {
    int flag = 1;
    setsockopt(client_sock, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(flag));
    int sock_buf = 256 * 1024;
    setsockopt(client_sock, SOL_SOCKET, SO_RCVBUF, &sock_buf, sizeof(sock_buf));
    setsockopt(client_sock, SOL_SOCKET, SO_SNDBUF, &sock_buf, sizeof(sock_buf));

    int flags = fcntl(client_sock, F_GETFL, 0);
    fcntl(client_sock, F_SETFL, flags & ~O_NONBLOCK);

    char *req_json = recv_json_packet(client_sock);
    if (!req_json) {
        closesocket(client_sock);
        return;
    }

    char cmd[32] = {0};
    char path[512] = "/";
    int64_t size = 0;

    json_extract_string(req_json, "cmd", cmd, sizeof(cmd));
    json_extract_string(req_json, "path", path, sizeof(path));
    json_extract_int64(req_json, "size", &size);

    if (strcmp(cmd, "PING") == 0) {
        uint64_t free_b = 0, total_b = 0;
        get_sd_space(&free_b, &total_b);
        char resp[256];
        snprintf(
            resp, sizeof(resp),
            "{\"status\":\"OK\",\"model\":\"%s\",\"sd_free_bytes\":%llu,\"sd_total_bytes\":%llu}",
            g_model_name, (unsigned long long)free_b, (unsigned long long)total_b
        );
        send_json_packet(client_sock, resp);
    } else if (strcmp(cmd, "LIST") == 0) {
        snprintf(g_status_line, sizeof(g_status_line), "Conectada con tu PC (Explorando SD)");
        handle_list_cmd(client_sock, path);
    } else if (strcmp(cmd, "MKDIR") == 0) {
        char sd_path[512];
        build_sdmc_path(path, sd_path, sizeof(sd_path));
        mkdir_recursive(sd_path);
        send_json_packet(client_sock, "{\"status\":\"OK\"}");
    } else if (strcmp(cmd, "DELETE") == 0) {
        char sd_path[512];
        build_sdmc_path(path, sd_path, sizeof(sd_path));
        if (delete_recursive(sd_path) == 0) {
            send_json_packet(client_sock, "{\"status\":\"OK\"}");
        } else {
            send_json_packet(client_sock, "{\"status\":\"ERROR\",\"error\":\"No se pudo eliminar\"}");
        }
    } else if (strcmp(cmd, "UPLOAD") == 0) {
        handle_upload_cmd(client_sock, path, size);
    } else if (strcmp(cmd, "DOWNLOAD") == 0) {
        handle_download_cmd(client_sock, path);
    } else {
        send_json_packet(client_sock, "{\"status\":\"ERROR\",\"error\":\"Comando no soportado\"}");
    }

    free(req_json);
    closesocket(client_sock);
}

static void send_udp_announcement(int udp_sock, const struct sockaddr_in *dest_addr) {
    uint64_t free_b = 0, total_b = 0;
    get_sd_space(&free_b, &total_b);
    update_local_ip();

    char payload[320];
    int len = snprintf(
        payload, sizeof(payload),
        "{\"magic\":\"3DSDROP_HERE\",\"model\":\"%s\",\"ip\":\"%s\",\"tcp_port\":%d,\"sd_free_bytes\":%llu,\"sd_total_bytes\":%llu}",
        g_model_name, g_ip_str, TCP_PORT,
        (unsigned long long)free_b, (unsigned long long)total_b
    );
    sendto(udp_sock, payload, len, 0, (const struct sockaddr *)dest_addr, sizeof(*dest_addr));
}

static void toggle_backlight(void) {
    g_backlight_on = !g_backlight_on;
    if (g_backlight_on) {
        GSPLCD_PowerOnBacklight(GSPLCD_SCREEN_BOTH);
    } else {
        GSPLCD_PowerOffBacklight(GSPLCD_SCREEN_BOTH);
    }
}

int main(int argc, char **argv) {
    (void)argc;
    (void)argv;

    gfxInitDefault();
    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    C2D_Init(C2D_DEFAULT_MAX_OBJECTS);
    C2D_Prepare();

    g_top_target = C2D_CreateScreenTarget(GFX_TOP, GFX_LEFT);
    g_bot_target = C2D_CreateScreenTarget(GFX_BOTTOM, GFX_LEFT);
    g_text_buf   = C2D_TextBufNew(4096);

    fsInit();
    gspLcdInit();
    init_console_hardware();
    audioInit();
    audioPlaySfx(SFX_STARTUP);

    g_sd_ram_buf    = (char *)memalign(0x1000, SD_RAM_BUF_SIZE);
    g_net_chunk_buf = (uint8_t *)memalign(0x1000, NET_CHUNK_SIZE);
    g_soc_buffer    = (u32 *)memalign(SOC_ALIGN, SOC_BUFFERSIZE);

    Result ret = socInit(g_soc_buffer, SOC_BUFFERSIZE);
    if (R_FAILED(ret)) {
        snprintf(g_status_line, sizeof(g_status_line), "Activa el Wi-Fi de tu consola");
    } else {
        update_local_ip();
    }

    int udp_sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (udp_sock >= 0) {
        int bcast = 1;
        setsockopt(udp_sock, SOL_SOCKET, SO_BROADCAST, &bcast, sizeof(bcast));
        struct sockaddr_in udp_bind;
        memset(&udp_bind, 0, sizeof(udp_bind));
        udp_bind.sin_family      = AF_INET;
        udp_bind.sin_port        = htons(UDP_PORT);
        udp_bind.sin_addr.s_addr = htonl(INADDR_ANY);
        bind(udp_sock, (struct sockaddr *)&udp_bind, sizeof(udp_bind));
        fcntl(udp_sock, F_SETFL, fcntl(udp_sock, F_GETFL, 0) | O_NONBLOCK);
    }

    int tcp_sock = socket(AF_INET, SOCK_STREAM, 0);
    if (tcp_sock >= 0) {
        int reuse = 1;
        setsockopt(tcp_sock, SOL_SOCKET, SO_REUSEADDR, &reuse, sizeof(reuse));
        struct sockaddr_in tcp_bind;
        memset(&tcp_bind, 0, sizeof(tcp_bind));
        tcp_bind.sin_family      = AF_INET;
        tcp_bind.sin_port        = htons(TCP_PORT);
        tcp_bind.sin_addr.s_addr = htonl(INADDR_ANY);
        bind(tcp_sock, (struct sockaddr *)&tcp_bind, sizeof(tcp_bind));
        listen(tcp_sock, 4);
        fcntl(tcp_sock, F_SETFL, fcntl(tcp_sock, F_GETFL, 0) | O_NONBLOCK);
    }

    struct sockaddr_in bcast_addr;
    memset(&bcast_addr, 0, sizeof(bcast_addr));
    bcast_addr.sin_family      = AF_INET;
    bcast_addr.sin_port        = htons(UDP_PORT);
    bcast_addr.sin_addr.s_addr = htonl(INADDR_BROADCAST);

    u64 last_bcast_ms = 0;

    while (aptMainLoop()) {
        hidScanInput();
        u32 kDown = hidKeysDown();

        if (kDown & KEY_START) {
            break;
        }

        if (kDown & KEY_SELECT) {
            audioPlaySfx(SFX_TAP);
            toggle_backlight();
        }

        if (kDown & KEY_Y) {
            g_audio.musicAllowed = !g_audio.musicAllowed;
            audioPlaySfx(SFX_TAP);
        }

        if (kDown & KEY_TOUCH) {
            touchPosition touch;
            hidTouchRead(&touch);
            if (touch.px >= BTN_MUSIC_X && touch.px <= (BTN_MUSIC_X + BTN_MUSIC_W) &&
                touch.py >= BTN_MUSIC_Y && touch.py <= (BTN_MUSIC_Y + BTN_MUSIC_H)) {
                g_audio.musicAllowed = !g_audio.musicAllowed;
                audioPlaySfx(SFX_TAP);
            } else if (touch.px >= BTN_LIGHT_X && touch.px <= (BTN_LIGHT_X + BTN_LIGHT_W) &&
                       touch.py >= BTN_LIGHT_Y && touch.py <= (BTN_LIGHT_Y + BTN_LIGHT_H)) {
                audioPlaySfx(SFX_TAP);
                toggle_backlight();
            }
        }

        u64 now_ms = osGetTime();
        if (udp_sock >= 0 && (now_ms - last_bcast_ms >= 2000)) {
            last_bcast_ms = now_ms;
            send_udp_announcement(udp_sock, &bcast_addr);
        }

        if (udp_sock >= 0) {
            char udp_buf[128];
            struct sockaddr_in sender;
            socklen_t sender_len = sizeof(sender);
            ssize_t r = recvfrom(udp_sock, udp_buf, sizeof(udp_buf) - 1, 0,
                                 (struct sockaddr *)&sender, &sender_len);
            if (r > 0) {
                udp_buf[r] = '\0';
                if (strstr(udp_buf, "3DSDROP_DISCOVER") != NULL) {
                    send_udp_announcement(udp_sock, &sender);
                }
            }
        }

        if (tcp_sock >= 0) {
            struct sockaddr_in client_addr;
            socklen_t client_len = sizeof(client_addr);
            int client_sock = accept(tcp_sock, (struct sockaddr *)&client_addr, &client_len);
            if (client_sock >= 0) {
                handle_client_connection(client_sock);
            }
        }

        render_ui_frame();
    }

    if (!g_backlight_on) {
        GSPLCD_PowerOnBacklight(GSPLCD_SCREEN_BOTH);
    }

    if (udp_sock >= 0) closesocket(udp_sock);
    if (tcp_sock >= 0) closesocket(tcp_sock);

    socExit();
    if (g_soc_buffer) free(g_soc_buffer);
    if (g_sd_ram_buf) free(g_sd_ram_buf);
    if (g_net_chunk_buf) free(g_net_chunk_buf);

    if (g_is_new_3ds) {
        osSetSpeedupEnable(false);
    }

    audioExit();
    C2D_TextBufDelete(g_text_buf);
    C2D_Fini();
    C3D_Fini();
    gspLcdExit();
    fsExit();
    gfxExit();
    return 0;
}
