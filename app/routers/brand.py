"""Endpoints de la voz de marca (W-04, CRI-563).

GET /brand-voice   devuelve el perfil de marca vigente (propio o default)
PUT /brand-voice   guarda/actualiza el perfil de marca (single-tenant)

El perfil se inyecta automáticamente en cada repropósito (ver services/repurpose).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..services import brand

router = APIRouter(tags=["brand"])


@router.get("/brand-voice")
def get_brand_voice() -> dict:
    """Devuelve el perfil de marca vigente y si es propio o el default."""
    return {"customized": brand.is_customized(), "voice": brand.load_brand_voice()}


@router.put("/brand-voice")
def put_brand_voice(voice: str = Body(..., embed=True)) -> dict:
    """Guarda el perfil de marca. Body: {"voice": "<markdown de 1 página>"}."""
    try:
        saved = brand.save_brand_voice(voice)
    except brand.BrandError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"customized": True, "voice": saved}
