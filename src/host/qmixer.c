/*
 * QSound's QMixer (QMixer.dll), answered here: the game's 3D sound mixer.
 *
 * Theme Park World opens every sound as an input stream (OpenWaveEx type
 * 0x11): a WAVEFORMATEX, a block size, and a callback the mixer calls for
 * each block, (buffer, bytes, user) -> nonzero while there is more. Its own
 * decoder thread fills those blocks, so a callback can wait (up to 500 ms)
 * for data; each channel therefore has a puller thread of its own (made on
 * the channel's first play and kept: every thread that calls the game gets
 * a guest stack, so they are not made per sound) that keeps a ring a few
 * blocks ahead of playback from the callback, and one mixer thread mixes the
 * rings
 * into win32hle's audio output (hle_audio_stream_*), 44.1 kHz stereo,
 * keeping about 100 ms queued.
 *
 * Per channel: volume (0..32767), a frequency override, pause, and simple 3D
 * placement: distance between the mapping's minimum (full volume) and
 * maximum (silent), and left/right from the listener's orientation. QMixer's
 * vectors are three floats.
 *
 * Each export pops what QMixer.dll's own does: its `ret N`, measured from the
 * disc's DLL as tools/gen/won_stubs.py measures the Won libraries.
 * TPW_QMTRACE=1 prints set-up, waves and plays; TPW_QMWAV=FILE records the mix.
 */
#include <math.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "win32hle.h"

#define RATE        44100
#define NCHAN       64
#define RING        (1 << 18)            /* bytes of source audio buffered per channel */
#define AHEAD       4410                 /* frames kept queued at the output: 100 ms */
/* Blocks a channel pulls ahead of what it plays. The game decodes a sound a
 * window at a time and answers "no more" to a mixer that has run past it:
 * pulled as fast as the ring took them, every sound stopped at 31 blocks
 * (6.5 s of speech). QMixer pulls just ahead of playback; so does this. */
#define LEAD        4
#define MMSYSERR_ERROR 1u

static int g_trace = -1;
static int trace(void) { if (g_trace < 0) g_trace = getenv("TPW_QMTRACE") != NULL; return g_trace; }
#define T(...) do { if (trace()) fprintf(stderr, "[qmixer] " __VA_ARGS__); } while (0)

typedef struct { float x, y, z; } vec3;

typedef struct {
    uint32_t va;                          /* the handle the game holds: a block of guest memory */
    int channels, bits, rate;
    uint32_t block, callback, user, buf;  /* the stream: block size, callback, its user data, a guest buffer */
} wave_t;

typedef struct {
    int open, playing, paused, stopping, eof;
    wave_t *wave;
    float volume;                         /* 0..1 */
    int freq;                             /* 0: the wave's own rate */
    vec3 pos;
    int has_pos;
    uint8_t ring[RING];
    int head, tail;                       /* bytes: read at head, written at tail */
    double frac;                          /* the read position between source frames */
    pthread_t puller;
    int puller_live;                      /* its puller thread exists */
    int busy;                             /* the puller is between taking a wave and letting it go */
    uint32_t pulled;                      /* bytes of the wave so far */
    wave_t own;                           /* a spare's: the format of the tail it plays (the wave may go) */
    pthread_cond_t wake;
} chan_t;

static chan_t g_ch[NCHAN];
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_idle = PTHREAD_COND_INITIALIZER;   /* a puller let its wave go */
static int g_stream = -1, g_session;
static double g_t0;                                      /* the trace's clock: from InitEx */
static vec3 g_listener, g_front = { 0, 0, 1 }, g_up = { 0, 1, 0 };
static float g_min_dist = 1.0f, g_max_dist = 1000.0f;
#define MAX_WAVES 4096
static wave_t *g_waves[MAX_WAVES];

