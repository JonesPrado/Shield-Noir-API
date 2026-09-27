from fastapi import APIRouter
from app.services.comic_vine import (
    get_random_marvel_characters
)
from app.routes.ai import generate_case as generate_investigation_case

router = APIRouter()

@router.post("/case/generate")
async def generate_case():
    characters = await get_random_marvel_characters()
    
    investigation_case = await generate_investigation_case(characters)
    
    return investigation_case