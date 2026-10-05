"""Build factual questions from the structured Comic Vine profile fields."""

import re
import unicodedata

from app.schemas import Question, SuspectAnswer


_ORIGIN_LABELS = {
    "human": "humana",
    "human mutate": "humana alterada",
    "mutant": "mutante",
    "alien": "extraterrestre",
    "asgardian": "asgardiana",
    "god": "divina",
    "robot": "artificial",
    "cyborg": "cibernética",
    "animal": "animal",
    "inhuman": "inumana",
    "demon": "demoníaca",
    "cosmic being": "cósmica",
    "cosmic entity": "cósmica",
    "radiation": "radioativa",
}

_TEAM_LABELS = {
    "avengers": "os Vingadores",
    "new avengers": "os Novos Vingadores",
    "young avengers": "os Jovens Vingadores",
    "west coast avengers": "os Vingadores da Costa Oeste",
    "secret avengers": "os Vingadores Secretos",
    "x men": "os X-Men",
    "fantastic four": "o Quarteto Fantástico",
    "guardians of the galaxy": "os Guardiões da Galáxia",
    "defenders": "os Defensores",
    "inhumans": "os Inumanos",
    "thunderbolts": "os Thunderbolts",
    "shield": "a S.H.I.E.L.D.",
    "s h i e l d": "a S.H.I.E.L.D.",
    "hydra": "a HYDRA",
    "brotherhood of evil mutants": "a Irmandade de Mutantes",
    "horsemen of apocalypse": "os Cavaleiros do Apocalipse",
    "marauders": "os Carrascos",
    "the hand": "o Tentáculo",
    "weapon x": "a Arma X",
    "outlaw avengers": "os Vingadores Fora da Lei",
}

# Only exact, recognizable Comic Vine power names are used. Unknown labels are
# omitted instead of guessed or translated by the model.
_POWER_LABELS = {
    "flight": "capacidade de voo",
    "super strength": "força sobre-humana",
    "superhuman strength": "força sobre-humana",
    "super speed": "supervelocidade",
    "telepathy": "telepatia",
    "telekinesis": "telecinese",
    "energy projection": "projeção de energia",
    "energy manipulation": "manipulação de energia",
    "weather manipulation": "controle climático",
    "weather control": "controle climático",
    "healing factor": "fator de cura acelerada",
    "healing": "capacidade de cura",
    "regeneration": "regeneração",
    "shape shifting": "metamorfose",
    "shapeshifting": "metamorfose",
    "size changing": "alteração de tamanho",
    "invisibility": "invisibilidade",
    "intangibility": "intangibilidade",
    "magic": "magia",
    "invulnerability": "invulnerabilidade",
    "superhuman durability": "resistência sobre-humana",
    "enhanced durability": "resistência sobre-humana",
    "wallcrawling": "aderência a superfícies",
    "electrokinesis": "manipulação de eletricidade",
    "electrical manipulation": "manipulação de eletricidade",
    "ice control": "manipulação de gelo",
    "fire control": "manipulação de fogo",
    "elasticity": "elasticidade corporal",
    "precognition": "precognição",
    "enhanced senses": "sentidos aprimorados",
    "matter manipulation": "manipulação da matéria",
    "intellect": "intelecto excepcional",
    "weapon master": "maestria com armas",
    "escape artist": "habilidade de fuga",
    "tracking": "rastreamento avançado",
    "adaptive": "adaptação sobre-humana",
    "marksmanship": "pontaria excepcional",
    "stealth": "furtividade",
    "leadership": "liderança",
    "longevity": "longevidade",
    "super hearing": "audição sobre-humana",
    "gadgets": "uso de dispositivos tecnológicos",
    "swordsmanship": "esgrima",
    "teleport": "teletransporte",
    "blast power": "disparos de energia",
    "immortal": "imortalidade",
    "cosmic awareness": "percepção cósmica",
    "psionic": "poderes psiônicos",
    "super sight": "visão sobre-humana",
}