static float fl(uint32_t v) { float f; memcpy(&f, &v, 4); return f; }
static vec3 vec_at(uint32_t p) { vec3 v = { fl(MEM32(p)), fl(MEM32(p + 4)), fl(MEM32(p + 8)) }; return v; }
static int ring_used(const chan_t *c) { return (c->tail - c->head + RING) % RING; }

/* ---- pulling a stream's blocks ----
 * One thread a channel, for good: it sleeps until the channel plays a
 * stream, pulls blocks into the ring while there is room, until the stream
 * ends or the channel stops, then sleeps again. */
static void *puller(void *arg) {
    chan_t *c = (chan_t *)arg;
    pthread_mutex_lock(&g_mu);
    for (;;) {
        while (!c->playing || c->eof || c->stopping || !c->wave) {
            c->busy = 0;
            pthread_cond_broadcast(&g_idle);
            pthread_cond_wait(&c->wake, &g_mu);
        }
        c->busy = 1;
        wave_t *w = c->wave;
        if (ring_used(c) >= LEAD * (int)w->block || RING - 1 - ring_used(c) < (int)w->block) {   /* far enough ahead: wait for the mix */
            pthread_mutex_unlock(&g_mu);
            usleep(5000);
            pthread_mutex_lock(&g_mu);
            continue;
        }
        pthread_mutex_unlock(&g_mu);
        uint32_t a[3] = { w->buf, w->block, w->user };
        uint32_t t0 = hle_ticks_ms();
        uint32_t more = hle_call_guest(w->callback, 3, a);
        uint32_t took = hle_ticks_ms() - t0;
        pthread_mutex_lock(&g_mu);
        if (c->stopping || c->wave != w) continue;
        c->pulled += w->block;
        if (!more || took > 200) T("%.1fs channel %d: %s after %u bytes (%.1f s), the block took %u ms\n", hle_ticks_ms() / 1000.0 - g_t0, (int)(c - g_ch),
                                   more ? "slow block" : "stream ends", c->pulled,
                                   c->pulled / (double)(w->rate * w->channels * (w->bits / 8)), took);
        const uint8_t *src = (const uint8_t *)hle_ptr(w->buf);
        for (uint32_t i = 0; i < w->block; i++) c->ring[(c->tail + (int)i) % RING] = src[i];
        c->tail = (c->tail + (int)w->block) % RING;
        if (!more) c->eof = 1;
    }
    return NULL;
}
/* Stop a channel: its puller lets the wave go first, and the machine is given
 * up while waiting for that, since the puller may be waiting for it to call
 * the game. */
static void stop_channel(chan_t *c) {
    pthread_mutex_lock(&g_mu);
    c->stopping = 1;
    while (c->busy) {
        pthread_mutex_unlock(&g_mu);
        hle_block_begin();
        pthread_mutex_lock(&g_mu);
        if (c->busy) pthread_cond_wait(&g_idle, &g_mu);
        pthread_mutex_unlock(&g_mu);
        hle_block_end();
        pthread_mutex_lock(&g_mu);
    }
    c->playing = 0, c->paused = 0, c->eof = 0, c->head = c->tail = 0, c->frac = 0, c->wave = NULL;
    c->stopping = 0;
    pthread_mutex_unlock(&g_mu);
}

