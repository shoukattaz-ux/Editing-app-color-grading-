"""Ready-made looks: tested blueprints a user can add with one click (no JSON needed).

Every template is an ordinary blueprint (the same format an AI would write), so it is validated by the same registry-driven checks and can be
edited afterwards like any other effects. They never contain files: a background is chosen by the user in the Backgrounds panel.
`scale` > 1 means "also export at that many times the source size" (used by the AI upscale looks)."""
from __future__ import annotations
from typing import Any

SECTIONS = ("Start to finish", "Green screen", "Face", "Colour", "Quality")


def _op(i: str, t: str, **p: Any) -> dict:
    return {"id": i, "type": t, "parameters": p}                  # no start/end = the whole video


_KEY = _op("key", "background_removal", mode="screen", hair_detail=0.6)        # professional green-screen key; the AI restores fine hair strands at the edge
_BG = _op("bg", "background_replacement", color="#0b0b2e", fit="cover", subject_anchor="bottom", contact_shadow=0.3, reflection=0.15)
_FACE = [_op("fr", "face_restoration", strength=0.5), _op("skin", "skin_retouch", amount=0.4), _op("tone", "face_tone_correction"),
         _op("light", "face_relighting", key_intensity=0.4)]
_CINE = _op("grade", "color_grading", cinematic=0.6, cinematic_style="teal_orange", saturation=1.1, contrast=1.05)
_STUDIO = _op("look", "studio_look")


def _t(id: str, section: str, title: str, text: str, ops: list[dict], needs: str = "", scale: int = 1) -> dict:
    return {"id": id, "section": section, "title": title, "description": text, "needs": needs, "scale": scale,
            "blueprint": {"blueprint_version": "1.0", "project": {"name": title}, "assets": [], "operations": ops}}


TEMPLATES: list[dict] = [
    _t("club_night_full", "Start to finish", "Club night: green screen to finished video",
       "The complete look: removes the green (fine hair strands included), puts the person in a club scene, restores and retouches the face, "
       "adds a cinematic grade and a finishing look. Then pick your own club picture or video in Backgrounds.",
       [_KEY, _BG, _op("clean", "video_enhancement", denoise=0.3, sharpen=0.2), *_FACE, _CINE, _STUDIO], "A green-screen video"),
    _t("club_night_hd", "Start to finish", "Club night + 2x AI upscale (best quality, slow)",
       "The complete club look and the picture is also rebuilt at twice the size (for example 720p becomes 1440p). "
       "Slow on a computer without a strong graphics card.",
       [_KEY, _BG, _op("clean", "video_enhancement", denoise=0.3, sharpen=0.2), *_FACE, _CINE, _STUDIO,
        _op("up", "ai_upscale", model="general", quality="fast")], "A green-screen video", 2),
    _t("clean_green_cutout", "Green screen", "Clean green-screen cut-out",
       "Removes the green and keeps the fine hair strands. Add any background afterwards.", [_KEY], "A green-screen video"),
    _t("on_dark_floor", "Green screen", "On a dark stage floor",
       "Cut-out on a dark colour with a soft shadow and a faint reflection under the feet, like a stage floor.",
       [_KEY, _op("bg", "background_replacement", color="#10102a", fit="cover", subject_anchor="bottom", contact_shadow=0.5, reflection=0.25, shadow=0.2)],
       "A green-screen video"),
    _t("club_light_boxes", "Green screen", "Club lights: three coloured boxes",
       "Red from the left, blue from the right and gold from above, each in its own box on the subject. Drag the boxes on the preview to place them. Needs a background.",
       [_KEY, _BG, _op("look", "studio_look", preset="none", ambient_match=0.5, colour_light=0.9, colour_light_side_a="off", colour_light_side_b="off", colour_zones=[
           {"x0": 0.15, "y0": 0.1, "x1": 0.5, "y1": 0.95, "color": "#ff2a55", "side": "left", "reach": 0.7, "intensity": 1.0, "soft": 0.3},
           {"x0": 0.5, "y0": 0.1, "x1": 0.85, "y1": 0.95, "color": "#19b8ff", "side": "right", "reach": 0.7, "intensity": 1.0, "soft": 0.3},
           {"x0": 0.3, "y0": 0.0, "x1": 0.7, "y1": 0.5, "color": "#ffc21a", "side": "top", "reach": 0.6, "intensity": 0.8, "soft": 0.4}])],
       "A green-screen video"),
    _t("natural_portrait", "Face", "Natural portrait",
       "Restores detail, smooths skin without making it plastic, evens the face brightness and adds a soft key light.", _FACE, "A visible face"),
    _t("skin_glow", "Face", "Soft skin glow",
       "Stronger skin smoothing (texture is kept) with warm, even face colour.",
       [_op("skin", "skin_retouch", amount=0.65, texture=0.4), _op("tone", "face_tone_correction", warmth=0.1)], "A visible face"),
    _t("cinematic_teal_orange", "Colour", "Cinematic teal and orange", "The classic film look: warm skin against teal shadows.",
       [_op("grade", "color_grading", cinematic=0.7, cinematic_style="teal_orange", contrast=1.05)]),
    _t("moody_night", "Colour", "Moody night", "Darker, cooler and more contrasty: good for clubs and night scenes.",
       [_op("grade", "color_grading", cinematic=0.7, cinematic_style="moody", saturation=1.05)]),
    _t("golden_hour", "Colour", "Golden hour", "Warm, glowing sunset colours.",
       [_op("grade", "color_grading", cinematic=0.6, cinematic_style="golden")]),
    _t("punchy_clean", "Colour", "Clean and punchy", "Automatic correction of exposure and colour with a crisp finish. Good first step for any footage.",
       [_op("grade", "color_grading", auto_enhance=0.6, enhance_style="crisp")]),
    _t("clean_sharp", "Quality", "Clean and sharp", "Reduces camera noise and adds light sharpening.",
       [_op("clean", "video_enhancement", denoise=0.3, sharpen=0.25)]),
    _t("upscale_2x", "Quality", "2x AI upscale", "Rebuilds the picture at twice the size with AI. Slow without a strong graphics card.",
       [_op("up", "ai_upscale", model="general", quality="fast")], "", 2),
]