def _mapping(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value if isinstance(value, dict) else {}


def _normalize(value: str) -> str:
    plain = "".join(
        char
        for char in unicodedata.normalize("NFKD", value.lower())
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", plain).strip()


def _name(value) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    elif hasattr(value, "model_dump"):
        value = _mapping(value).get("name")
    return value.strip() if isinstance(value, str) else ""


def _question_rows(characters: list) -> list[tuple[str, set[int]]]:
    profiles = [_mapping(character) for character in characters]
    ids = [profile.get("id") for profile in profiles]
    if len(profiles) != 10 or any(not isinstance(value, int) for value in ids):
        return []

    candidates: list[tuple[str, set[int]]] = []

    # Origin is categorical; do not turn missing or unrecognized values into NO.
    origins = [_name(profile.get("origin")) for profile in profiles]
    origin_keys = [_normalize(value) for value in origins]
    if all(key in _ORIGIN_LABELS for key in origin_keys):
        for key, label in _ORIGIN_LABELS.items():
            members = {ids[index] for index, value in enumerate(origin_keys) if value == key}
            if 3 <= len(members) <= 7:
                candidates.append((
                    f"A origem do suspeito é classificada como {label}?",
                    members,
                ))

    # Team and power lists are used only when Comic Vine returned the field for
    # every suspect. This avoids treating absent profile data as a negative fact.
    if all(isinstance(profile.get("teams"), list) for profile in profiles):
        teams_by_id = [
            {_normalize(_name(team)): _name(team) for team in profile["teams"] if _name(team)}
            for profile in profiles
        ]
        team_keys = set().union(*(set(teams) for teams in teams_by_id))
        for key in team_keys:
            members = {
                ids[index] for index, teams in enumerate(teams_by_id) if key in teams
            }
            if not 3 <= len(members) <= 7:
                continue
            original = next(teams[key] for teams in teams_by_id if key in teams)
            label = _TEAM_LABELS.get(key)
            text = (
                f"O suspeito já integrou {label}?"
                if label
                else f"O histórico do suspeito registra vínculo com o grupo \"{original}\"?"
            )
            candidates.append((text, members))

    if all(isinstance(profile.get("powers"), list) for profile in profiles):
        powers_by_id = [
            {_normalize(_name(power)) for power in profile["powers"] if _name(power)}
            for profile in profiles
        ]
        members_by_label: dict[str, set[int]] = {}
        for index, powers in enumerate(powers_by_id):
            for key in powers:
                label = _POWER_LABELS.get(key)
                if label:
                    members_by_label.setdefault(label, set()).add(ids[index])
        for label, members in members_by_label.items():
            if 3 <= len(members) <= 7:
                candidates.append((
                    f"O perfil de habilidades do suspeito inclui {label}?",
                    members,
                ))

    # Keep the order stable. The prompt exposes these rows as PROFILE_FACTS and
    # the response is resolved again after the provider returns; changing the
    # order between those two passes would make profile:N point to a different
    # fact and silently corrupt the answers.
    unique: list[tuple[str, set[int]]] = []
    seen_splits: set[tuple[int, ...]] = set()
    for text, members in candidates:
        yes = tuple(sorted(members))
        no = tuple(sorted(set(ids) - members))
        # The same binary split can be phrased from either side. Canonicalize
        # both 5/5 partitions as well as unequal splits before deduplicating.
        split = min(yes, no)
        if split in seen_splits:
            continue
        seen_splits.add(split)
        unique.append((text, members))
    return unique


def build_profile_questions(characters: list) -> list[Question]:
    """Create the full 10-answer table deterministically from profile facts."""
    profiles = [_mapping(character) for character in characters]
    ids = [profile.get("id") for profile in profiles]
    rows = _question_rows(characters)
    return [
        Question(
            id=index,
            text=text,
            answers=[
                SuspectAnswer(suspect_id=suspect_id, answer=suspect_id in members)
                for suspect_id in ids
            ],
        )
        for index, (text, members) in enumerate(rows, 1)
    ]