/* ---- the mix ---- */
static int sample_at(const chan_t *c, int frame, float out[2]) {
    const wave_t *w = c->wave;
    int bps = w->bits / 8, fb = bps * w->channels;
    if ((frame + 1) * fb > ring_used(c)) return 0;
    for (int ch = 0; ch < 2; ch++) {
        int sc = w->channels == 1 ? 0 : ch;
        int at = (c->head + frame * fb + sc * bps) % RING;
        float v;
        if (bps == 2) {
            int16_t s = (int16_t)(c->ring[at] | c->ring[(at + 1) % RING] << 8);
            v = (float)s / 32768.0f;
        } else {
            v = ((float)c->ring[at] - 128.0f) / 128.0f;
        }
        out[ch] = v;
    }
    return 1;
}
/* 3D: a gain and a pan (-1 left .. 1 right) from where the source is. */
static void place(const chan_t *c, float *gain, float *pan) {
    *gain = 1, *pan = 0;
    if (!c->has_pos) return;
    vec3 d = { c->pos.x - g_listener.x, c->pos.y - g_listener.y, c->pos.z - g_listener.z };
    float dist = sqrtf(d.x * d.x + d.y * d.y + d.z * d.z);
    if (dist >= g_max_dist) { *gain = 0; return; }
    if (dist > g_min_dist) *gain = g_min_dist / dist;
    if (dist > 1e-4f) {
        vec3 r = { g_up.y * g_front.z - g_up.z * g_front.y, g_up.z * g_front.x - g_up.x * g_front.z,
                   g_up.x * g_front.y - g_up.y * g_front.x };   /* up x front: the listener's right */
        float rl = sqrtf(r.x * r.x + r.y * r.y + r.z * r.z);
        if (rl > 1e-4f) *pan = (d.x * r.x + d.y * r.y + d.z * r.z) / (dist * rl);
    }
}
static void mix(int16_t *out, int frames) {
    static float acc[2 * 1024];
    memset(acc, 0, sizeof(float) * 2 * (size_t)frames);
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < NCHAN; i++) {
        chan_t *c = &g_ch[i];
        if (!c->playing || c->paused || !c->wave) continue;
        const wave_t *w = c->wave;
        float gain, pan;
        place(c, &gain, &pan);
        gain *= c->volume;
        float gl = gain * (pan > 0 ? 1 - pan : 1), gr = gain * (pan < 0 ? 1 + pan : 1);
        double step = (double)(c->freq ? c->freq : w->rate) / RATE;
        int fb = w->bits / 8 * w->channels;
        for (int f = 0; f < frames; f++) {
            float a[2], b[2];
            if (!sample_at(c, 0, a)) {
                if (c->eof) { c->playing = 0; c->wave = NULL; }   /* played to the end */
                break;
            }
            if (!sample_at(c, 1, b)) memcpy(b, a, sizeof a);
            float t = (float)c->frac;
            acc[2 * f] += (a[0] + (b[0] - a[0]) * t) * gl;
            acc[2 * f + 1] += (a[1] + (b[1] - a[1]) * t) * gr;
            c->frac += step;
            int whole = (int)c->frac;
            if (whole) {
                c->frac -= whole;
                int have = ring_used(c) / fb;
                if (whole > have) whole = have;
                c->head = (c->head + whole * fb) % RING;
            }
        }
    }
    pthread_mutex_unlock(&g_mu);
    for (int i = 0; i < 2 * frames; i++) {
        float v = acc[i] * 32767.0f;
        out[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
    }
}
/* TPW_QMWAV=FILE: the mix, as it is played, also written to a WAV file (its
 * sizes filled in as it grows), to hear or measure a headless run. */
static FILE *g_wav;
static uint32_t g_wav_frames;
static void wav_header(void) {
    uint32_t data = g_wav_frames * 4, riff = 36 + data, rate = RATE, bps = RATE * 4, fmt = 16;
    uint16_t pcm = 1, ch = 2, align = 4, bits = 16;
    fseek(g_wav, 0, SEEK_SET);
    fwrite("RIFF", 1, 4, g_wav), fwrite(&riff, 4, 1, g_wav), fwrite("WAVEfmt ", 1, 8, g_wav);
    fwrite(&fmt, 4, 1, g_wav), fwrite(&pcm, 2, 1, g_wav), fwrite(&ch, 2, 1, g_wav), fwrite(&rate, 4, 1, g_wav);
    fwrite(&bps, 4, 1, g_wav), fwrite(&align, 2, 1, g_wav), fwrite(&bits, 2, 1, g_wav);
    fwrite("data", 1, 4, g_wav), fwrite(&data, 4, 1, g_wav);
    fseek(g_wav, 0, SEEK_END);
}
static void *mixer(void *arg) {
    (void)arg;
    int16_t buf[2 * 441];
    const char *wav = getenv("TPW_QMWAV");
    if (wav && (g_wav = fopen(wav, "wb"))) wav_header();
    for (;;) {
        while (hle_audio_stream_queued(g_stream) < AHEAD) {
            mix(buf, 441);
            hle_audio_stream_push(g_stream, buf, 441);
            if (g_wav) {
                fwrite(buf, 4, 441, g_wav);
                g_wav_frames += 441;
                if (g_wav_frames % (RATE / 2) < 441) wav_header(), fflush(g_wav);
            }
        }
        usleep(5000);
    }
    return NULL;
}

