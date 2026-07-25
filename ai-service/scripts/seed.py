"""Seed the SQLite database with sample artists, albums, and tracks."""
from __future__ import annotations

import asyncio
import sys
import os

# Ensure ai-service/ is on the path so `app.database` can be imported
_svc = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _svc)

from app.database import init_db, get_db

# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

SEED_ARTISTS = [
    {"name": "The Cosmic Waves", "slug": "cosmic-waves", "bio_md": "Independent synthwave band from Tokyo.", "avatar_url": "https://picsum.photos/seed/cosmic/200"},
    {"name": "Luna Eclipse", "slug": "luna-eclipse", "bio_md": "Ambient electronic producer. Known for ethereal soundscapes.", "avatar_url": "https://picsum.photos/seed/luna/200"},
    {"name": "Beat Mechanics", "slug": "beat-mechanics", "bio_md": "Berlin-based techno collective.", "avatar_url": "https://picsum.photos/seed/beat/200"},
]

SEED_ALBUMS = [
    # Artist 1: Cosmic Waves
    {"artist_id": 1, "title": "Neon Nights", "slug": "neon-nights", "release_date": "2026-01-15", "cover_url": "https://picsum.photos/seed/neon/400"},
    {"artist_id": 1, "title": "Solar Drift", "slug": "solar-drift", "release_date": "2026-05-20", "cover_url": "https://picsum.photos/seed/solar/400"},
    # Artist 2: Luna Eclipse
    {"artist_id": 2, "title": "Silent Horizons", "slug": "silent-horizons", "release_date": "2025-11-01", "cover_url": "https://picsum.photos/seed/silent/400"},
    {"artist_id": 2, "title": "Dreamscape", "slug": "dreamscape", "release_date": "2026-03-10", "cover_url": "https://picsum.photos/seed/dream/400"},
    # Artist 3: Beat Mechanics
    {"artist_id": 3, "title": "Concrete Pulse", "slug": "concrete-pulse", "release_date": "2026-02-01", "cover_url": "https://picsum.photos/seed/concrete/400"},
]

SEED_TRACKS = [
    # Album 1 (Neon Nights) — 4 tracks
    {"album_id": 1, "title": "Midnight Drive",    "track_number": 1, "duration_ms": 245000, "hls_url": "seed/neon_nights/01_midnight_drive.m3u8"},
    {"album_id": 1, "title": "Chrome Sunset",     "track_number": 2, "duration_ms": 198000, "hls_url": "seed/neon_nights/02_chrome_sunset.m3u8"},
    {"album_id": 1, "title": "Pixel Rain",        "track_number": 3, "duration_ms": 312000, "hls_url": "seed/neon_nights/03_pixel_rain.m3u8"},
    {"album_id": 1, "title": "Retrograde",        "track_number": 4, "duration_ms": 267000, "hls_url": "seed/neon_nights/04_retrograde.m3u8"},
    # Album 2 (Solar Drift) — 3 tracks
    {"album_id": 2, "title": "Solar Wind",        "track_number": 1, "duration_ms": 221000, "hls_url": "seed/solar_drift/01_solar_wind.m3u8"},
    {"album_id": 2, "title": "Gravity Well",      "track_number": 2, "duration_ms": 189000, "hls_url": "seed/solar_drift/02_gravity_well.m3u8"},
    {"album_id": 2, "title": "Event Horizon",     "track_number": 3, "duration_ms": 335000, "hls_url": "seed/solar_drift/03_event_horizon.m3u8"},
    # Album 3 (Silent Horizons) — 5 tracks
    {"album_id": 3, "title": "First Light",       "track_number": 1, "duration_ms": 284000, "hls_url": "seed/silent_horizons/01_first_light.m3u8"},
    {"album_id": 3, "title": "Tidal Lock",        "track_number": 2, "duration_ms": 193000, "hls_url": "seed/silent_horizons/02_tidal_lock.m3u8"},
    {"album_id": 3, "title": "Deep Silence",      "track_number": 3, "duration_ms": 356000, "hls_url": "seed/silent_horizons/03_deep_silence.m3u8",
     "lyrics_lrc": "[00:12.50] In the deep silence I wander\n[00:18.30] Where shadows dance alone\n[00:25.10] Nothing left but echo\n[00:31.40] Of stories we once owned"},
    {"album_id": 3, "title": "Starfall",          "track_number": 4, "duration_ms": 178000, "hls_url": "seed/silent_horizons/04_starfall.m3u8"},
    {"album_id": 3, "title": "Last Transmission", "track_number": 5, "duration_ms": 401000, "hls_url": "seed/silent_horizons/05_last_transmission.m3u8"},
    # Album 4 (Dreamscape) — 3 tracks
    {"album_id": 4, "title": "Lucid",             "track_number": 1, "duration_ms": 210000, "hls_url": "seed/dreamscape/01_lucid.m3u8"},
    {"album_id": 4, "title": "Blue Haze",         "track_number": 2, "duration_ms": 167000, "hls_url": "seed/dreamscape/02_blue_haze.m3u8"},
    {"album_id": 4, "title": "Awakening",         "track_number": 3, "duration_ms": 298000, "hls_url": "seed/dreamscape/03_awakening.m3u8"},
    # Album 5 (Concrete Pulse) — 4 tracks
    {"album_id": 5, "title": "Koncrete",          "track_number": 1, "duration_ms": 275000, "hls_url": "seed/concrete_pulse/01_koncrete.m3u8"},
    {"album_id": 5, "title": "Rave Code",         "track_number": 2, "duration_ms": 192000, "hls_url": "seed/concrete_pulse/02_rave_code.m3u8"},
    {"album_id": 5, "title": "Machine Whispers",  "track_number": 3, "duration_ms": 314000, "hls_url": "seed/concrete_pulse/03_machine_whispers.m3u8",
     "lyrics_lrc": "[00:05.00] The machines whisper at dawn\n[00:12.00] Binary dreams turning on\n[00:18.50] Circuits humming our song\n[00:25.00] In the city of steel we belong"},
    {"album_id": 5, "title": "010101",            "track_number": 4, "duration_ms": 243000, "hls_url": "seed/concrete_pulse/04_010101.m3u8"},
]


