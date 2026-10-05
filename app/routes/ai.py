import asyncio
import json
import random
import re
import unicodedata
from itertools import combinations

from google import genai
from groq import AsyncGroq
from openai import AsyncOpenAI
from pydantic import BaseModel, StrictInt

from app.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)
from app.data.marvel_characters import MARVEL_CHARACTERS
from app.schemas import Clue, InvestigationCase, Question, Suspect
from app.services.question_bank import build_profile_questions


N_SUSPECTS = 10
N_QUESTIONS = 10
MIN_QUESTIONS = N_QUESTIONS
MAX_OUTPUT_TOKENS = 8192
QUESTION_CANDIDATES = 18
PROVIDER_TIMEOUT_SECONDS = 32
CASE_DEADLINE_SECONDS = 95


class _GeneratedQuestion(BaseModel):
    text: str
    basis: str


class _GeneratedCase(BaseModel):
    id: StrictInt
    culprit_id: StrictInt
    description: str
    suspects: list[Suspect]
    clues: list[Clue]
    questions: list[_GeneratedQuestion]


_STOPWORDS = {
    "a", "as", "o", "os", "um", "uma", "uns", "umas", "de", "do", "da",
    "dos", "das", "e", "ou", "em", "no", "na", "nos", "nas", "por", "para",
    "com", "sem", "que", "se", "é", "sao", "são", "tem", "possui", "pode",
    "capaz", "capacidade", "algum", "alguma", "alguns", "algumas", "tipo",
    "tipos", "suspeito", "suspeita", "foi", "ser", "estar", "estava",
}

_FALLBACK_ALIBIS = (
    "na sala de controle, conferindo o painel de segurança às 21h12",
    "no salão principal, conversando com convidados durante o blecaute",
    "no gerador do subsolo, acompanhado por um técnico às 21h14",
    "na oficina, organizando equipamentos diante de uma câmera interna",
    "na varanda leste, em uma comunicação registrada às 21h16",
    "na ala médica, auxiliando a equipe durante a queda de energia",
    "no estacionamento, registrado pelo leitor de credenciais às 21h11",
    "na cozinha, ajudando a equipe até o fim da janela do crime",
    "na biblioteca, consultando a planta do prédio às 21h13",
    "no quarto de hóspedes, em uma chamada registrada às 21h15",
)

_FALLBACK_FACTS = (
    "acesso ao perímetro interno",
    "presença registrada entre 21h10 e 21h20",
    "contato com a equipe de segurança",
    "autorização para consultar os registros",
    "conhecimento do layout do arquivo",
    "passagem pelo corredor leste",
    "acesso a ferramentas de manutenção",
    "contato anterior com o objeto desaparecido",
    "motivo documentado para estar no subsolo",
    "comunicação registrada durante o blecaute",
)
_FALLBACK_PATTERN = (0, 1, 2, 3, 5)
_FALLBACK_SCENARIOS = (
    {
        "window": "entre 21h12 e 21h16",
        "description": (
            "Entre 21h12 e 21h16, durante uma queda breve de energia, um protótipo "
            "de contenção desapareceu do arquivo subterrâneo da S.H.I.E.L.D. "
            "A porta não foi arrombada e o objeto não deixou marcas de arrasto."
        ),
        "clues": (
            "O leitor de acesso registrou uma credencial temporária na porta restrita às 21h13.",
            "A câmera do corredor perdeu sinal por 47 segundos dentro da janela do desaparecimento.",
            "O invólucro do protótipo conservou um fragmento de material usado na rota de serviço.",
        ),
    },
    {
        "window": "entre 02h08 e 02h14",
        "description": (
            "Entre 02h08 e 02h14, um módulo de navegação desapareceu do laboratório "
            "de quarentena durante uma troca de turno. O selo permaneceu intacto, "
            "mas o registro interno foi interrompido por alguns segundos."
        ),
        "clues": (
            "O sistema registrou uma abertura autorizada no corredor de quarentena às 02h11.",
            "Um sensor de movimento ficou sem leitura durante 38 segundos na troca de turno.",
            "Uma fibra azul foi encontrada no fecho interno do módulo desaparecido.",
        ),
    },
    {
        "window": "entre 18h41 e 18h47",
        "description": (
            "Entre 18h41 e 18h47, um artefato de pesquisa foi removido da câmara "
            "de conservação enquanto o prédio recebia visitantes. O alarme não tocou, "
            "e a rota de saída passou por uma área de serviço."
        ),
        "clues": (
            "O controle de portas registrou uma passagem fora da rota de visitantes às 18h44.",
            "A gravação do saguão apresenta uma lacuna de 31 segundos no momento crítico.",
            "Marcas finas na base do artefato indicam que ele foi acondicionado, não arrastado.",
        ),
    },
)
_FALLBACK_QUESTION_TEXTS = (
    "O registro coloca o suspeito dentro do perímetro durante a janela do crime?",
    "O suspeito tinha uma rota plausível até a área restrita?",
    "A credencial do suspeito poderia abrir a porta usada na operação?",
    "Há um registro independente que confirme o local alegado pelo suspeito?",
    "O álibi do suspeito cobre todo o intervalo do desaparecimento?",
    "O suspeito poderia passar pelo corredor de serviço sem ser identificado?",
    "Os recursos disponíveis ao suspeito são compatíveis com o método observado?",
    "O depoimento do suspeito coincide com os registros técnicos do prédio?",
    "O suspeito teria contato plausível com o material encontrado no objeto?",
    "Existe uma lacuna no trajeto informado pelo suspeito?",
)


def _dict(value) -> dict:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _normalize(text: str) -> str:
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.lower())
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _terms(text: str) -> set[str]:
    return {
        term for term in _normalize(text).split()
        if len(term) >= 3 and term not in _STOPWORDS
    }


def _compact_character(character) -> dict:
    data = _dict(character)
    powers = []
    for power in data.get("powers") or []:
        if isinstance(power, dict):
            name = str(power.get("name") or "").strip()
            item = name
        else:
            item = str(power).strip()
        if item:
            powers.append(item[:80])
    origin = data.get("origin")
    origin_name = origin.get("name") if isinstance(origin, dict) else None
    teams = [
        str(team.get("name") or "").strip()
        for team in (data.get("teams") or [])
        if isinstance(team, dict) and team.get("name")
    ]
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "name_pt": data.get("name_pt"),
        "origin": str(origin_name or "")[:60],
        "teams": teams[:6],
        "powers": powers[:8],
    }


