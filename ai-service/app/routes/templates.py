"""模板库系统 — 风格模板 CRUD + 初始化 + 复用。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Query
from pydantic import BaseModel

from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["templates"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TemplateCreate(BaseModel):
    template_name: str
    cover_img: str = ""
    style_tags: str = ""
    lyric_template: str = ""
    music_prompt: str = ""
    mv_prompt: str = ""


class TemplateUpdate(BaseModel):
    template_name: Optional[str] = None
    cover_img: Optional[str] = None
    style_tags: Optional[str] = None
    lyric_template: Optional[str] = None
    music_prompt: Optional[str] = None
    mv_prompt: Optional[str] = None
    is_active: Optional[int] = None


# ---------------------------------------------------------------------------
# 内置模板封面 SVG 生成器
# ---------------------------------------------------------------------------

import base64

_COVER_PALETTES = [
    ("#4a1a2c","#c9a96e"),  # 古风：深红→金
    ("#1a1a4a","#ff6b9d"),  # 流行：深蓝→粉
    ("#1a1a1a","#f59e0b"),  # 说唱：黑→金
    ("#0a0a2e","#00ffff"),  # 赛博朋克：深蓝→青
    ("#1a3a2a","#a8e6cf"),  # 治愈：墨绿→薄荷
    ("#2a1a0a","#ff8c00"),  # 短剧：棕→橙
]

def _generate_svg_cover(name: str, palette_index: int) -> str:
    """生成内联 SVG 渐变封面并返回 data: URI。"""
    c1, c2 = _COVER_PALETTES[palette_index % len(_COVER_PALETTES)]
    initial = (name or "♪")[0]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">'
        f'<defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">'
        f'<stop offset="0%" style="stop-color:{c1}"/>'
        f'<stop offset="100%" style="stop-color:{c2}"/></linearGradient></defs>'
        f'<rect width="400" height="300" fill="url(#g)"/>'
        f'<text x="200" y="160" text-anchor="middle" dominant-baseline="central" '
        f'font-size="80" font-weight="bold" fill="rgba(255,255,255,0.15)" '
        f'font-family="sans-serif">{initial}</text>'
        f'<circle cx="320" cy="60" r="40" fill="rgba(255,255,255,0.05)"/>'
        f'<circle cx="80" cy="240" r="30" fill="rgba(255,255,255,0.05)"/>'
        f'</svg>'
    )
    encoded = base64.b64encode(svg.encode()).decode()
    return f"data:image/svg+xml;base64,{encoded}"


# ---------------------------------------------------------------------------
# 6 套内置免费风格模板
# ---------------------------------------------------------------------------

BUILTIN_TEMPLATES = [
    {
        "template_name": "古风国风",
        "cover_img": _generate_svg_cover("古风国风", 0),
        "style_tags": "古风,国风,民乐",
        "lyric_template": (
            "【主歌】\n"
            "（四言/五言古风句式，每句押韵）\n"
            "\n"
            "【副歌】\n"
            "（重复主题，情感升华，加入典故意象）\n"
            "\n"
            "【桥段】\n"
            "（过渡段落，节奏变化）\n"
            "\n"
            "【尾声】\n"
            "（渐弱收尾，余韵悠长）"
        ),
        "music_prompt": (
            "Style: Chinese traditional folk with modern orchestration. "
            "Instruments: guzheng, pipa, erhu, bamboo flute, soft strings, light percussion. "
            "Tempo: 70-80 BPM. Key: pentatonic scale (C major pentatonic). "
            "Structure: intro → verse → chorus → interlude → verse → chorus → bridge → outro."
        ),
        "mv_prompt": (
            "Ancient Chinese ink-wash painting style. Misty mountains, flowing waterfalls, "
            "cherry blossom petals drifting in wind. A lone figure in hanfu standing on a cliff overlooking "
            "a vast valley. Soft morning light filtering through clouds. Crane birds flying across the sky. "
            "Color palette: muted greens, grays, soft pinks, and gold accents."
        ),
    },
    {
        "template_name": "流行甜歌",
        "cover_img": _generate_svg_cover("流行甜歌", 1),
        "style_tags": "流行,甜歌,情歌",
        "lyric_template": (
            "【主歌】\n"
            "（叙事开场，日常场景引入，每句8-10字）\n"
            "\n"
            "【预副歌】\n"
            "（情绪递进，重复性句式）\n"
            "\n"
            "【副歌】\n"
            "（记忆点旋律，直白情感表达）\n"
            "\n"
            "【副歌重复】\n"
            "（情感加强）\n"
            "\n"
            "【尾声】\n"
            "（轻声呢喃收尾）"
        ),
        "music_prompt": (
            "Style: Sweet pop with acoustic elements. "
            "Instruments: acoustic guitar, piano, light synth pad, soft drum beat, bass. "
            "Tempo: 90-100 BPM. Key: D major / B minor. "
            "Structure: intro → verse → pre-chorus → chorus → verse → pre-chorus → chorus → bridge → chorus → outro."
        ),
        "mv_prompt": (
            "Warm, cozy urban atmosphere. Sunlight streaming through cafe windows. "
            "A couple sharing a moment in a park, autumn leaves falling. "
            "Soft focus lens, golden hour lighting. Pastel color palette with warm tones. "
            "Handwritten love notes, polaroid photos, string lights. "
            "Style: Korean drama aesthetic with soft bokeh effects."
        ),
    },
    {
        "template_name": "说唱嘻哈",
        "cover_img": _generate_svg_cover("说唱嘻哈", 2),
        "style_tags": "说唱,嘻哈,Rap",
        "lyric_template": (
            "【Intro】\n"
            "（简短hook，节奏感开场）\n"
            "\n"
            "【Verse 1】\n"
            "（16小节，叙事/炫技，句尾押韵）\n"
            "\n"
            "【Hook】\n"
            "（重复副歌，洗脑旋律）\n"
            "\n"
            "【Verse 2】\n"
            "（16小节，主题深化）\n"
            "\n"
            "【Bridge】\n"
            "（节奏转换，情绪爆发）\n"
            "\n"
            "【Outro】\n"
            "（渐弱，ad-libs）"
        ),
        "music_prompt": (
            "Style: Modern hip-hop / trap with 808 bass. "
            "Instruments: 808 kick drum, hi-hat rolls, synth bass, brass stabs, vocal chops. "
            "Tempo: 140-150 BPM. Key: C minor / A-flat major. "
            "Production: heavy sidechain compression, reverb on vocals, ad-libs throughout. "
            "Structure: intro → verse 1 → hook → verse 2 → hook → bridge → hook → outro."
        ),
        "mv_prompt": (
            "Urban street style cinematography. Neon-lit city streets at night. "
            "Graffiti walls, skate park, basketball court. Low-angle shots, slow-motion walking. "
            "Color grading: high contrast, blue and orange tones. "
            "Quick cuts, glitch effects, lens flares. "
            "Style: modern music video aesthetic with kinetic typography overlays."
        ),
    },
    {
        "template_name": "赛博朋克电子",
        "cover_img": _generate_svg_cover("赛博朋克电子", 3),
        "style_tags": "赛博朋克,电子,科幻",
        "lyric_template": (
            "【Verse】\n"
            "（科技意象，未来感描述）\n"
            "\n"
            "【Chorus】\n"
            "（强烈律动，合成器主导）\n"
            "\n"
            "【Drop】\n"
            "（纯电子段落，无歌词）\n"
            "\n"
            "【Verse 2】\n"
            "（主题深化）\n"
            "\n"
            "【Outro】\n"
            "（渐弱，电子音效）"
        ),
        "music_prompt": (
            "Style: Cyberpunk electronic / synthwave. "
            "Instruments: analog synth bass, arpeggiator, distorted 808 drums, reverb-heavy pads. "
            "Tempo: 120-130 BPM. Key: E minor / G major. "
            "Production: heavy distortion, filter sweeps, delay effects. "
            "Structure: intro → build-up → drop → verse → build-up → drop → breakdown → final drop → outro."
        ),
        "mv_prompt": (
            "Cyberpunk cityscape. Rain-slicked neon streets, holographic billboards. "
            "Flying vehicles between skyscrapers. A figure in futuristic gear walking through "
            "a crowded night market. Glowing neon signs in pink, cyan, and purple. "
            "Fog machines, laser beams, digital glitch effects. "
            "Style: Blade Runner aesthetic with dark, moody color grading."
        ),
    },
    {
        "template_name": "治愈轻音乐",
        "cover_img": _generate_svg_cover("治愈轻音乐", 4),
        "style_tags": "治愈,轻音乐,纯音乐,放松",
        "lyric_template": (
            "（纯器乐/吟唱，无歌词或极少歌词）\n"
            "\n"
            "【A段】\n"
            "（钢琴主旋律，轻柔进入）\n"
            "\n"
            "【B段】\n"
            "（弦乐加入，情绪渐起）\n"
            "\n"
            "【C段】\n"
            "（全乐器合奏，情感高潮）\n"
            "\n"
            "【尾声】\n"
            "（回归钢琴，渐弱至静）"
        ),
        "music_prompt": (
            "Style: Healing instrumental / ambient piano. "
            "Instruments: grand piano, soft strings (violin, cello), light pads, nature sounds (rain, birds). "
            "Tempo: 60-70 BPM. Key: C major / A minor. "
            "Production: minimal reverb, natural dynamics, gentle crescendos. "
            "Structure: piano intro → string entry → full ensemble → piano solo → quiet outro."
        ),
        "mv_prompt": (
            "Nature landscape cinematography. Sunrise over a calm lake, mist rising from the water. "
            "Forest path with dappled sunlight, autumn leaves falling. "
            "Gentle waves on a secluded beach. Time-lapse of clouds moving across mountains. "
            "Macro shots of dewdrops on leaves. Warm, soft color grading. "
            "Style: Studio Ghibli-inspired natural beauty, slow-paced transitions."
        ),
    },
    {
        "template_name": "短剧BGM",
        "cover_img": _generate_svg_cover("短剧BGM", 5),
        "style_tags": "短剧,BGM,短视频,轻快",
        "lyric_template": (
            "【短副歌】\n"
            "（8-12秒记忆点，直接进入高潮）\n"
            "\n"
            "【Verse】\n"
            "（16秒，快速叙事）\n"
            "\n"
            "【副歌重复】\n"
            "（8-12秒）\n"
            "\n"
            "【End】\n"
            "（戛然而止或渐弱）"
        ),
        "music_prompt": (
            "Style: Short-form video BGM / commercial jingle. "
            "Instruments: ukulele, claps, synth bell, light percussion, bass. "
            "Tempo: 110-120 BPM. Key: G major / E minor. "
            "Production: bright, compressed, loop-friendly. "
            "Structure: quick intro → hook → verse → hook → outro (total 30-45 seconds)."
        ),
        "mv_prompt": (
            "Fast-paced short-form video style. Quick cuts every 2-3 seconds. "
            "Bright, saturated colors. Lifestyle scenes: coffee making, city walking, "
            "friends laughing, food plating. Top-down flat lay shots. "
            "Trendy transitions: swipe, zoom, spin. "
            "Style: TikTok / Instagram Reels aesthetic with text overlays."
        ),
    },
]


# ---------------------------------------------------------------------------
# 初始化内置模板（启动时自动执行）
# ---------------------------------------------------------------------------

async def init_builtin_templates():
    """首次启动时写入 6 套内置模板（幂等），已有模板自动补充封面。"""
    db = await get_db()
    cur = await db.execute("SELECT COUNT(*) FROM templates")
    count = (await cur.fetchone())[0]
    if count == 0:
        for tpl in BUILTIN_TEMPLATES:
            await db.execute(
                """INSERT INTO templates (template_name, cover_img, style_tags, lyric_template, music_prompt, mv_prompt, is_active)
                   VALUES (?, ?, ?, ?, ?, ?, 1)""",
                (tpl["template_name"], tpl["cover_img"], tpl["style_tags"],
                 tpl["lyric_template"], tpl["music_prompt"], tpl["mv_prompt"]),
            )
    else:
        # 已有模板时，仅补充 cover_img 为空的内置模板封面（前 6 条）
        for idx, tpl in enumerate(BUILTIN_TEMPLATES):
            await db.execute(
                "UPDATE templates SET cover_img = ? WHERE id = ? AND (cover_img IS NULL OR cover_img = '')",
                (tpl["cover_img"], idx + 1),
            )
    await db.commit()


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


@router.get("/templates/list")
async def list_all_templates(
    style: Optional[str] = Query(default=None),
    limit: int = Query(default=20, le=100),
    offset: int = Query(default=0),
):
    """分页查询所有公开活跃模板。"""
    db = await get_db()
    where = ["is_active = 1"]
    params: list = []
    if style:
        where.append("style_tags LIKE ?")
        params.append(f"%{style}%")

    where_sql = " AND ".join(where)

    cur = await db.execute(
        f"SELECT * FROM templates WHERE {where_sql} ORDER BY id ASC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    rows = await cur.fetchall()
    items = [dict(r) for r in rows]

    cnt = await db.execute(
        f"SELECT COUNT(*) FROM templates WHERE {where_sql}", params
    )
    total = (await cnt.fetchone())[0]

    return {
        "success": True,
        "items": items,
        "pagination": {"total": total, "limit": limit, "offset": offset},
    }


@router.get("/templates/{template_id}")
async def get_template_detail(template_id: int):
    """获取单条模板完整参数。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, detail="Template not found")
    return {"success": True, "data": dict(row)}


