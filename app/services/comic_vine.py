import asyncio
import random
import httpx

from app.schemas import CharacterData
from app.config import COMIC_VINE_API_KEY
from app.data.marvel_characters import MARVEL_CHARACTERS

BASE_URL = "https://comicvine.gamespot.com/api/"
IMAGE_URL_FIELDS = (
    "super_url",
    "screen_large_url",
    "screen_url",
    "medium_url",
    "small_url",
    "icon_url",
)
_PROFILE_FACTS_CACHE: dict[int, dict] = {}


def _extract_image_url(character: dict) -> str | None:
    image = character.get("image")
    if not isinstance(image, dict):
        return None
    for field in IMAGE_URL_FIELDS:
        value = image.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None

def prepare_character(character: dict, catalog_entry: dict) -> CharacterData:
    """Prepares character data for the investigation case."""
    return CharacterData(
        id=character["id"],
        name=catalog_entry["name"],
        name_pt=catalog_entry["name_pt"],
        image_url=_extract_image_url(character),
        real_name=character.get("real_name"),
        deck=character.get("deck"),
        origin=character.get("origin"),
        teams=character.get("teams"),
        powers=character.get("powers"),
    )


async def _fetch_profile_facts(client: httpx.AsyncClient, character_id: int) -> dict:
    cached = _PROFILE_FACTS_CACHE.get(character_id)
    if cached is not None:
        return cached

    response = await client.get(
        f"{BASE_URL}character/4005-{character_id}/",
        params={
            "api_key": COMIC_VINE_API_KEY,
            "format": "json",
            "field_list": "id,origin,powers,teams",
        },
        headers={"User-Agent": "ShieldNoir/1.0"},
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("results")
    if payload.get("status_code") != 1 or not isinstance(result, dict):
        raise RuntimeError("Comic Vine não retornou o perfil detalhado.")

    facts = {field: result.get(field) for field in ("origin", "powers", "teams")}
    if isinstance(facts["powers"], list) and isinstance(facts["teams"], list):
        _PROFILE_FACTS_CACHE[character_id] = facts
    return facts
    
async def get_characters_by_ids(character_ids: list[int]):
    """Fetches multiple characters by their IDs in batches."""
    if not COMIC_VINE_API_KEY:
        raise RuntimeError("COMIC_VINE_API_KEY não foi definida.")

    characters = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for start in range(0, len(character_ids), 50):
            batch = character_ids[start:start + 50]

            ids = "|".join(str(character_id) for character_id in batch)

            response = await client.get(
                f"{BASE_URL}characters/",
                params={
                    "api_key": COMIC_VINE_API_KEY,
                    "format": "json",
                    "filter": f"id:{ids}",
                    "field_list": "id,name,real_name,deck,origin,powers,teams,publisher,image",
                    "limit": len(batch),
                },
                headers={
                    "User-Agent": "ShieldNoir/1.0"
                },
            )

            response.raise_for_status()
            data = response.json()

            if data.get("status_code") != 1:
                raise RuntimeError(
                    data.get("error", "Comic Vine API falhou na requisição.")
                )

            characters.extend(data["results"])

        details = await asyncio.gather(
            *(_fetch_profile_facts(client, character["id"]) for character in characters),
            return_exceptions=True,
        )
        unavailable = 0
        for character, detail in zip(characters, details):
            if isinstance(detail, Exception):
                unavailable += 1
                continue
            for field in ("origin", "powers", "teams"):
                if detail.get(field) is not None:
                    character[field] = detail[field]

        if unavailable:
            print(
                f"[Comic Vine] Perfis detalhados indisponíveis="
                f"{unavailable}/{len(characters)}"
            )

    return characters

async def get_random_marvel_characters(amount: int = 10):
    """Fetches random Marvel characters from the predefined pool."""
    catalog_by_id = {character["id"]: character for character in MARVEL_CHARACTERS}
    if amount > len(catalog_by_id):
        raise ValueError("A lista controlada não possui personagens suficientes.")

    selected_entries = random.sample(list(catalog_by_id.values()), amount)
    selected_ids = [character["id"] for character in selected_entries]

    characters = await get_characters_by_ids(selected_ids)
    characters_by_id = {character["id"]: character for character in characters}
    if set(characters_by_id) != set(selected_ids):
        raise RuntimeError("Comic Vine não retornou todos os personagens selecionados.")

    return [
        prepare_character(characters_by_id[character["id"]], character)
        for character in selected_entries
    ]