def _profile_description(character: dict) -> str:
    name = character.get("name_pt") or character.get("name") or "O suspeito"
    origin = character.get("origin") or {}
    origin_name = origin.get("name") if isinstance(origin, dict) else None
    teams = [
        str(team.get("name")).strip()
        for team in (character.get("teams") or [])
        if isinstance(team, dict) and team.get("name")
    ]
    powers = character.get("powers") or []
    names = []
    for power in powers[:4]:
        if isinstance(power, dict) and power.get("name"):
            names.append(str(power["name"]))
        elif power:
            names.append(str(power))
    details = []
    if origin_name:
        details.append(f"origem classificada como {origin_name}")
    if teams:
        details.append(f"vínculo registrado com {teams[0]}")
    if names:
        details.append("habilidades catalogadas: " + ", ".join(names[:3]))
    if not details:
        details.append("dados biográficos estruturados limitados")
    return f"{name}: " + "; ".join(details) + "."


def _source_anchored_description(text: str, character: dict) -> str:
    """Keep provider prose only when it is anchored in structured source data."""
    cleaned = str(text or "").strip()
    if not cleaned or len(cleaned) > 320:
        return _profile_description(character)
    source_values = [
        character.get("name"),
        character.get("name_pt"),
    ]
    origin = character.get("origin") or {}
    if isinstance(origin, dict):
        source_values.append(origin.get("name"))
    source_values.extend(
        team.get("name")
        for team in (character.get("teams") or [])
        if isinstance(team, dict)
    )
    source_values.extend(
        power.get("name")
        for power in (character.get("powers") or [])
        if isinstance(power, dict)
    )
    source_terms = set().union(*(_terms(str(value)) for value in source_values if value))
    if source_terms & _terms(cleaned):
        return cleaned
    return _profile_description(character)