# ---------------------------------------------------------------------------
# 复用模板 — 一键填创作页
# ---------------------------------------------------------------------------


@router.post("/creations/use-template/{template_id}")
async def use_template(template_id: int, user_id: int = Form(default=1)):
    """复用模板：预填歌词模板+曲风提示词+MV分镜，跳转创作页。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM templates WHERE id = ? AND is_active = 1", (template_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, detail="Template not found or inactive")

    tpl = dict(row)
    return {
        "success": True,
        "data": {
            "template_id": tpl["id"],
            "template_name": tpl["template_name"],
            "style_tags": tpl["style_tags"],
            "lyric_template": tpl["lyric_template"],
            "music_prompt": tpl["music_prompt"],
            "mv_prompt": tpl["mv_prompt"],
            "redirect": "/create",
        },
    }


# ---------------------------------------------------------------------------
# 用户端 — 从创作保存为模板
# ---------------------------------------------------------------------------


@router.post("/creations/{creation_id}/save-template")
async def save_as_template(creation_id: int, user_id: int = Form(default=1)):
    """将已有创作保存为模板（任何人均可保存）。"""
    db = await get_db()
    cur = await db.execute("SELECT * FROM ai_creations WHERE id = ?", (creation_id,))
    row = await cur.fetchone()
    if not row:
        raise HTTPException(404, detail="Creation not found")
    src = dict(row)
    await db.execute(
        """INSERT INTO templates
           (template_name, cover_img, style_tags, lyric_template, music_prompt, mv_prompt, is_active)
           VALUES (?, ?, ?, ?, ?, ?, 0)""",
        (
            src.get("title") or f"Remix of #{creation_id}",
            src.get("cover_url") or "",
            src.get("style_tags") or "",
            src.get("lyrics") or "",
            src.get("prompt_text") or "",
            "",  # mv_prompt not available from creation
        ),
    )
    await db.commit()
    return {"success": True, "message": "Template saved (pending admin review)"}


# ---------------------------------------------------------------------------
# 管理后台接口
# ---------------------------------------------------------------------------


@router.get("/admin/templates")
async def admin_list_templates(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0),
    show_inactive: bool = Query(default=False),
):
    """管理后台：模板列表（含下线的）。"""
    db = await get_db()
    where = [] if show_inactive else ["is_active = 1"]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur = await db.execute(
        f"SELECT * FROM templates {where_sql} ORDER BY id ASC LIMIT ? OFFSET ?",
        (limit, offset),
    )
    rows = await cur.fetchall()
    items = [dict(r) for r in rows]

    cnt = await db.execute(f"SELECT COUNT(*) FROM templates {where_sql}")
    total = (await cnt.fetchone())[0]

    return {"items": items, "pagination": {"total": total, "limit": limit, "offset": offset}}


@router.post("/admin/templates")
async def admin_create_template(body: TemplateCreate):
    """新增模板。"""
    db = await get_db()
    cur = await db.execute(
        """INSERT INTO templates (template_name, cover_img, style_tags, lyric_template, music_prompt, mv_prompt)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (body.template_name, body.cover_img, body.style_tags,
         body.lyric_template, body.music_prompt, body.mv_prompt),
    )
    await db.commit()
    return {"success": True, "id": cur.lastrowid}


