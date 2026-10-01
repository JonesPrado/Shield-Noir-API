import json
import random
import re
import unicodedata

from google import genai
from groq import AsyncGroq
from openai import AsyncOpenAI

from app.config import (
  GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY,
  GEMINI_MODEL, GROQ_MODEL, OPENROUTER_MODEL
)
from app.data.marvel_characters import MARVEL_CHARACTERS
from app.schemas import AdditionalQuestions, InvestigationCase, Question

N_SUSPECTS = 10
N_QUESTIONS = 10
MIN_QUESTIONS = 10
QUESTIONS_PER_CALL = 7
MAX_PROFILE_CHARS = 300

def _to_dict(character) -> dict:
    """Aceita tanto CharacterData (pydantic) quanto dict — não depende de
    quem chama já ter feito .model_dump()."""
    return character.model_dump() if hasattr(character, "model_dump") else character


def _compact_character(character) -> dict:
    data = _to_dict(character)
    powers = []
    for power in data.get("powers") or []:
        if isinstance(power, dict):
            item = {key: str(power[key])[:80] for key in ("name", "description") if power.get(key)}
        else:
            item = str(power)[:80]
        if item:
            powers.append(item)
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "name_pt": data.get("name_pt"),
        "real_name": data.get("real_name"),
        "deck": str(data.get("deck") or "")[:MAX_PROFILE_CHARS],
        "powers": powers[:5],
    }