/* ---- the exports ---- */
static chan_t *chan(uint32_t i) { return i < NCHAN ? &g_ch[i] : NULL; }

static void q_QSWaveMixInitEx(void) {                    /* (QMIXCONFIG*) -> a session */
    uint32_t cfg = A32(0);
    T("InitEx: rate %u, channels %d\n", cfg ? MEM32(cfg + 8) : 0, cfg ? (int)MEM32(cfg + 20) : 0);
    if (g_stream < 0) {
        g_t0 = hle_ticks_ms() / 1000.0;
        g_stream = hle_audio_stream_open(RATE, 2);
        if (g_stream >= 0) {
            pthread_t t;
            pthread_create(&t, NULL, mixer, NULL);
            pthread_detach(t);
        } else {
            fprintf(stderr, "[qmixer] no audio output: silent\n");
        }
    }
    g_session = 0x51D0;
    RET(g_session, 1);
}
static void q_QSWaveMixActivate(void) { RET(0, 2); }
static void q_QSWaveMixCloseSession(void) {
    for (int i = 0; i < NCHAN; i++) stop_channel(&g_ch[i]);
    RET(0, 1);
}
static void q_QSWaveMixOpenChannel(void) {               /* (session, channel, flags) */
    uint32_t ch = A32(1), fl_ = A32(2);
    int lo = 0, hi = 0;
    if (fl_ == 1) lo = 0, hi = NCHAN;                     /* QMIX_OPENALL */
    else if (fl_ == 2) lo = 0, hi = (int)(ch < NCHAN ? ch : NCHAN);   /* QMIX_OPENCOUNT */
    else if (fl_ == 3) {                                  /* QMIX_OPENAVAILABLE: the first not open */
        for (int i = 0; i < NCHAN; i++) if (!g_ch[i].open) { g_ch[i].open = 1, g_ch[i].volume = 1; RET((uint32_t)i, 3); }
        RET(0xFFFFFFFFu, 3);
    } else lo = (int)ch, hi = (int)ch + 1;
    for (int i = lo; i < hi && i < NCHAN; i++) if (!g_ch[i].open) g_ch[i].open = 1, g_ch[i].volume = 1;
    T("OpenChannel %u flags %u\n", ch, fl_);
    RET(0, 3);
}
static void q_QSWaveMixConfigureChannel(void) { RET(0, 5); }
static void q_QSWaveMixEnableChannel(void) { RET(0, 4); }