def _prompt(characters: list, culprit_id: int) -> str:
    roster = json.dumps(
        [_compact_character(character) for character in characters],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    profile_questions = build_profile_questions(characters)
    profile_index = json.dumps(
        [{"id": question.id, "text": question.text} for question in profile_questions],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
Você gera um caso investigativo para Shield Noir usando somente PERSONAGENS.
O backend já escolheu o culpado; mantenha culprit_id={culprit_id}.

Retorne somente JSON válido, sem markdown ou explicação. Campos obrigatórios:
id, culprit_id, description, suspects, clues e questions. Preserve os 10 IDs,
names e name_pt recebidos. Escreva todo o conteúdo narrativo em português brasileiro.
Não gere image_url. Não invente biografias específicas ausentes dos dados estruturados.

Construa o caso nesta ordem: janela temporal, local, objeto, método indireto,
requisitos de acesso, oportunidades concorrentes, vestígios, álibis e contradições.
A descrição deve deixar claro quando e onde o objeto desapareceu. O crime deve ser
criativo e relacionado ao elenco sem ser a demonstração óbvia do poder mais conhecido
do culpado. Não nomeie o mecanismo ou a capacidade-assinatura na description: mostre
apenas o desaparecimento e efeitos observáveis; a descoberta do método deve vir das
clues. Dê apoio plausível a pelo menos duas hipóteses além da correta.

Cada suspect precisa ter description curta e crime_moment como depoimento defensivo:
local, atividade e horário dentro ou perto da janela, com testemunha, câmera, chamada,
credencial ou outro registro quando fizer sentido. Varie a confiabilidade: alguns
álibis são confirmados, outros dependem de uma pessoa, um sistema ou têm uma pequena
lacuna. Não escreva "sem álibi", "desconhecido" ou uma confissão.

Gere 5 clues curtas, factuais e diferentes. Misture temporalidade, acesso/rota,
câmera/sensor/log, vestígio físico e álibi/contradição. Cada related_suspects tem
3–7 IDs existentes e representa somente suspeitos diretamente sustentados pelo fato
registrado na pista, não pessoas que apenas poderiam fazer algo parecido; não escreva
nomes ou IDs no texto. Ao menos uma clue deve incluir culprit_id={culprit_id} e outros
suspeitos diretamente compatíveis sem revelar o culpado. Não use frases genéricas
sobre fichas, poderes ou habilidades compartilhadas.

Gere até {QUESTION_CANDIDATES} perguntas candidatas em questions. Cada item tem
somente text e basis. Use exatamente "clue:N", apontando para uma clue explícita,
ou "profile:N", apontando para um fato estruturado disponível em PROFILE_FACTS
(por exemplo, "basis":"clue:2").
Não escreva true_suspect_ids nem answers: o Python deriva as respostas da base.
Uma pergunta baseada em clue deve falar sobre o registro/evidência daquela clue,
sem transformar possibilidade em fato. Uma pergunta baseada em profile só pode usar
o fato catalogado e só deve ser usada se ele tiver relação com este crime.
Não pergunte quem poderia fazer algo apenas por ter um poder genérico.
Não use nomes, alter egos, identidade, "qual personagem", duas características na
mesma pergunta ou perguntas vagas. Não narre a solução na pergunta, não diga que
houve redução, manipulação ou método específico antes de isso estar evidenciado.
Faça candidatas variadas; não reformule a mesma pergunta. O Python escolherá as 10.

PERSONAGENS={roster}
PROFILE_FACTS={profile_index}
"""


def _extract_json(text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("provider não retornou conteúdo")
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", cleaned)
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("resposta não contém um objeto JSON")
    try:
        value, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON inválido: {error.msg}") from error
    if not isinstance(value, dict):
        raise ValueError("resposta JSON não é um objeto")
    return value


def _response_text(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices and isinstance(response, dict):
        choices = response.get("choices") or []
    if not choices:
        return ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    if message is None and isinstance(first_choice, dict):
        message = first_choice.get("message")
    values: list[str] = []
    fields = (
        "content",
        "output_text",
        "text",
        "reasoning_content",
        "reasoning",
        "reasoning_details",
    )

    def collect(value) -> None:
        if isinstance(value, str) and value.strip():
            values.append(value)
            return
        if isinstance(value, dict):
            for key in ("text", "content", "value"):
                collect(value.get(key))
            return
        if isinstance(value, list):
            for part in value:
                collect(part)

    for item in (message, choices[0], response):
        if item is None:
            continue
        for field in fields:
            value = item.get(field) if isinstance(item, dict) else getattr(item, field, None)
            collect(value)
        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                for field in fields:
                    collect(dumped.get(field))

    json_values = [value for value in values if "{" in value]
    for value in json_values:
        if any(marker in value for marker in ("suspects", "questions", "clues")):
            return value
    return json_values[0] if json_values else (values[0] if values else "")


def _looks_like_alibi(text: str) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in ("alega", "afirma", "estava", "permaneceu", "diz que", "registro")
    )


def _is_placeholder_alibi(text: str) -> bool:
    normalized = _normalize(text)
    return (
        len(normalized) < 24
        or any(
            marker in normalized
            for marker in (
                "desconhecido",
                "nao informado",
                "sem informacao",
                "sem alibi",
                "nao apresentou versao",
                "suspeito de ter se infiltrado",
                "sem registro de identificacao",
            )
        )
    )


_CASE_QUESTION_MARKERS = (
    "janela", "horario", "hora", "crime", "presenca", "local", "acesso",
    "autorizacao", "credencial", "porta", "corredor", "rota", "camera",
    "sensor", "log", "registro", "testemunha", "alibi", "depoimento",
    "objeto", "prototipo", "vestigio", "marca", "fibra", "peso", "material",
    "sinal", "entrada", "saida", "sistema", "ferramenta", "metodo", "rastro",
    "distracao", "blecaute", "energia", "movimento", "chamada",
    "lacuna", "contradicao", "trajeto", "subsolo", "area restrita",
    "transportar", "carregar", "mover", "remover", "retirar",
)


def _is_case_question(text: str, case_context: str) -> bool:
    normalized = _normalize(text)
    if not normalized:
        return False
    has_marker = any(marker in normalized for marker in _CASE_QUESTION_MARKERS)
    context_overlap = bool(_terms(text) & _terms(case_context))
    generic_profile = any(
        marker in normalized
        for marker in (
            "perfil de habilidades",
            "ficha oficial",
            "habilidades compartilhadas",
            "ja integrou os vingadores",
            "ja integrou os x men",
            "ja integrou os guardioes",
            "o suspeito possui capacidade de",
            "o suspeito possui super",
        )
    )
    generic_verb = any(
        marker in normalized
        for marker in ("possui", "tem capacidade", "inclui", "e capaz de", "controla")
    )
    return (has_marker or context_overlap) and not (
        generic_profile and not has_marker
    ) and not (generic_verb and not has_marker)


_ALIBI_QUESTION_MARKERS = (
    "esteve", "estava", "presente", "entrada", "registro confirmado",
    "sala", "laboratorio", "corredor", "zona", "hangar", "deposito",
    "oficina", "biblioteca", "centro de comando", "manutencao",
)
_ALIBI_GENERIC_TERMS = {
    "esteve", "estava", "presente", "registro", "confirmado", "entrada",
    "suspeito", "janela", "crime", "horario", "hora", "durante", "entre",
    "quem", "qual", "local", "onde",
}


def _alibi_grounded_ids(text: str, raw_suspects: list, suspect_ids: set[int]) -> set[int] | None:
    normalized = _normalize(text)
    if not any(marker in normalized for marker in _ALIBI_QUESTION_MARKERS):
        return None
    question_terms = _terms(text) - _ALIBI_GENERIC_TERMS
    grounded = set()
    for raw in raw_suspects:
        if not isinstance(raw, dict):
            continue
        suspect_id = _int(raw.get("id"))
        if suspect_id not in suspect_ids:
            continue
        alibi_terms = _terms(str(raw.get("crime_moment") or "")) - _ALIBI_GENERIC_TERMS
        overlap = question_terms & alibi_terms
        if overlap and len(overlap) >= min(2, len(question_terms)):
            grounded.add(suspect_id)
    return grounded


def _unsafe_inference_wording(text: str) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "quem poderia",
            "qual suspeito poderia",
            "quem conseguiria",
            "quem tem capacidade de",
            "possui capacidade de",
            "possui recurso para",
            "poderia usar",
            "poderia gerar",
            "poderia produzir",
            "conseguiria usar",
            "conseguiria gerar",
            "usar seu traje para",
            "gerar o pulso",
            "pulso de reducao",
            "reducao de tamanho",
            "alteracao de tamanho",
            "deixar as particulas",
            "neutralizar temporariamente",
        )
    )


def _infer_question_basis(
    raw: dict,
    text: str,
    clues_by_id: dict[int, dict],
    profile_questions: dict[int, Question],
) -> str:
    """Recover a missing/loosely formatted basis without trusting answers."""
    raw_basis = raw.get("basis")
    if isinstance(raw_basis, dict):
        kind = str(raw_basis.get("type") or raw_basis.get("kind") or "").strip().lower()
        number = _int(raw_basis.get("id") or raw_basis.get("index"))
        if kind and number is not None:
            raw_basis = f"{kind}:{number}"
    basis = str(raw_basis or "").strip().lower()
    if basis:
        if re.fullmatch(r"\d+", basis):
            basis = f"clue:{basis}"
        if re.fullmatch(r"(?:clue|profile)\s*[:_-]?\s*\d+", basis):
            return basis

    explicit_clue_id = _int(
        raw.get("clue_id")
        or raw.get("source_clue_id")
        or raw.get("clue_index")
    )
    if explicit_clue_id in clues_by_id:
        return f"clue:{explicit_clue_id}"

    question_terms = _terms(text)
    clue_scores = [
        (len(question_terms & _terms(str(clue.get("description") or ""))), clue_id)
        for clue_id, clue in clues_by_id.items()
    ]
    if clue_scores:
        best_score = max(score for score, _ in clue_scores)
        best_ids = [clue_id for score, clue_id in clue_scores if score == best_score]
        if best_score >= 2 and len(best_ids) == 1:
            return f"clue:{best_ids[0]}"

    profile_scores = [
        (len(question_terms & _terms(question.text)), profile_id)
        for profile_id, question in profile_questions.items()
    ]
    if profile_scores:
        best_score = max(score for score, _ in profile_scores)
        best_ids = [profile_id for score, profile_id in profile_scores if score == best_score]
        if best_score >= 2 and len(best_ids) == 1:
            return f"profile:{best_ids[0]}"
    return ""


def _questions_from_payload(
    payload: dict,
    suspect_ids: list[int],
    case_context: str,
    characters: list,
    clues: list[dict],
    raw_suspects: list,
) -> list[Question]:
    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        print("[QUESTIONS] Normalização | provider não retornou uma lista questions")
        return []
    expected = set(suspect_ids)
    clues_by_id = {
        clue["id"]: clue
        for clue in clues
        if (
            isinstance(clue, dict)
            and isinstance(clue.get("id"), int)
            and not _generic_clue(str(clue.get("description") or ""))
        )
    }
    profile_questions = {
        question.id: question
        for question in build_profile_questions(characters)
    }
    context_terms = _terms(case_context)
    result: list[Question] = []
    rejected = {
        "sem_base": 0,
        "texto_inseguro": 0,
        "clue_inexistente": 0,
        "nao_relacionada": 0,
        "perfil_invalido": 0,
        "divisao": 0,
    }
    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text") or raw.get("question")
        if not isinstance(text, str):
            continue
        text = text.strip()
        basis = _infer_question_basis(raw, text, clues_by_id, profile_questions)
        if not basis:
            rejected["sem_base"] += 1
            continue
        if _unsafe_inference_wording(text):
            rejected["texto_inseguro"] += 1
            continue
        true_ids: set[int]
        output_text = text
        clue_match = re.fullmatch(r"clue\s*(?:[:_-]|\s+)\s*(\d+)", basis)
        profile_match = re.fullmatch(r"profile\s*(?:[:_-]|\s+)\s*(\d+)", basis)
        if clue_match:
            clue = clues_by_id.get(int(clue_match.group(1)))
            if clue is None:
                rejected["clue_inexistente"] += 1
                continue
            true_ids = {
                value for value in clue.get("related_suspects", []) if value in expected
            }
            grounded = _alibi_grounded_ids(text, raw_suspects, expected)
            if grounded is not None:
                true_ids = grounded
            if not _is_case_question(text, case_context):
                rejected["nao_relacionada"] += 1
                continue
        elif profile_match:
            profile_question = profile_questions.get(int(profile_match.group(1)))
            if profile_question is None:
                rejected["perfil_invalido"] += 1
                continue
            fact_terms = _terms(profile_question.text) - {
                "perfil", "habilidades", "inclui", "suspeito", "capacidade",
            }
            if not fact_terms & context_terms:
                rejected["perfil_invalido"] += 1
                continue
            if not _bad_question_text(text, characters):
                if not (fact_terms & _terms(text)):
                    rejected["perfil_invalido"] += 1
                    continue
            else:
                rejected["perfil_invalido"] += 1
                continue
            true_ids = {
                answer.suspect_id
                for answer in profile_question.answers
                if answer.answer
            }
        else:
            rejected["sem_base"] += 1
            continue
        if not 3 <= len(true_ids) <= 7:
            rejected["divisao"] += 1
            continue
        result.append(Question(
            id=len(result) + 1,
            text=output_text,
            answers=[
                {"suspect_id": suspect_id, "answer": suspect_id in true_ids}
                for suspect_id in suspect_ids
            ],
        ))
    if isinstance(raw_questions, list) and len(result) < N_QUESTIONS:
        details = " ".join(f"{key}={value}" for key, value in rejected.items() if value)
        print(
            f"[QUESTIONS] Normalização | Recebidas={len(raw_questions)} | "
            f"Derivadas={len(result)} | {details or 'sem rejeições'}"
        )
    return result


_CLUE_CATEGORIES = {
    "temporal": ("entre ", "durante", "hora", "minuto", "janela", "antes", "depois"),
    "access": ("acesso", "credencial", "porta", "entrada", "saida", "leitor", "rota", "corredor"),
    "technical": ("camera", "sensor", "log", "registro", "sinal", "sistema", "chamada", "comunicacao"),
    "physical": ("marca", "fibra", "fragmento", "residuo", "peso", "material", "pegada", "objeto", "prototipo"),
    "alibi": ("alibi", "depoimento", "testemunha", "afirma", "alega", "versao", "contradicao"),
}


def _clue_categories(text: str) -> set[str]:
    normalized = _normalize(text)
    categories = {
        category
        for category, markers in _CLUE_CATEGORIES.items()
        if any(marker in normalized for marker in markers)
    }
    if re.search(r"\b\d{1,2}\s*h(?:\s*\d{1,2})?\b", normalized):
        categories.add("temporal")
    return categories


def _generic_clue(text: str) -> bool:
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "fichas oficiais confirmam",
            "habilidades compartilhadas",
            "perfil de habilidades",
            "parte do elenco possui",
            "registros oficiais de poderes",
            "a ficha do personagem",
        )
    ) or not _clue_categories(text)


def _has_time_anchor(text: str) -> bool:
    normalized = _normalize(text)
    return bool(
        re.search(r"\b\d{1,2}\s*h(?:\s*\d{1,2})?\b", normalized)
        or re.search(r"\b(?:entre|durante|apos|antes|depois)\b", normalized)
        or any(marker in normalized for marker in ("madrugada", "noite", "blecaute"))
    )


def _description_reveals_method(text: str) -> bool:
    """Reject a synopsis that gives away the signature before investigation."""
    normalized = _normalize(text)
    return any(
        marker in normalized
        for marker in (
            "o metodo usado",
            "o metodo envolveu",
            "foi usado para",
            "usando seu poder",
            "usando sua capacidade",
            "gracas a sua capacidade",
            "reducao de tamanho",
            "alteracao de tamanho",
            "pulso de reducao",
            "manipulacao magnetica",
            "campo magnetico",
        )
    )


def _redact_description(text: str) -> str:
    """Keep the case setup while removing a provider's premature conclusion."""
    cleaned = str(text or "").strip()
    if not _description_reveals_method(cleaned):
        return cleaned
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    kept = [sentence.strip() for sentence in sentences if sentence.strip() and not _description_reveals_method(sentence)]
    redacted = " ".join(kept).strip()
    if redacted and _has_time_anchor(redacted):
        return redacted
    return (
        "Durante a janela indicada, um artefato de pesquisa desapareceu de uma área "
        "restrita sem sinais de arrombamento; os registros técnicos apresentaram "
        "anomalias ainda não interpretadas."
    )


def _append_grounded_question(
    result: list[Question],
    text: str,
    true_ids: set[int],
    suspect_ids: list[int],
) -> None:
    if not 3 <= len(true_ids) <= 7:
        return
    candidate = Question(
        id=len(result) + 1,
        text=text.strip(),
        answers=[
            {"suspect_id": suspect_id, "answer": suspect_id in true_ids}
            for suspect_id in suspect_ids
        ],
    )
    partition = _question_partition(candidate, set(suspect_ids))
    if partition in {_question_partition(item, set(suspect_ids)) for item in result}:
        return
    if any(_similar_questions(candidate.text, item.text) for item in result):
        return
    result.append(candidate)


def _supplement_questions(
    questions: list[Question],
    clues: list[dict],
    raw_suspects: list[dict],
    suspect_ids: list[int],
    characters: list,
    profile_questions: list[Question] | None = None,
) -> list[Question]:
    """Complete weak provider batches from facts already present in the case."""
    result = list(questions)
    for clue in clues:
        description = str(clue.get("description") or "")
        if _generic_clue(description) or _mentions_identity(description, characters):
            continue
        related = {
            value for value in clue.get("related_suspects", []) if value in set(suspect_ids)
        }
        if not 3 <= len(related) <= 7:
            continue
        categories = _clue_categories(description)
        category = next(
            (
                label
                for key, label in (
                    ("access", "acesso"),
                    ("technical", "registro técnico"),
                    ("physical", "vestígio físico"),
                    ("temporal", "tempo"),
                    ("alibi", "álibi"),
                )
                if key in categories
            ),
            "evidência",
        )
        _append_grounded_question(
            result,
            f"A pista de {category} relaciona diretamente o suspeito ao caso?",
            related,
            suspect_ids,
        )
        if len(result) >= N_QUESTIONS:
            return result[:N_QUESTIONS]

    feature_specs = (
        ("um horário preciso", lambda text: bool(re.search(r"\b\d{1,2}\s*h", text))),
        ("um registro eletrônico", lambda text: any(marker in text for marker in (
            "camera", "registrad", "leitor", "chamada", "comunicacao",
        ))),
        ("uma testemunha", lambda text: any(marker in text for marker in (
            "acompanhado", "convidados", "testemunha", "equipe",
        ))),
        ("uma área operacional", lambda text: any(marker in text for marker in (
            "controle", "gerador", "oficina", "medica", "estacionamento", "biblioteca",
        ))),
        ("uma comunicação", lambda text: any(marker in text for marker in (
            "comunicacao", "chamada", "conversando",
        ))),
        ("uma equipe presente", lambda text: any(marker in text for marker in (
            "equipe", "tecnico", "convidados",
        ))),
        ("uma atividade de apoio", lambda text: any(marker in text for marker in (
            "conferindo", "organizando", "auxiliando", "ajudando", "consultando",
        ))),
        ("uma área de operação técnica", lambda text: any(marker in text for marker in (
            "controle", "gerador", "oficina", "estacionamento",
        ))),
    )
    for label, matches in feature_specs:
        true_ids = {
            suspect_id
            for suspect in raw_suspects
            if isinstance(suspect, dict)
            and (suspect_id := _int(suspect.get("id"))) in set(suspect_ids)
            and matches(_normalize(str(suspect.get("crime_moment") or "")))
        }
        _append_grounded_question(
            result,
            f"O depoimento do suspeito menciona {label}?",
            true_ids,
            suspect_ids,
        )
        if len(result) >= N_QUESTIONS:
            return result[:N_QUESTIONS]

    profile_questions = profile_questions if profile_questions is not None else build_profile_questions(characters)
    for question in profile_questions:
        _append_grounded_question(
            result,
            question.text,
            {
                answer.suspect_id
                for answer in question.answers
                if answer.answer
            },
            suspect_ids,
        )
        if len(result) >= N_QUESTIONS:
            break
    return result[:N_QUESTIONS]


def _normalize_payload(payload: dict, characters: list, culprit_id: int) -> dict:
    source = [_dict(character) for character in characters]
    raw_suspects = payload.get("suspects")
    raw_suspects = raw_suspects if isinstance(raw_suspects, list) else []
    by_id = {
        _int(item.get("id")): item
        for item in raw_suspects
        if isinstance(item, dict) and _int(item.get("id")) is not None
    }

    suspects = []
    for index, character in enumerate(source):
        character_id = character["id"]
        raw = by_id.get(character_id)
        if raw is None and index < len(raw_suspects) and isinstance(raw_suspects[index], dict):
            raw = raw_suspects[index]
        raw = raw or {}
        name = character.get("name") or character.get("name_pt") or str(character_id)
        description = str(raw.get("description") or "").strip()
        crime_moment = str(raw.get("crime_moment") or "").strip()
        short_label = len(crime_moment) < 180 and crime_moment.count(",") >= 2
        if (
            _looks_like_alibi(description)
            and crime_moment
            and not _is_placeholder_alibi(crime_moment)
            and (not _looks_like_alibi(crime_moment) or short_label)
        ):
            description, crime_moment = crime_moment, description
        suspects.append({
            "id": character_id,
            "name": name,
            "name_pt": character.get("name_pt") or name,
            "image_url": character.get("image_url"),
            "description": _source_anchored_description(description, character),
            "crime_moment": crime_moment,
        })

    clues = []
    used_clue_ids: set[int] = set()
    raw_clues = payload.get("clues")
    if isinstance(raw_clues, list):
        for raw in raw_clues:
            if not isinstance(raw, dict):
                continue
            text = raw.get("description") or raw.get("text")
            related = (
                raw.get("related_suspects")
                or raw.get("related_suspect_ids")
                or raw.get("suspect_ids")
            )
            if not isinstance(text, str) or not isinstance(related, list):
                continue
            related_ids = [value for value in (_int(item) for item in related) if value is not None]
            raw_clue_id = _int(raw.get("id"))
            clue_id = raw_clue_id if raw_clue_id and raw_clue_id not in used_clue_ids else len(clues) + 1
            while clue_id in used_clue_ids:
                clue_id += 1
            used_clue_ids.add(clue_id)
            clues.append({
                "id": clue_id,
                "description": text.strip(),
                "related_suspects": related_ids,
            })

    suspect_ids = [item["id"] for item in source]
    case_context = " ".join([
        str(payload.get("description") or ""),
        " ".join(str(item.get("description") or "") for item in raw_clues or [] if isinstance(item, dict)),
        " ".join(str(item.get("crime_moment") or "") for item in raw_suspects if isinstance(item, dict)),
    ])
    questions = _questions_from_payload(
        payload,
        suspect_ids,
        case_context,
        characters,
        clues,
        raw_suspects,
    )
    questions = _supplement_questions(
        _filter_questions(questions, characters, set(suspect_ids)),
        clues,
        raw_suspects,
        suspect_ids,
        characters,
    )

    return {
        "id": _int(payload.get("id")) or random.randint(100000, 999999),
        "culprit_id": culprit_id,
        "description": _redact_description(str(payload.get("description") or "")),
        "suspects": suspects,
        "clues": clues,
        "questions": questions,
    }


def _mentions_identity(text: str, characters: list) -> bool:
    normalized = f" {_normalize(text)} "
    for character in characters:
        data = _dict(character)
        character_id = _int(data.get("id"))
        if character_id is not None and re.search(
            rf"\b{character_id}\b", text
        ):
            return True
        for field in ("name", "name_pt", "real_name"):
            value = data.get(field)
            if not value:
                continue
            identity = _normalize(str(value))
            if identity and f" {identity} " in normalized:
                return True
    return False


def _bad_question_text(text: str, characters: list) -> bool:
    normalized = _normalize(text)
    return (
        not text.strip()
        or _mentions_identity(text, characters)
        or ";" in text
        or "/" in text
        or re.search(r"\b(ou|nem)\b", normalized) is not None
        or re.search(
            r"\b(nome|alter ego|identidade civil|letra inicial|qual personagem)\b",
            normalized,
        ) is not None
    )


def _similar_questions(first: str, second: str) -> bool:
    first_terms = _terms(first)
    second_terms = _terms(second)
    if not first_terms or not second_terms:
        return _normalize(first) == _normalize(second)
    intersection = len(first_terms & second_terms)
    union = len(first_terms | second_terms)
    return intersection / union >= 0.78


def _question_partition(question: Question, suspect_ids: set[int]) -> tuple[int, ...]:
    true_ids = tuple(sorted(
        answer.suspect_id for answer in question.answers if answer.answer
    ))
    false_ids = tuple(sorted(suspect_ids - set(true_ids)))
    return min(true_ids, false_ids)


def _filter_questions(
    questions: list[Question],
    characters: list,
    suspect_ids: set[int],
    diagnostic: dict[str, int] | None = None,
) -> list[Question]:
    valid: list[Question] = []
    seen_partitions: set[tuple[int, ...]] = set()
    rejected_format = rejected_text = rejected_division = duplicates = 0
    for question in questions:
        ids = [answer.suspect_id for answer in question.answers]
        if len(ids) != N_SUSPECTS or set(ids) != suspect_ids or len(set(ids)) != len(ids):
            rejected_format += 1
            continue
        if _bad_question_text(question.text, characters):
            rejected_text += 1
            continue
        true_count = sum(answer.answer for answer in question.answers)
        if min(true_count, len(suspect_ids) - true_count) < 3:
            rejected_division += 1
            continue
        cleaned = question.model_copy(update={"text": question.text.strip()})
        partition = _question_partition(cleaned, suspect_ids)
        if (
            partition in seen_partitions
            or any(_similar_questions(cleaned.text, previous.text) for previous in valid)
        ):
            duplicates += 1
            continue
        valid.append(cleaned)
        seen_partitions.add(partition)

    if diagnostic is not None:
        diagnostic["geradas"] = len(questions)
        diagnostic["rejeitadas_formato"] = rejected_format
        diagnostic["rejeitadas_texto"] = rejected_text
        diagnostic["rejeitadas_divisao"] = rejected_division
        diagnostic["duplicadas"] = diagnostic.get("duplicadas", 0) + duplicates
        diagnostic["aprovadas"] = len(valid)
    return valid


def _rank_questions(
    questions: list[Question],
    total: int,
    limit: int = N_QUESTIONS,
) -> list[Question]:
    balanced = [
        question
        for question in questions
        if min(
            sum(answer.answer for answer in question.answers),
            total - sum(answer.answer for answer in question.answers),
        ) >= 3
    ]
    suspect_ids = {
        answer.suspect_id
        for question in balanced
        for answer in question.answers
    }
    unresolved_pairs = set(combinations(sorted(suspect_ids), 2))
    selected: list[Question] = []
    remaining = list(balanced)
    while remaining and len(selected) < limit:
        best = max(
            remaining,
            key=lambda question: (
                min(
                    sum(answer.answer for answer in question.answers),
                    total - sum(answer.answer for answer in question.answers),
                ),
                sum(
                    1
                    for first, second in unresolved_pairs
                    if next(a.answer for a in question.answers if a.suspect_id == first)
                    != next(a.answer for a in question.answers if a.suspect_id == second)
                ),
            ),
        )
        selected.append(best)
        separated_pairs = {
            (first, second)
            for first, second in unresolved_pairs
            if next(a.answer for a in best.answers if a.suspect_id == first)
            != next(a.answer for a in best.answers if a.suspect_id == second)
        }
        unresolved_pairs.difference_update(separated_pairs)
        remaining.remove(best)
    return [
        question.model_copy(update={"id": index})
        for index, question in enumerate(selected, 1)
    ]


def _has_distinct_answer_patterns(
    questions: list[Question],
    suspect_ids: set[int],
) -> bool:
    if not questions or not suspect_ids:
        return False
    signatures = {
        tuple(
            next(
                answer.answer
                for answer in question.answers
                if answer.suspect_id == suspect_id
            )
            for question in questions
        )
        for suspect_id in suspect_ids
    }
    return len(signatures) == len(suspect_ids)


def _validate_case(
    case: InvestigationCase,
    characters: list,
    culprit_id: int,
    require_minimum: bool = False,
    diagnostic: dict[str, int] | None = None,
) -> InvestigationCase:
    character_data = [_dict(character) for character in characters]
    expected_ids = {item["id"] for item in character_data}
    if len(character_data) != N_SUSPECTS or len(expected_ids) != N_SUSPECTS:
        raise ValueError("O elenco precisa conter exatamente 10 personagens distintos.")
    if case.culprit_id != culprit_id:
        raise ValueError("O provider alterou o culpado sorteado pelo backend.")
    if not case.description.strip():
        raise ValueError("Caso sem descrição do crime.")
    if not _has_time_anchor(case.description):
        raise ValueError("Descrição do caso não define uma janela temporal.")
    if _description_reveals_method(case.description):
        raise ValueError("Descrição do caso revela diretamente o método do crime.")
    if (
        len(case.suspects) != N_SUSPECTS
        or {suspect.id for suspect in case.suspects} != expected_ids
    ):
        raise ValueError("Caso não contém exatamente os 10 suspeitos recebidos.")

    source_by_id = {item["id"]: item for item in character_data}
    canonical_suspects = [
        suspect.model_copy(update={
            "name": source_by_id[suspect.id].get("name") or suspect.name,
            "name_pt": source_by_id[suspect.id].get("name_pt") or suspect.name_pt,
            "image_url": source_by_id[suspect.id].get("image_url"),
        })
        for suspect in case.suspects
    ]
    invalid_alibis = [
        suspect.name_pt or suspect.name
        for suspect in canonical_suspects
        if _is_placeholder_alibi(suspect.crime_moment)
    ]
    if invalid_alibis:
        raise ValueError(
            "Álibi ausente ou genérico para: " + ", ".join(invalid_alibis[:3])
        )

    valid_clues = []
    seen_descriptions = set()
    rejected_clues = {
        "vazia": 0,
        "grupo/ids": 0,
        "duplicada": 0,
        "identidade": 0,
        "generica": 0,
    }
    for clue in case.clues:
        related = tuple(clue.related_suspects)
        normalized_description = _normalize(clue.description)
        if not normalized_description:
            rejected_clues["vazia"] += 1
            continue
        if (
            len(related) < 2
            or len(related) > 6
            or len(set(related)) != len(related)
            or not set(related) <= expected_ids
        ):
            rejected_clues["grupo/ids"] += 1
            continue
        if normalized_description in seen_descriptions:
            rejected_clues["duplicada"] += 1
            continue
        if _mentions_identity(clue.description, characters):
            rejected_clues["identidade"] += 1
            continue
        if _generic_clue(clue.description):
            rejected_clues["generica"] += 1
            continue
        seen_descriptions.add(normalized_description)
        valid_clues.append(clue.model_copy(update={"id": len(valid_clues) + 1}))
    if len(valid_clues) < 3:
        rejected = ", ".join(
            f"{reason}={count}"
            for reason, count in rejected_clues.items()
            if count
        ) or "sem detalhe adicional"
        raise ValueError(
            f"Caso contém {len(valid_clues)}/3 clues válidas ({rejected})."
        )
    if len({_category for clue in valid_clues for _category in _clue_categories(clue.description)}) < 2:
        raise ValueError("Clues válidas não cobrem categorias investigativas diferentes.")
    if not any(culprit_id in clue.related_suspects for clue in valid_clues):
        raise ValueError("Nenhuma clue válida sustenta o culpado.")
    if diagnostic is not None:
        diagnostic["clues_geradas"] = len(case.clues)
        diagnostic["clues_validas"] = len(valid_clues)
        diagnostic["clues_descartadas"] = len(case.clues) - len(valid_clues)

    valid_questions = _filter_questions(case.questions, characters, expected_ids, diagnostic)
    if require_minimum and len(_rank_questions(valid_questions, N_SUSPECTS)) < N_QUESTIONS:
        raise ValueError(
            f"Provider produziu apenas {len(valid_questions)} perguntas úteis; "
            f"são necessárias {N_QUESTIONS}."
        )
    return case.model_copy(update={
        "suspects": canonical_suspects,
        "clues": valid_clues,
        "questions": valid_questions,
    })


def _finalize_case(case: InvestigationCase, candidates: list[Question]) -> InvestigationCase:
    selected = _rank_questions(candidates, len(case.suspects))
    if len(selected) < MIN_QUESTIONS:
        raise ValueError(
            f"Provider gerou apenas {len(selected)} perguntas úteis; "
            f"são necessárias {MIN_QUESTIONS}."
        )
    if not _has_distinct_answer_patterns(
        selected, {suspect.id for suspect in case.suspects}
    ):
        raise ValueError("Dois suspeitos têm o mesmo padrão de respostas nas perguntas finais.")
    return case.model_copy(update={"questions": selected[:N_QUESTIONS]})


def _error_summary(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "tempo limite do provider excedido"
    message = " ".join(str(error).split())
    lowered = message.lower()
    if any(marker in lowered for marker in ("429", "quota", "resource_exhausted")):
        return "quota do provider excedida"
    return message[:240] or error.__class__.__name__


async def _generate_gemini(characters: list, culprit_id: int) -> InvestigationCase:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada")
    client = genai.Client(api_key=GEMINI_API_KEY)
    chat = client.aio.chats.create(
        model=GEMINI_MODEL,
        config={
            "response_mime_type": "application/json",
            "response_schema": _GeneratedCase,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
        },
    )
    response = await chat.send_message(_prompt(characters, culprit_id))
    payload = _normalize_payload(_extract_json(response.text), characters, culprit_id)
    return InvestigationCase.model_validate(payload)


async def _generate_groq(characters: list, culprit_id: int) -> InvestigationCase:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY não configurada")
    client = AsyncGroq(api_key=GROQ_API_KEY)
    options = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": _prompt(characters, culprit_id)}],
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    try:
        response = await client.chat.completions.create(**options)
    except Exception as error:
        if "response_format" not in str(error).lower() and "json_object" not in str(error).lower():
            raise
        options.pop("response_format", None)
        response = await client.chat.completions.create(**options)
    payload = _normalize_payload(_extract_json(_response_text(response)), characters, culprit_id)
    return InvestigationCase.model_validate(payload)


async def _call_openrouter(messages: list[dict]):
    """Accommodate free OpenRouter models with different reasoning modes."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY não configurada")
    client = AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
    )

    async def request(reasoning_effort: str, json_mode: bool = True):
        options = {
            "model": OPENROUTER_MODEL,
            "messages": messages,
            "reasoning_effort": reasoning_effort,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if json_mode:
            options["response_format"] = {"type": "json_object"}
        return await client.chat.completions.create(**options)

    last_error: Exception | None = None
    for reasoning_effort, json_mode in (
        ("none", True),
        ("none", False),
        ("low", True),
        ("low", False),
    ):
        try:
            response = await request(reasoning_effort, json_mode)
            if _response_text(response):
                return response
            last_error = RuntimeError("OpenRouter não retornou conteúdo")
        except Exception as error:
            lowered = str(error).lower()
            if any(marker in lowered for marker in ("429", "quota", "resource_exhausted")):
                raise
            last_error = error
            if (
                "reasoning is mandatory" not in lowered
                and "response_format" not in lowered
                and "json_object" not in lowered
                and "503" not in lowered
                and "unavailable" not in lowered
            ):
                raise
    raise last_error or RuntimeError("OpenRouter não retornou conteúdo")


async def _generate_openrouter(characters: list, culprit_id: int) -> InvestigationCase:
    response = await _call_openrouter(
        [{"role": "user", "content": _prompt(characters, culprit_id)}]
    )
    payload = _normalize_payload(_extract_json(_response_text(response)), characters, culprit_id)
    return InvestigationCase.model_validate(payload)


def _fallback_case(
    characters: list,
    culprit_id: int,
    profile_questions: list[Question] | None = None,
) -> InvestigationCase:
    data = [_dict(character) for character in characters]
    ids = [item["id"] for item in data]
    culprit_index = ids.index(culprit_id)
    scenario = random.choice(_FALLBACK_SCENARIOS)

    true_sets = [
        {
            (question_index + offset) % N_SUSPECTS
            for offset in _FALLBACK_PATTERN
        }
        for question_index in range(N_QUESTIONS)
    ]
    clue_indices = [
        culprit_index,
        (culprit_index - 1) % N_QUESTIONS,
        (culprit_index - 2) % N_QUESTIONS,
    ]
    fallback_raw_suspects = [
        {
            "id": character["id"],
            "crime_moment": f"Afirma que estava {_FALLBACK_ALIBIS[index % len(_FALLBACK_ALIBIS)]}.",
        }
        for index, character in enumerate(data)
    ]
    fallback_clues = [
        {
            "id": clue_id,
            "description": scenario["clues"][clue_id - 1],
            "related_suspects": [ids[index] for index in true_sets[question_index]],
        }
        for clue_id, question_index in enumerate(clue_indices, 1)
    ]
    factual_questions = _rank_questions(
        _supplement_questions(
            [],
            fallback_clues,
            fallback_raw_suspects,
            ids,
            data,
            profile_questions,
        ),
        N_SUSPECTS,
    )
    if len(factual_questions) == N_QUESTIONS and any(
        answer.suspect_id == culprit_id and answer.answer
        for question in factual_questions
        for answer in question.answers
    ):
        suspects = [
            Suspect(
                id=character["id"],
                name=character.get("name") or character.get("name_pt") or str(character["id"]),
                name_pt=character.get("name_pt") or character.get("name") or str(character["id"]),
                image_url=character.get("image_url"),
                description=_profile_description(character),
                crime_moment=(
                    f"Afirma que estava {_FALLBACK_ALIBIS[index % len(_FALLBACK_ALIBIS)]}."
                ),
            )
            for index, character in enumerate(data)
        ]
        return InvestigationCase(
            id=random.randint(100000, 999999),
            culprit_id=culprit_id,
            description=scenario["description"],
            suspects=suspects,
            clues=[Clue.model_validate(clue) for clue in fallback_clues],
            questions=factual_questions,
        )

    suspects = []
    for index, character in enumerate(data):
        positive = [_FALLBACK_FACTS[q] for q, group in enumerate(true_sets) if index in group]
        negative = [_FALLBACK_FACTS[q] for q, group in enumerate(true_sets) if index not in group]
        facts = (
            " O relatório operacional registra: "
            + "; ".join(positive)
            + ". Não registra: "
            + "; ".join(negative)
            + "."
        )
        suspects.append(Suspect(
            id=character["id"],
            name=character.get("name") or character.get("name_pt") or str(character["id"]),
            name_pt=character.get("name_pt") or character.get("name") or str(character["id"]),
            image_url=character.get("image_url"),
            description=_profile_description(character),
            crime_moment=(
                f"Afirma que estava {_FALLBACK_ALIBIS[index % len(_FALLBACK_ALIBIS)]}."
                + facts
            ),
        ))

    questions = [
            Question(
            id=index + 1,
            text=_FALLBACK_QUESTION_TEXTS[index],
            answers=[
                {"suspect_id": suspect_id, "answer": position in true_sets[index]}
                for position, suspect_id in enumerate(ids)
            ],
        )
        for index, fact in enumerate(_FALLBACK_FACTS)
    ]
    return InvestigationCase(
        id=random.randint(100000, 999999),
        culprit_id=culprit_id,
        description=scenario["description"],
        suspects=suspects,
        clues=[
            Clue(
                id=clue_id,
                description=scenario["clues"][clue_id - 1],
                related_suspects=[
                    ids[index] for index in true_sets[question_index]
                ],
            )
            for clue_id, question_index in enumerate(clue_indices, 1)
        ],
        questions=questions,
    )


async def generate_case(characters: list[dict]) -> InvestigationCase:
    data = [_dict(character) for character in characters]
    ids = [item.get("id") for item in data]
    if len(data) != N_SUSPECTS or len(ids) != len(set(ids)) or any(item is None for item in ids):
        raise ValueError("O elenco recebido precisa ter exatamente 10 personagens distintos.")
    controlled_ids = {character["id"] for character in MARVEL_CHARACTERS}
    if not set(ids) <= controlled_ids:
        raise ValueError("O elenco contém personagem fora da lista controlada.")

    culprit_id = random.choice(ids)
    providers = (
        ("Gemini", _generate_gemini),
        ("Groq", _generate_groq),
        ("OpenRouter", _generate_openrouter),
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + CASE_DEADLINE_SECONDS
    for provider_index, (name, generate) in enumerate(providers):
        try:
            diagnostic: dict[str, int] = {}
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("deadline global do caso excedida")
            candidate = await asyncio.wait_for(
                generate(data, culprit_id),
                timeout=min(PROVIDER_TIMEOUT_SECONDS, remaining),
            )
            case = _validate_case(
                candidate,
                data,
                culprit_id,
                require_minimum=True,
                diagnostic=diagnostic,
            )
            print(
                f"[QUESTIONS] Provider={name} | Caso válido | "
                f"Clues={diagnostic.get('clues_validas', 0)} | "
                f"Perguntas candidatas={len(case.questions)}"
            )
            if diagnostic.get("clues_descartadas"):
                print(
                    f"[QUESTIONS] Provider={name} | Clues descartadas="
                    f"{diagnostic['clues_descartadas']} | "
                    f"Clues válidas={diagnostic.get('clues_validas', 0)}"
                )
            return _finalize_case(case, case.questions)
        except Exception as error:
            print(f"{name} falhou: {_error_summary(error)}")
            if provider_index + 1 < len(providers):
                print(f"Tentando {providers[provider_index + 1][0]}...")

    profile_questions = _filter_questions(
        build_profile_questions(characters),
        data,
        set(ids),
    )
    print(
        "Todos os provedores de IA falharam. Usando fallback investigativo; "
        f"perguntas factuais disponíveis={len(profile_questions)}."
    )
    return _fallback_case(data, culprit_id, profile_questions)