def _build_prompt(characters: list, culprit_id: int) -> str:
    characters_json = json.dumps(
        [_compact_character(c) for c in characters],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"""
Você é o motor lógico do Shield Noir. Gere um caso usando SOMENTE os 10
personagens em PERSONAGENS. O backend já escolheu o culpado: mantenha
culprit_id={culprit_id}, sem trocar, inventar ou favorecer heróis/vilões.

SAÍDA DO CASO
- Retorne exatamente um JSON válido, sem markdown, comentários ou explicação.
- Mantenha id, name, name_pt e IDs dos personagens exatamente como recebidos.
- Retorne os 10 suspects, 3 clues e exatamente {QUESTIONS_PER_CALL} questions.
- Escreva description, suspects, clues, crime_moment e questions.text em
  português brasileiro.
- Cada suspect deve ter description curta e crime_moment em 1–2 frases: uma
  defesa plausível do que afirma estar fazendo na hora do crime, com horário,
  local, atividade, testemunha ou registro quando fizer sentido. Varie os
  álibis e inclua alguma dúvida plausível; não liste poderes.
- Crie um crime específico para este elenco, com método, janela de tempo e
  evidências que permitam hipóteses concorrentes. Cada clue deve ser curta,
  factual e ter 2–6 related_suspects. Nunca revele o culpado.

PROTOCOLO OBRIGATÓRIO PARA QUESTIONS
1. Use apenas fatos explícitos em deck/powers dos perfis. Não use memória ou
   conhecimento externo da Marvel. Uma resposta true só é permitida quando o
   perfil sustenta claramente a característica; caso contrário, marque false.
2. Antes de escrever, crie internamente uma matriz: para cada candidata,
   escreva os 10 IDs e as 10 respostas booleanas. Só aprove uma candidata
   factual, concreta, em português, de uma única característica e que divida
   o elenco em 3/7, 4/6, 5/5, 6/4 ou 7/3.
3. Priorize nesta ordem: 5/5, depois 4/6 ou 6/4, depois 3/7 ou 7/3.
   Nunca retorne 0/10, 1/9, 2/8, 8/2, 9/1 ou 10/0. Se uma candidata falhar,
   descarte-a antes de escrever e escolha outra característica compartilhada.
4. As {QUESTIONS_PER_CALL} perguntas precisam ser diferentes entre si e cobrir
   famílias diferentes. Não reformule a mesma habilidade ou categoria.
5. Pergunte uma única propriedade verificável por vez. Não use nomes, aliases,
   identidade civil, título, alter ego ou uma característica tão exclusiva
   que identifique um único personagem. Evite também perguntas vagas como
   “é poderoso”, “é habilidoso” ou “é conhecido”.
6. Cada question deve ter text e exatamente 10 answers: uma resposta por cada
   ID, sem repetição ou omissão. answer é booleano JSON true/false, nunca texto.
   As respostas devem seguir a mesma regra factual da pergunta, não o culpado.

Não exponha a matriz nem o raciocínio. O JSON final deve conter somente os
campos id, culprit_id, description, suspects, clues e questions.

PERSONAGENS={characters_json}
"""


def _build_additional_questions_prompt(
    characters: list,
    perguntas_aprovadas: list[Question],
) -> str:
    characters_json = json.dumps(
        [_compact_character(c) for c in characters],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    perguntas_aprovadas_json = json.dumps(
        [
            {"id": pergunta.id, "text": pergunta.text}
            for pergunta in perguntas_aprovadas
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    proximo_id = max((pergunta.id for pergunta in perguntas_aprovadas), default=0) + 1
    return f"""
Complete o lote de perguntas do caso existente. Retorne somente um JSON
válido com o campo questions. Não retorne caso, culprit_id, description,
suspects ou clues.

Gere exatamente {QUESTIONS_PER_CALL} perguntas novas, numeradas a partir de
{proximo_id}. Cada uma deve ter text em português brasileiro e exatamente 10
answers: uma entrada para cada ID de PERSONAGENS, sem duplicatas, com answer
booleano JSON true/false. Não retorne exemplos, explicações ou markdown.

Use este processo antes da saída:
1. Monte internamente uma matriz de 10 IDs por candidata. Use somente fatos
   explícitos nos perfis; true exige suporte claro no perfil, false caso não
   haja suporte. Não use conhecimento externo.
2. Só mantenha candidatas de uma única característica, concretas, factuais e
   equilibradas: 5/5 é melhor; depois 4/6 ou 6/4; depois 3/7 ou 7/3.
   Descarte 0/10, 1/9, 2/8, 8/2, 9/1 e 10/0 antes de escrever.
3. Faça as {QUESTIONS_PER_CALL} perguntas cobrirem famílias diferentes.
   Não repita, parafraseie ou aproxime semanticamente nenhuma pergunta de
   PERGUNTAS_APROVADAS. Não use nomes, aliases, identidades, títulos,
   características exclusivas, perguntas vagas ou propriedades combinadas.
4. Se uma candidata falhar em qualquer regra, descarte-a e substitua-a por
   outra característica compartilhada antes de responder. Nunca preencha o
   lote com perguntas individualizantes só para atingir a quantidade.

PERGUNTAS_APROVADAS={perguntas_aprovadas_json}
PERSONAGENS={characters_json}
"""

_STOPWORDS = {
    "a", "as", "o", "os", "um", "uma", "uns", "umas", "de", "do", "da",
    "dos", "das", "e", "ou", "em", "no", "na", "nos", "nas", "por", "para",
    "com", "sem", "que", "se", "é", "sao", "são", "tem", "possui", "pode",
    "capaz", "capacidade", "algum", "alguma", "alguns", "algumas", "tipo",
    "tipos", "suspeito", "suspeita",
}


def _normalizar_texto(texto: str) -> str:
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", texto.lower())
        if not unicodedata.combining(caractere)
    )
    return re.sub(r"[^a-z0-9]+", " ", sem_acentos).strip()


def _termos_significativos(texto: str) -> set[str]:
    return {
        termo for termo in _normalizar_texto(texto).split()
        if termo not in _STOPWORDS and len(termo) >= 3
    }


def _familias_pergunta(texto: str) -> set[str]:
    """Agrupa poucas reformulações óbvias sem tentar fazer NLP completo."""
    normalizado = _normalizar_texto(texto)
    familias: set[str] = set()
    grupos = {
        "combate_corporal": (
            "combate corpo", "corpo a corpo", "artes marciais", "luta corporal",
        ),
        "contato_alienigena": (
            "alienigena", "civilizacao alienigena", "fora da terra",
            "extraterrestre", "outras civilizacoes",
        ),
        "voo": ("capacidade de voo", "pode voar", "voo autonomo", "voar"),
        "sentidos": (
            "sentidos aprimorados", "percepcao aprimorada", "visao agucada",
            "audicao agucada", "olfato aprimorado",
        ),
    }
    for familia, termos in grupos.items():
        if any(termo in normalizado for termo in termos):
            familias.add(familia)
    return familias


def _menciona_identidade(texto: str, characters: list) -> bool:
    """Impede que uma pergunta entregue um suspeito por nome/alter ego."""
    normalizado = f" {_normalizar_texto(texto)} "
    for character in characters:
        dados = _to_dict(character)
        for campo in ("name", "name_pt", "real_name"):
            valor = dados.get(campo)
            if valor:
                identidade = _normalizar_texto(str(valor))
                if identidade:
                    if f" {identidade} " in normalizado:
                        return True
                    # Também bloqueia um nome civil/alias de uma palavra
                    # quando o modelo omite o restante de uma identidade.
                    partes = identidade.split()
                    if any(
                        len(parte) >= 4 and f" {parte} " in normalizado
                        for parte in partes
                    ):
                        return True
    return False


def _sao_semelhantes(primeira: Question, segunda: Question) -> bool:
    """Deduplicação simples e conservadora para perguntas."""
    if _familias_pergunta(primeira.text) & _familias_pergunta(segunda.text):
        return True
    termos_a = _termos_significativos(primeira.text)
    termos_b = _termos_significativos(segunda.text)
    if not termos_a or not termos_b:
        return _normalizar_texto(primeira.text) == _normalizar_texto(segunda.text)
    intersecao = len(termos_a & termos_b)
    uniao = len(termos_a | termos_b)
    return intersecao / uniao >= 0.65 or termos_a <= termos_b or termos_b <= termos_a


def _divisao_pergunta(pergunta: Question, total_suspeitos: int) -> tuple[int, int]:
    verdadeiros = sum(answer.answer for answer in pergunta.answers)
    return verdadeiros, total_suspeitos - verdadeiros


def _selecionar_perguntas(candidatas: list[Question], total_suspeitos: int) -> list[Question]:
    """Prioriza divisões equilibradas e rejeita perguntas individualizantes."""
    prioritarias = [
        pergunta for pergunta in candidatas
        if sorted(_divisao_pergunta(pergunta, total_suspeitos)) in ([5, 5], [4, 6])
    ]
    reservas = [
        pergunta for pergunta in candidatas
        if sorted(_divisao_pergunta(pergunta, total_suspeitos)) == [3, 7]
    ]

    return (prioritarias + reservas)[:N_QUESTIONS]


def _extrair_conteudo_resposta(response) -> str:
    """Normaliza content string/list dos clientes compatíveis com OpenAI."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        partes: list[str] = []
        for item in content:
            if isinstance(item, str):
                partes.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                partes.append(item["text"])
            elif isinstance(getattr(item, "text", None), str):
                partes.append(item.text)
        return "".join(partes).strip()
    return ""


def _extrair_json(texto: str) -> str:
    """Aceita JSON puro ou JSON envolvido em bloco markdown, sem relaxar schema."""
    texto = texto.strip()
    if texto.startswith("```"):
        linhas = texto.splitlines()
        if linhas and linhas[0].strip().startswith("```"):
            linhas = linhas[1:]
        if linhas and linhas[-1].strip() == "```":
            linhas = linhas[:-1]
        texto = "\n".join(linhas).strip()

    # Alguns modelos devolvem quebras de linha/tabulações literais dentro de
    # strings JSON. Removê-las como espaços permite o parse sem aceitar texto
    # fora do objeto nem relaxar a validação Pydantic seguinte.
    texto = "".join(caractere if ord(caractere) >= 32 else " " for caractere in texto)

    try:
        json.loads(texto)
        return texto
    except json.JSONDecodeError:
        inicio = texto.find("{")
        fim = texto.rfind("}")
        if inicio < 0 or fim <= inicio:
            raise ValueError("resposta não contém um objeto JSON")
        candidato = texto[inicio:fim + 1]
        json.loads(candidato)
        return candidato


def _erro_de_quota(erro: Exception) -> bool:
    mensagem = str(erro).lower()
    return (
        "429" in mensagem
        or "resource_exhausted" in mensagem
        or "quota exceeded" in mensagem
        or "quota_exceeded" in mensagem
    )


def _erro_transitorio(erro: Exception) -> bool:
    mensagem = str(erro).lower()
    return any(
        marcador in mensagem
        for marcador in ("503", "temporarily unavailable", "timeout", "timed out")
    )


def _erro_sem_conteudo(erro: Exception) -> bool:
    mensagem = str(erro).lower()
    return "não retornou conteúdo" in mensagem or "nao retornou conteudo" in mensagem


def _resumo_erro(erro: Exception) -> str:
    if _erro_de_quota(erro):
        return "quota do provider excedida"
    mensagem = " ".join(str(erro).split())
    for segredo in (GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY):
        if segredo:
            mensagem = mensagem.replace(segredo, "[redacted]")
    if len(mensagem) > 240:
        mensagem = mensagem[:237] + "..."
    return mensagem or erro.__class__.__name__


def _pergunta_composta(texto: str) -> bool:
    normalizado = _normalizar_texto(texto)
    if re.search(r"\bnem\b", normalizado) or "/" in texto or ";" in texto:
        return True
    if re.search(r"\b(ou|or)\b", normalizado):
        # Permite a especificação natural "artificial ou sintética", mas
        # rejeita alternativas independentes na mesma pergunta.
        sinonimos_naturais = (
            "artificial" in normalizado
            and bool(re.search(r"\b(sintetica|sintetico|synthetic)\b", normalizado))
        )
        return not sinonimos_naturais
    return False


gemini_client = genai.Client(api_key=GEMINI_API_KEY)
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
openrouter_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)


async def _chamar_openrouter(mensagens: list[dict]):
    """Lida com modelos gratuitos que alternam entre exigir raciocínio ou não."""
    async def solicitar(reasoning_effort: str):
        return await openrouter_client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=mensagens,
            reasoning_effort=reasoning_effort,
            max_tokens=6144,
        )

    try:
        response = await solicitar("none")
    except Exception as erro:
        if "reasoning is mandatory" not in str(erro).lower():
            raise
        return await solicitar("low")

    if _extrair_conteudo_resposta(response):
        return response
    return await solicitar("low")


def _validar_e_reparar(
    case: InvestigationCase,
    characters: list,
    exigir_minimo: bool = True,
    diagnostico: dict[str, int] | None = None,
    expected_culprit_id: int | None = None,
) -> InvestigationCase:
    """Rede de segurança: valida clues/culpado (isso sim invalida o caso
    inteiro, é estrutural) e FILTRA perguntas individualmente defeituosas
    (suspeito faltando, pergunta que não divide ninguém) em vez de jogar
    fora o caso todo por causa de 1 pergunta ruim entre 12 — a IA erra uma
    pergunta ocasional bem mais que erra o caso inteiro, então vale
    aproveitar o resto."""
    controlled_by_id = {character["id"]: character for character in MARVEL_CHARACTERS}
    character_data = [_to_dict(character) for character in characters]
    character_by_id = {character["id"]: character for character in character_data}
    controlled_ids = {character.get("id") for character in character_data}
    if len(character_data) != N_SUSPECTS or len(controlled_ids) != N_SUSPECTS:
        raise ValueError(f"O elenco recebido precisa ter exatamente {N_SUSPECTS} personagens distintos.")
    if not controlled_ids <= set(controlled_by_id):
        raise ValueError("O elenco contém personagem fora da lista controlada.")

    suspeitos_ids = {s.id for s in case.suspects}
    if len(case.suspects) != N_SUSPECTS or suspeitos_ids != controlled_ids:
        raise ValueError("O caso não contém exatamente os 10 personagens controlados recebidos.")

    # Identidade e nomes vêm sempre do catálogo; a IA só produz o conteúdo
    # narrativo. Isso impede IDs, nomes ou traduções inventados no JSON final.
    suspeitos_canonicos = [
        suspeito.model_copy(update={
            "name": controlled_by_id[suspeito.id]["name"],
            "name_pt": controlled_by_id[suspeito.id]["name_pt"],
            "image_url": character_by_id[suspeito.id].get("image_url"),
        })
        for suspeito in case.suspects
    ]
    case = case.model_copy(update={"suspects": suspeitos_canonicos})
    if case.culprit_id not in suspeitos_ids:
        raise ValueError("culprit_id não pertence aos 10 suspeitos controlados.")
    if expected_culprit_id is not None and case.culprit_id != expected_culprit_id:
        raise ValueError(
            "O provider alterou o culpado sorteado pelo backend."
        )

    grupos_de_clues: set[tuple[int, ...]] = set()
    for clue in case.clues:
        descricao_normalizada = _normalizar_texto(clue.description)
        relacionados = tuple(sorted(set(clue.related_suspects)))
        if (
            not clue.description.strip()
            or not clue.related_suspects
            or len(clue.related_suspects) != len(set(clue.related_suspects))
            or not set(clue.related_suspects) <= suspeitos_ids
            or len(relacionados) < 2
            or len(relacionados) > 6
            or relacionados in grupos_de_clues
            or _menciona_identidade(clue.description, characters)
            or any(
                marcador in descricao_normalizada
                for marcador in (
                    "all suspects have an official name listed",
                    "todos os suspeitos tem um nome oficial listado",
                    "apenas um suspeito",
                    "somente um suspeito",
                    "unico suspeito",
                    "único suspeito",
                )
            )
        ):
            raise ValueError("Clue vazia, repetida ou individualizante.")
        grupos_de_clues.add(relacionados)

    if case.clues and not any(
        case.culprit_id == suspect_id
        for clue in case.clues
        for suspect_id in clue.related_suspects
    ):
        raise ValueError("Nenhuma clue relaciona o culpado a uma hipótese concorrente.")

    if diagnostico is not None:
        diagnostico["geradas"] = len(case.questions)

    perguntas_boas: list[Question] = []
    rejeitadas_divisao = 0
    duplicadas = 0
    perguntas_estruturais: list[Question] = []
    for q in case.questions:
        vistos: set[int] = set()
        respostas_limpas = []
        for a in q.answers:
            if a.suspect_id in suspeitos_ids and a.suspect_id not in vistos:
                vistos.add(a.suspect_id)
                respostas_limpas.append(a)

        if vistos != suspeitos_ids:
            continue  # suspeito faltando/duplicado — descarta só ESSA pergunta

        if len({a.answer for a in respostas_limpas}) < 2:
            rejeitadas_divisao += 1
            continue  # não divide ninguém (todos true/false) — inútil, descarta só ela

        pergunta_limpa = Question(id=q.id, text=q.text.strip(), answers=respostas_limpas)
        if pergunta_limpa.text:
            perguntas_estruturais.append(pergunta_limpa)
        if (
            not pergunta_limpa.text
            or _pergunta_composta(pergunta_limpa.text)
            or _menciona_identidade(pergunta_limpa.text, characters)
        ):
            continue  # texto vazio ou identidade explícita
        if any(_sao_semelhantes(pergunta_limpa, anterior) for anterior in perguntas_boas):
            duplicadas += 1
            continue  # não desperdiçar slots com a mesma característica
        perguntas_boas.append(pergunta_limpa)

    candidatas_balanceadas = len(perguntas_boas)
    perguntas_boas = _selecionar_perguntas(perguntas_boas, len(suspeitos_ids))
    rejeitadas_divisao += max(0, candidatas_balanceadas - len(perguntas_boas))

    # Se o modelo criou perguntas com respostas completas, mas o filtro de
    # qualidade eliminou todas, reaproveita a estrutura antes de abandonar o
    # provider. A seleção ainda remove perguntas constantes e prioriza as
    # divisões equilibradas.
    if exigir_minimo and len(perguntas_boas) < MIN_QUESTIONS:
        perguntas_estruturais_unicas: list[Question] = []
        for pergunta in perguntas_estruturais:
            if any(_sao_semelhantes(pergunta, anterior) for anterior in perguntas_estruturais_unicas):
                continue
            perguntas_estruturais_unicas.append(pergunta)
        perguntas_boas = _selecionar_perguntas(
            perguntas_estruturais_unicas,
            len(suspeitos_ids),
        )

    if exigir_minimo and len(perguntas_boas) < MIN_QUESTIONS:
        raise ValueError(f"Só sobraram {len(perguntas_boas)} perguntas boas depois de filtrar defeituosas, precisa de pelo menos {MIN_QUESTIONS}.")

    if diagnostico is not None:
        diagnostico["aprovadas"] = len(perguntas_boas)
        diagnostico["rejeitadas_divisao"] = rejeitadas_divisao
        diagnostico["duplicadas"] = duplicadas

    return case.model_copy(update={"questions": perguntas_boas})


def _acumular_perguntas(
    acumuladas: list[Question],
    novas: list[Question],
    diagnostico: dict[str, int] | None = None,
) -> list[Question]:
    resultado = list(acumuladas)
    ids_existentes = {pergunta.id for pergunta in resultado}
    duplicadas = 0
    for pergunta in novas:
        if len(resultado) >= N_QUESTIONS:
            break
        if pergunta.id in ids_existentes:
            duplicadas += 1
            continue
        if any(_sao_semelhantes(pergunta, anterior) for anterior in resultado):
            duplicadas += 1
            continue
        resultado.append(pergunta)
        ids_existentes.add(pergunta.id)
    if diagnostico is not None:
        diagnostico["duplicadas"] = diagnostico.get("duplicadas", 0) + duplicadas
    return resultado


async def _com_retry(
    gerar_caso,
    gerar_adicionais,
    characters: list,
    culprit_id: int,
    tentativas: int = 3,
    provider: str = "Provider",
) -> InvestigationCase:
    """Gera o caso uma vez e complementa somente as perguntas aprovadas."""
    ultimo_erro: Exception | None = None
    caso_base: InvestigationCase | None = None
    perguntas_aprovadas: list[Question] = []
    rodadas_executadas = 0
    for tentativa in range(tentativas):
        rodadas_executadas += 1
        rodada = tentativa + 1
        complementar = caso_base is not None
        diagnostico: dict[str, int] = {}
        try:
            if complementar:
                print(
                    f"[QUESTIONS] Provider={provider} | "
                    f"Iniciando rodada complementar={rodada} | "
                    f"Acumuladas antes={len(perguntas_aprovadas)} | "
                    f"Necessárias={max(0, MIN_QUESTIONS - len(perguntas_aprovadas))}"
                )

            if not complementar:
                case = await gerar_caso(characters, culprit_id)
                case = case.model_copy(
                    update={"questions": case.questions[:QUESTIONS_PER_CALL]}
                )
                parcial = _validar_e_reparar(
                    case,
                    characters,
                    exigir_minimo=False,
                    diagnostico=diagnostico,
                    expected_culprit_id=culprit_id,
                )
                caso_base = parcial
            else:
                adicionais = await gerar_adicionais(characters, perguntas_aprovadas)
                adicionais = adicionais.model_copy(
                    update={
                        "questions": adicionais.questions[:QUESTIONS_PER_CALL]
                    }
                )
                print(
                    f"[QUESTIONS] Provider={provider} | Rodada={rodada} | "
                    f"Perguntas complementares recebidas={len(adicionais.questions)}"
                )
                parcial = _validar_e_reparar(
                    caso_base.model_copy(update={"questions": adicionais.questions}),
                    characters,
                    exigir_minimo=False,
                    diagnostico=diagnostico,
                    expected_culprit_id=culprit_id,
                )

            perguntas_aprovadas = _acumular_perguntas(
                perguntas_aprovadas,
                parcial.questions,
                diagnostico,
            )

            if diagnostico.get("rejeitadas_divisao", 0):
                print(
                    f"[QUESTIONS] Provider={provider} | Rodada={rodada} | "
                    f"Rejeitadas por divisão={diagnostico['rejeitadas_divisao']}"
                )
            if diagnostico.get("duplicadas", 0):
                print(
                    f"[QUESTIONS] Provider={provider} | Rodada={rodada} | "
                    f"Duplicadas descartadas={diagnostico['duplicadas']}"
                )

            if complementar:
                print(
                    f"[QUESTIONS] Provider={provider} | Rodada={rodada} | "
                    f"Complementares aprovadas={len(parcial.questions)}"
                )
                print(
                    f"[QUESTIONS] Provider={provider} | Rodada={rodada} | "
                    f"Total acumulado={len(perguntas_aprovadas)}"
                )
            else:
                print(
                    f"[QUESTIONS] Provider={provider} | Geração principal | Rodada=1 | "
                    f"Geradas={diagnostico.get('geradas', len(case.questions))} | "
                    f"Aprovadas={len(parcial.questions)} | "
                    f"Acumuladas={len(perguntas_aprovadas)}"
                )

            if len(perguntas_aprovadas) >= MIN_QUESTIONS:
                print(
                    f"[QUESTIONS] Provider={provider} | Mínimo atingido | "
                    f"Total={len(perguntas_aprovadas)} | Rodadas={rodadas_executadas}"
                )
                return caso_base.model_copy(
                    update={"questions": perguntas_aprovadas[:N_QUESTIONS]}
                )
        except Exception as erro:
            ultimo_erro = erro
            if _erro_de_quota(erro):
                break
            if complementar:
                # JSON/schema inválido na rodada complementar pode ser
                # tentado novamente; erros definitivos do provider não.
                pode_tentar_novamente = _erro_transitorio(erro) or isinstance(erro, ValueError)
            else:
                # A geração inicial só repete falhas transitórias.
                pode_tentar_novamente = (
                    _erro_transitorio(erro)
                    or _erro_sem_conteudo(erro)
                    or (provider == "OpenRouter" and isinstance(erro, ValueError))
                )
            if not pode_tentar_novamente:
                break
            if tentativa + 1 < tentativas:
                print(f"Tentativa {tentativa + 1} falhou: {_resumo_erro(erro)}")
    if ultimo_erro is not None:
        print(
            f"[QUESTIONS] Provider={provider} | Falhou | "
            f"Total acumulado={len(perguntas_aprovadas)} | Rodadas={rodadas_executadas}"
        )
        raise ultimo_erro
    print(
        f"[QUESTIONS] Provider={provider} | Falhou | "
        f"Total acumulado={len(perguntas_aprovadas)} | Rodadas={rodadas_executadas}"
    )
    raise ValueError(
        f"Provider gerou apenas {len(perguntas_aprovadas)} perguntas válidas; "
        f"são necessárias pelo menos {MIN_QUESTIONS}."
    )


async def _gerar_gemini(characters: list, culprit_id: int) -> InvestigationCase:
    chat = gemini_client.aio.chats.create(
        model=GEMINI_MODEL,
        config={
            "response_mime_type": "application/json",
            "response_schema": InvestigationCase,
            "max_output_tokens": 6144,
        },
    )
    response = await chat.send_message(_build_prompt(characters, culprit_id))
    return InvestigationCase.model_validate_json(response.text)


async def _gerar_gemini_adicionais(
    characters: list,
    perguntas_aprovadas: list[Question],
) -> AdditionalQuestions:
    chat = gemini_client.aio.chats.create(
        model=GEMINI_MODEL,
        config={
            "response_mime_type": "application/json",
            "response_schema": AdditionalQuestions,
            "max_output_tokens": 6144,
        },
    )
    response = await chat.send_message(
        _build_additional_questions_prompt(characters, perguntas_aprovadas)
    )
    return AdditionalQuestions.model_validate_json(response.text)


async def _gerar_groq(characters: list, culprit_id: int) -> InvestigationCase:
    response = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": _build_prompt(characters, culprit_id)}],
        reasoning_effort="low",
        max_tokens=6144,
    )
    content = _extrair_conteudo_resposta(response)
    if not content:
        raise RuntimeError("Groq não retornou conteúdo.")
    return InvestigationCase.model_validate_json(_extrair_json(content))


async def _gerar_groq_adicionais(
    characters: list,
    perguntas_aprovadas: list[Question],
) -> AdditionalQuestions:
    response = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{
            "role": "user",
            "content": _build_additional_questions_prompt(characters, perguntas_aprovadas),
        }],
        reasoning_effort="low",
        max_tokens=6144,
    )
    content = _extrair_conteudo_resposta(response)
    if not content:
        raise RuntimeError("Groq não retornou perguntas adicionais.")
    return AdditionalQuestions.model_validate_json(_extrair_json(content))


async def _gerar_openrouter(characters: list, culprit_id: int) -> InvestigationCase:
    response = await _chamar_openrouter(
        [{"role": "user", "content": _build_prompt(characters, culprit_id)}]
    )

    content = _extrair_conteudo_resposta(response)

    if not content:
        raise RuntimeError("OpenRouter não retornou conteúdo.")

    return InvestigationCase.model_validate_json(_extrair_json(content))


async def _gerar_openrouter_adicionais(
    characters: list,
    perguntas_aprovadas: list[Question],
) -> AdditionalQuestions:
    response = await _chamar_openrouter(
        [{
            "role": "user",
            "content": _build_additional_questions_prompt(characters, perguntas_aprovadas),
        }]
    )
    content = _extrair_conteudo_resposta(response)
    if not content:
        raise RuntimeError("OpenRouter não retornou perguntas adicionais.")
    return AdditionalQuestions.model_validate_json(_extrair_json(content))


def _caso_fallback_estatico(
    characters: list | None = None,
    culprit_id: int | None = None,
) -> InvestigationCase:
    """Última rede de segurança: se Gemini, Groq E OpenRouter falharem
    juntos (rate limit, fora do ar, o que for), o jogo continua jogável
    com este caso fixo em vez de dar 500 pro jogador."""
    catalog_by_id = {character["id"]: character for character in MARVEL_CHARACTERS}
    received = [_to_dict(character) for character in (characters or [])]
    catalog = [catalog_by_id[item["id"]] for item in received if item.get("id") in catalog_by_id]
    if len(catalog) != N_SUSPECTS or len({character["id"] for character in catalog}) != N_SUSPECTS:
        catalog = MARVEL_CHARACTERS[:N_SUSPECTS]
        received = []
    received_by_id = {item["id"]: item for item in received}
    ids = [character["id"] for character in catalog]
    fallback_culprit_id = culprit_id if culprit_id in ids else random.choice(ids)
    alibis = [
        "revisando o painel de segurança na sala de controle; o registro de acesso confirma sua entrada às 20h50",
        "conversando com convidados no salão principal; duas testemunhas dizem ter falado com essa pessoa às 21h10",
        "checando o gerador no subsolo; um técnico afirma tê-la visto sair às 21h05",
        "organizando equipamentos na oficina; uma câmera interna registra movimentação entre 20h55 e 21h20",
        "em uma ligação privada na varanda leste; o histórico do comunicador marca atividade contínua às 21h12",
        "acompanhando a equipe médica na ala norte; a chefe da equipe confirma sua presença durante o blecaute",
        "fazendo uma ronda no estacionamento; o leitor externo registra sua credencial às 21h08",
        "na cozinha, ajudando a preparar o serviço; funcionários relatam que permaneceu ali até 21h15",
        "consultando os mapas do prédio na biblioteca; o terminal foi acessado em seu nome às 21h11",
        "descansando no quarto de hóspedes após uma reunião; uma chamada registrada mostra que estava no local às 21h14",
    ]
    suspects = [
        {
            "id": character["id"],
            "name": character["name"],
            "name_pt": character["name_pt"],
            "image_url": received_by_id.get(character["id"], {}).get("image_url"),
            "description": (
                str(received_by_id.get(character["id"], {}).get("deck") or "")[:300]
                or f"{character['name_pt']} é um personagem da lista controlada do caso."
            ),
            "crime_moment": (
                f"{character['name_pt']} afirma que estava {alibis[index]}."
            ),
        }
        for index, character in enumerate(catalog)
    ]
    true_sets = [
        {0, 1, 2, 3, 4},
        {0, 1, 2, 5, 6},
        {0, 1, 3, 5, 7},
        {0, 2, 3, 5, 8},
        {0, 2, 4, 6, 9},
        {1, 3, 4, 7, 8},
        {2, 4, 6, 8, 9},
        {0, 1, 5, 7, 9},
        {0, 2, 4, 7, 8},
        {1, 2, 6, 8, 9},
    ]
    question_texts = [
        "O suspeito tinha acesso ao perímetro interno da base?",
        "O suspeito foi visto circulando antes do blecaute?",
        "O suspeito possui treinamento útil para uma operação de segurança?",
        "O suspeito teve contato com a equipe de vigilância no evento?",
        "O suspeito conhecia algum procedimento técnico do local?",
        "O suspeito permaneceu nas dependências durante o apagão?",
        "O suspeito tinha um motivo plausível para estar no setor restrito?",
        "O suspeito conhecia a rotina de patrulha do local?",
        "O suspeito tinha uma justificativa registrada para acessar a área?",
        "O suspeito esteve perto do cofre antes do desaparecimento?",
    ]
    payload = {
        "id": 0,
        "culprit_id": fallback_culprit_id,
        "description": "Um artefato desapareceu do cofre da base durante um blecaute breve.",
        "suspects": suspects,
        "clues": [
            {"id": 1, "description": "O painel do cofre foi desligado durante uma janela curta de acesso restrito.", "related_suspects": [ids[0], ids[1], ids[2], ids[3], ids[4]]},
            {"id": 2, "description": "O registro de entrada mostra movimentação em dois corredores durante o blecaute.", "related_suspects": [ids[0], ids[5], ids[6], ids[7]]},
            {"id": 3, "description": "Uma testemunha confirmou que parte da equipe permaneceu no salão principal.", "related_suspects": [ids[0], ids[8], ids[9]]},
        ],
        "questions": [
            {"id": index, "text": text, "answers": [
                {"suspect_id": suspect_id, "answer": position in true_set}
                for position, suspect_id in enumerate(ids)
            ]}
            for index, (text, true_set) in enumerate(zip(question_texts, true_sets), start=1)
        ],
    }
    return InvestigationCase.model_validate(payload)


async def generate_case(characters: list[dict]) -> InvestigationCase:
    character_ids = [_to_dict(character).get("id") for character in characters]
    if len(character_ids) != N_SUSPECTS or len(set(character_ids)) != N_SUSPECTS:
        raise ValueError(f"O elenco recebido precisa ter exatamente {N_SUSPECTS} personagens distintos.")
    culprit_id = random.choice(character_ids)

    try:
        return await _com_retry(
            _gerar_gemini,
            _gerar_gemini_adicionais,
            characters,
            culprit_id,
            tentativas=3,
            provider="Gemini",
        )
    except Exception as gemini_error:
        print(f"Gemini falhou: {_resumo_erro(gemini_error)}")
        print("Tentando Groq...")

    try:
        return await _com_retry(
            _gerar_groq,
            _gerar_groq_adicionais,
            characters,
            culprit_id,
            tentativas=4,
            provider="Groq",
        )
    except Exception as groq_error:
        print(f"Groq falhou: {_resumo_erro(groq_error)}")
        print("Tentando OpenRouter...")

    try:
        return await _com_retry(
            _gerar_openrouter,
            _gerar_openrouter_adicionais,
            characters,
            culprit_id,
            tentativas=4,
            provider="OpenRouter",
        )
    except Exception as openrouter_error:
        print(f"OpenRouter falhou: {_resumo_erro(openrouter_error)}")

    print("Todos os provedores de IA falharam. Usando caso fallback estático.")
    return _caso_fallback_estatico(characters, culprit_id)