async def main():
    await init_db()
    db = await get_db()

    # ── Artists ──
    for a in SEED_ARTISTS:
        await db.execute(
            "INSERT OR IGNORE INTO artists (name, slug, bio_md, avatar_url) VALUES (?, ?, ?, ?)",
            (a["name"], a["slug"], a["bio_md"], a["avatar_url"]),
        )

    # ── Albums ──
    for al in SEED_ALBUMS:
        cur = await db.execute(
            "SELECT id FROM albums WHERE artist_id=? AND slug=?",
            (al["artist_id"], al["slug"]),
        )
        if not await cur.fetchone():
            await db.execute(
                "INSERT INTO albums (artist_id, title, slug, release_date, cover_url) VALUES (?, ?, ?, ?, ?)",
                (al["artist_id"], al["title"], al["slug"], al["release_date"], al["cover_url"]),
            )

    # ── Tracks ──
    for t in SEED_TRACKS:
        cur = await db.execute(
            "SELECT id FROM tracks WHERE album_id=? AND title=?", (t["album_id"], t["title"])
        )
        if not await cur.fetchone():
            await db.execute(
                "INSERT INTO tracks (album_id, title, track_number, duration_ms, hls_url, lyrics_lrc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    t["album_id"], t["title"], t["track_number"],
                    t["duration_ms"], t["hls_url"], t.get("lyrics_lrc"),
                ),
            )
        # Update album duration/track count
        cur = await db.execute(
            "SELECT SUM(duration_ms), COUNT(*) FROM tracks WHERE album_id=?",
            (t["album_id"],),
        )
        row = await cur.fetchone()
        await db.execute(
            "UPDATE albums SET total_tracks=?, duration_ms=? WHERE id=?",
            (row[1], row[0] or 0, t["album_id"]),
        )

    await db.commit()

    # Rebuild FTS index
    await db.execute("INSERT INTO tracks_fts(tracks_fts) VALUES ('rebuild')")
    await db.commit()

    print("Seeded: {} artists, {} albums, {} tracks".format(len(SEED_ARTISTS), len(SEED_ALBUMS), len(SEED_TRACKS)))
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())