static void q_QSWaveMixOpenWaveEx(void) {                /* (session, params, flags) -> a wave */
    uint32_t p = A32(1), type = A32(2) & 0xFF;
    if (type != 0x11 || !p) {                             /* only input streams: all TPW opens */
        T("OpenWaveEx: type %X not answered\n", type);
        RET(0, 3);
    }
    uint32_t fmt = MEM32(p);
    wave_t *w = (wave_t *)calloc(1, sizeof *w);
    w->channels = MEM16(fmt + 2), w->rate = (int)MEM32(fmt + 4), w->bits = MEM16(fmt + 14);
    w->block = MEM32(p + 4), w->callback = MEM32(p + 8), w->user = MEM32(p + 12);
    if (MEM16(fmt) != 1 || (w->bits != 8 && w->bits != 16) || w->channels < 1 || w->channels > 2 ||
        !w->block || w->block > RING / 4) {
        T("OpenWaveEx: format %u, %d-bit, %d channels, block %u: not answered\n", MEM16(fmt), w->bits, w->channels, w->block);
        free(w);
        RET(0, 3);
    }
    w->buf = hle_va(hle_alloc(w->block));
    w->va = hle_va(hle_alloc(16));                       /* what the game holds */
    pthread_mutex_lock(&g_mu);
    int slot = -1;
    for (int i = 0; i < MAX_WAVES; i++) if (!g_waves[i]) { g_waves[i] = w; slot = i; break; }
    pthread_mutex_unlock(&g_mu);
    if (slot < 0) { free(w); RET(0, 3); }
    MEM32(w->va) = (uint32_t)slot;
    T("OpenWaveEx: stream %d Hz, %d-bit, %d ch, %u-byte blocks, callback %08X user %08X -> %08X\n", w->rate, w->bits, w->channels, w->block, w->callback, w->user, w->va);
    RET(w->va, 3);
}
static wave_t *wave_of(uint32_t va) {
    if (!va) return NULL;
    uint32_t slot = MEM32(va);
    return slot < MAX_WAVES && g_waves[slot] && g_waves[slot]->va == va ? g_waves[slot] : NULL;
}
/* The game counts a sound done once its decoder has given the last block,
 * which the mixer asks for before it has played what it holds, and sets the
 * channel up for another, frees the wave, or plays another there: a line of
 * speech pulled 6 s ahead lost all but its first three seconds of nine. A
 * tail like that, all given and still sounding, goes on in a channel the
 * game has not opened, to its end, with its own volume and place. */
static void hand_tail(chan_t *c) {
    pthread_mutex_lock(&g_mu);
    chan_t *s = NULL;
    for (int i = NCHAN - 1; i >= 0 && !s; i--) if (!g_ch[i].open && !g_ch[i].playing) s = &g_ch[i];
    if (s) {
        int n = ring_used(c);
        s->own = *c->wave, s->wave = &s->own;
        for (int i = 0; i < n; i++) s->ring[i] = c->ring[(c->head + i) % RING];
        s->head = 0, s->tail = n, s->frac = c->frac;
        s->volume = c->volume, s->freq = c->freq, s->pos = c->pos, s->has_pos = c->has_pos;
        s->eof = 1, s->paused = 0, s->playing = 1;
        T("%.1fs channel %d: its last %.1f s go on in channel %d (volume %.2f, %s)\n", hle_ticks_ms() / 1000.0 - g_t0,
          (int)(c - g_ch), n / (double)(c->wave->rate * c->wave->channels * (c->wave->bits / 8)), (int)(s - g_ch),
          c->volume, c->has_pos ? "3D" : "2D");
    }
    pthread_mutex_unlock(&g_mu);
}
/* The game sets a channel up (volume, place, frequency) for its next sound
 * before playing it there: a tail still sounding goes on first, as it was. */