@router.put("/admin/templates/{template_id}")
async def admin_update_template(template_id: int, body: TemplateUpdate):
    """编辑模板。"""
    db = await get_db()
    existing = await db.execute("SELECT id FROM templates WHERE id = ?", (template_id,))
    if not await existing.fetchone():
        raise HTTPException(404, detail="Template not found")

    fields = []
    params = []
    for key in ("template_name", "cover_img", "style_tags", "lyric_template", "music_prompt", "mv_prompt", "is_active"):
        val = getattr(body, key, None)
        if val is not None:
            fields.append(f"{key} = ?")
            params.append(val)

    if not fields:
        return {"success": True, "message": "No changes"}

    params.append(template_id)
    await db.execute(
        f"UPDATE templates SET {', '.join(fields)} WHERE id = ?",
        params,
    )
    await db.commit()
    return {"success": True}


@router.put("/admin/templates/{template_id}/audit")
async def admin_audit_template(template_id: int, audit_status: str = Form(...)):
    """审核模板：pass → is_active=1，reject → is_active=0。"""
    if audit_status not in ("pass", "reject"):
        raise HTTPException(400, detail="audit_status must be 'pass' or 'reject'")
    db = await get_db()
    existing = await db.execute("SELECT id FROM templates WHERE id = ?", (template_id,))
    if not await existing.fetchone():
        raise HTTPException(404, detail="Template not found")
    is_active = 1 if audit_status == "pass" else 0
    await db.execute("UPDATE templates SET is_active = ? WHERE id = ?", (is_active, template_id))
    await db.commit()
    return {"success": True, "is_active": is_active, "message": "Template audited"}


@router.delete("/admin/templates/{template_id}")
async def admin_delete_template(template_id: int):
    """删除模板。"""
    db = await get_db()
    await db.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    await db.commit()
    return {"success": True}