static void before_setup(chan_t *c) {
    if (!c || !c->wave || !c->eof || !ring_used(c)) return;
    hand_tail(c);
    stop_channel(c);
}
static void q_QSWaveMixFreeWave(void) {                  /* (session, wave) */
    wave_t *w = wave_of(A32(1));
    if (w) {
        for (int i = 0; i < NCHAN; i++) {
            chan_t *c = &g_ch[i];
            if (c->wave != w) continue;
            if (c->eof && ring_used(c)) hand_tail(c);
            stop_channel(c);
        }
        pthread_mutex_lock(&g_mu);
        g_waves[MEM32(w->va)] = NULL;
        pthread_mutex_unlock(&g_mu);
        hle_free(hle_ptr(w->buf));
        hle_free(hle_ptr(w->va));
        free(w);
    }
    RET(0, 2);
}
static void q_QSWaveMixPlayEx(void) {                    /* (session, channel, flags, wave, loops, params) */
    chan_t *c = chan(A32(1));
    wave_t *w = wave_of(A32(3));
    if (!c || !w) RET(MMSYSERR_ERROR, 6);
    if (c->wave && c->eof && ring_used(c)) hand_tail(c);
    stop_channel(c);
    pthread_mutex_lock(&g_mu);
    if (!c->puller_live) {
        pthread_cond_init(&c->wake, NULL);
        if (pthread_create(&c->puller, NULL, puller, c) == 0) pthread_detach(c->puller), c->puller_live = 1;
    }
    c->wave = w, c->playing = 1, c->paused = 0, c->eof = 0, c->head = c->tail = 0, c->frac = 0, c->pulled = 0;
    pthread_cond_signal(&c->wake);
    pthread_mutex_unlock(&g_mu);
    T("%.1fs PlayEx channel %u flags %X wave %08X\n", hle_ticks_ms() / 1000.0 - g_t0, A32(1), A32(2), A32(3));
    RET(0, 6);
}
static void q_QSWaveMixStopChannel(void) {               /* (session, channel, flags) */
    if (A32(2) & 1) { for (int i = 0; i < NCHAN; i++) stop_channel(&g_ch[i]); RET(0, 3); }   /* QMIX_ALL */
    chan_t *c = chan(A32(1));
    if (c && c->playing && (!c->eof || ring_used(c)) && trace())
        T("channel %u: stopped, %u bytes in, %d left unplayed\n", A32(1), c->pulled, ring_used(c));
    if (c) stop_channel(c);
    RET(0, 3);
}
static void q_QSWaveMixPauseChannel(void) {
    if (A32(2) & 1) { for (int i = 0; i < NCHAN; i++) g_ch[i].paused = 1; RET(0, 3); }
    chan_t *c = chan(A32(1));
    if (c) c->paused = 1;
    RET(0, 3);
}
static void q_QSWaveMixRestartChannel(void) {
    if (A32(2) & 1) { for (int i = 0; i < NCHAN; i++) g_ch[i].paused = 0; RET(0, 3); }
    chan_t *c = chan(A32(1));
    if (c) c->paused = 0;
    RET(0, 3);
}
static void q_QSWaveMixSetVolume(void) {                 /* (session, channel, flags, volume 0..32767) */
    float v = (float)(A32(3) > 32767 ? 32767 : A32(3)) / 32767.0f;
    if (A32(2) & 1) { for (int i = 0; i < NCHAN; i++) g_ch[i].volume = v; RET(0, 4); }
    chan_t *c = chan(A32(1));
    before_setup(c);
    if (c) c->volume = v;
    RET(0, 4);
}
static void q_QSWaveMixSetFrequency(void) {              /* (session, channel, flags, hz; 0 the wave's own) */
    chan_t *c = chan(A32(1));
    before_setup(c);
    if (c) c->freq = (int)A32(3);
    RET(0, 4);
}
static void q_QSWaveMixSetSourcePosition(void) {         /* (session, channel, flags, &vector) */
    chan_t *c = chan(A32(1));
    before_setup(c);
    if (c && A32(3)) c->pos = vec_at(A32(3)), c->has_pos = 1;
    RET(0, 4);
}
static void q_QSWaveMixGetSourcePosition(void) {         /* (session, channel, &vector) */
    chan_t *c = chan(A32(1));
    if (c && A32(2)) memcpy(hle_ptr(A32(2)), &c->pos, 12);
    RET(0, 3);
}
static void q_QSWaveMixSetPosition(void) {               /* (session, channel, flags, &polar): a 2D place, centred */
    chan_t *c = chan(A32(1));
    before_setup(c);
    if (c) c->has_pos = 0;
    RET(0, 4);
}
static void q_QSWaveMixSetListenerPosition(void) {       /* (session, &vector, flags) */
    if (A32(1)) g_listener = vec_at(A32(1));
    RET(0, 3);
}
static void q_QSWaveMixSetListenerOrientation(void) {    /* (session, &front, &up, flags) */
    if (A32(1)) g_front = vec_at(A32(1));
    if (A32(2)) g_up = vec_at(A32(2));
    RET(0, 4);
}
static void q_QSWaveMixSetDistanceMapping(void) {        /* (session, channel, flags, QMIX_DISTANCES*: size, min, max, scale) */
    uint32_t d = A32(3);
    if (d) {
        float mn = fl(MEM32(d + 4)), mx = fl(MEM32(d + 8));
        if (mn > 0 && mx > mn) g_min_dist = mn, g_max_dist = mx;
        T("SetDistanceMapping min %.1f max %.1f\n", mn, mx);
    }
    RET(0, 4);
}
static void q_QSWaveMixSetListenerVelocity(void) { RET(0, 3); }
static void q_QSWaveMixSetSourceCone(void) { RET(0, 6); }
static void q_QSWaveMixSetSpeedOfSound(void) { RET(0, 3); }
static void q_QSWaveMixSetSpeakerPlacement(void) { RET(0, 2); }
static void q_QSWaveMixSetOptions(void) { RET(0, 3); }
static void q_QSWaveMixSetPanRate(void) { RET(0, 4); }
static void q_QSWaveMixGetDirectSoundBuffer(void) {      /* none: the mix is ours */
    if (A32(2)) MEM32(A32(2)) = 0;
    RET(MMSYSERR_ERROR, 3);
}

const win32hle_shim tpw_qmixer_shims[] = {
    { "QSWaveMixInitEx", q_QSWaveMixInitEx }, { "QSWaveMixActivate", q_QSWaveMixActivate },
    { "QSWaveMixCloseSession", q_QSWaveMixCloseSession }, { "QSWaveMixOpenChannel", q_QSWaveMixOpenChannel },
    { "QSWaveMixConfigureChannel", q_QSWaveMixConfigureChannel }, { "QSWaveMixEnableChannel", q_QSWaveMixEnableChannel },
    { "QSWaveMixOpenWaveEx", q_QSWaveMixOpenWaveEx }, { "QSWaveMixFreeWave", q_QSWaveMixFreeWave },
    { "QSWaveMixPlayEx", q_QSWaveMixPlayEx }, { "QSWaveMixStopChannel", q_QSWaveMixStopChannel },
    { "QSWaveMixPauseChannel", q_QSWaveMixPauseChannel }, { "QSWaveMixRestartChannel", q_QSWaveMixRestartChannel },
    { "QSWaveMixSetVolume", q_QSWaveMixSetVolume }, { "QSWaveMixSetFrequency", q_QSWaveMixSetFrequency },
    { "QSWaveMixSetSourcePosition", q_QSWaveMixSetSourcePosition },
    { "QSWaveMixGetSourcePosition", q_QSWaveMixGetSourcePosition },
    { "QSWaveMixSetPosition", q_QSWaveMixSetPosition },
    { "QSWaveMixSetListenerPosition", q_QSWaveMixSetListenerPosition },
    { "QSWaveMixSetListenerOrientation", q_QSWaveMixSetListenerOrientation },
    { "QSWaveMixSetListenerVelocity", q_QSWaveMixSetListenerVelocity },
    { "QSWaveMixSetDistanceMapping", q_QSWaveMixSetDistanceMapping },
    { "QSWaveMixSetSourceCone", q_QSWaveMixSetSourceCone }, { "QSWaveMixSetSpeedOfSound", q_QSWaveMixSetSpeedOfSound },
    { "QSWaveMixSetSpeakerPlacement", q_QSWaveMixSetSpeakerPlacement },
    { "QSWaveMixSetOptions", q_QSWaveMixSetOptions }, { "QSWaveMixSetPanRate", q_QSWaveMixSetPanRate },
    { "QSWaveMixGetDirectSoundBuffer", q_QSWaveMixGetDirectSoundBuffer },
    { 0, 0 }
};
