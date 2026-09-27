import json

from google import genai
from groq import AsyncGroq
from openai import AsyncOpenAI

from app.config import (
  GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY,
  GEMINI_MODEL, GROQ_MODEL, OPENROUTER_MODEL
)
from app.schemas import InvestigationCase, Question

N_QUESTIONS = 12  # pedido no prompt, ideal
MIN_QUESTIONS = 7  # aceitável de verdade — modelos não seguem número exato com confiabilidade

# PROMPT FORMATO JSON
JSON_PROMPT = f"""
FORMATO OBRIGATÓRIO DO JSON:

- O campo raiz deve se chamar "id", nunca "case_id".
- Cada suspect deve possuir exatamente os campos: id, name, description, crime_moment.
- Cada clue deve ser um objeto com os campos: id, description, related_suspects.
- Cada question deve ser um objeto com os campos: id, text, answers.
  "answers" é uma LISTA de objetos, um por suspeito, cada um com:
  suspect_id (o id do suspeito) e answer (true ou false).
  TODOS os 5 suspeitos precisam aparecer em "answers" de TODA pergunta —
  ou seja, cada "answers" tem exatamente 5 itens.
- Deve haver exatamente 5 suspects.
- Deve haver exatamente 3 clues.
- Deve haver PELO MENOS {MIN_QUESTIONS} questions (o ideal é {N_QUESTIONS}, mas nunca menos que {MIN_QUESTIONS}).
- Todos os IDs dos suspects devem ser diferentes.
- culprit_id deve corresponder ao id de exatamente um dos suspects.
- related_suspects deve conter apenas IDs dos suspects.
- Retorne exclusivamente um JSON válido.
- Não altere os nomes dos campos definidos acima.

EXEMPLO DO FORMATO OBRIGATÓRIO (com só 2 questions pra caber aqui — no caso
real são {N_QUESTIONS}):

{{
  "id": 1234,
  "culprit_id": 5678,
  "description": *A descrição deve ser super criativa, seguindo de acordo com os poderes e contexto do personagem de uma forma que não indicie o culpado. Pode ser um crime, roubo, assassinato, invasão mascarada, infinitas possibilidades. Deve ter bastante capricho e detalhes, mas sem ficar muito grande.*.",
  "suspects": [
    {{
      "id": 5678,
      "name": "Example Character 1",
      "description": "Descrição breve do suspeito.",
      "crime_moment": "Afirma que estava em outro local, mas a explicação tem uma lacuna sutil de horário."
    }},
    {{
      "id": 9012,
      "name": "Example Character 2",
      "description": "Descrição breve do suspeito.",
      "crime_moment": "Ouviram afirmar que estava conversando com uma testemunha confiável."
    }},
    {{
      "id": 3456,
      "name": "Example Character 3",
      "description": "Descrição breve do suspeito.",
      "crime_moment": "Afirmou que estava investigando outro local, com registro que confirma."
    }},
    {{
      "id": 7890,
      "name": "Example Character 4",
      "description": "Descrição breve do suspeito.",
      "crime_moment": "Comentou que estava chegando ao evento no momento do crime."
    }},
    {{
      "id": 2468,
      "name": "Example Character 5",
      "description": "Descrição breve do suspeito.",
      "crime_moment": "Falou que estava deixando o local antes do crime acontecer."
    }}
  ],
  "clues": [
    {{
      "id": 1,
      "description": "Um objeto compatível com uma habilidade do culpado foi encontrado próximo ao cofre — mas também poderia pertencer a outro suspeito com poder parecido.",
      "related_suspects": [5678, 9012]
    }},
    {{
      "id": 2,
      "description": "Uma gravação mostra uma inconsistência de horário no depoimento do culpado.",
      "related_suspects": [5678]
    }},
    {{
      "id": 3,
      "description": "Uma evidência física exclui um dos suspeitos inocentes, sem tocar diretamente no culpado.",
      "related_suspects": [3456]
    }}
  ],
  "questions": [
    {{
      "id": 1,
      "text": "O suspeito possui algum tipo de arma ou apêndice mecânico/metálico?",
      "answers": [
        {{"suspect_id": 5678, "answer": true}},
        {{"suspect_id": 9012, "answer": false}},
        {{"suspect_id": 3456, "answer": false}},
        {{"suspect_id": 7890, "answer": false}},
        {{"suspect_id": 2468, "answer": false}}
      ]
    }},
    {{
      "id": 2,
      "text": "O suspeito tem ligação direta com alguma equipe reconhecida de heróis?",
      "answers": [
        {{"suspect_id": 5678, "answer": true}},
        {{"suspect_id": 9012, "answer": true}},
        {{"suspect_id": 3456, "answer": false}},
        {{"suspect_id": 7890, "answer": true}},
        {{"suspect_id": 2468, "answer": false}}
      ]
    }}
  ]
}}

IMPORTANTE:

- Este é apenas um exemplo de estrutura (com menos questions do que o real).
- Os personagens e valores do exemplo NÃO devem ser utilizados na resposta.
- O caso real deve utilizar exclusivamente os personagens fornecidos.
- Os 5 suspects devem ser personagens diferentes da lista fornecida.
- Não invente personagens.
- Não invente poderes/habilidades que não estejam na descrição fornecida do personagem.
- Retorne exclusivamente um JSON válido.
- Não altere os nomes dos campos.
"""

# REGRAS DE SOLUCIONABILIDADE — a parte que faltava.
# Sem isso, nada garante que exista um caminho de dedução até o culpado.
SOLVABILITY_RULES = """
REGRAS DE SOLUCIONABILIDADE (as mais importantes — um caso que não obedece
isso é um caso quebrado, mesmo que o JSON seja válido):

- O culprit_id DEVE aparecer em related_suspects de PELO MENOS 2 das 3 clues.
  Nunca deixe o culpado fora de todas as pistas — isso torna o caso
  impossível de resolver por lógica, só por chute.
- Cada um dos outros 4 suspeitos (inocentes) deve aparecer em PELO MENOS 1
  clue — seja como red herring (parece culpado mas tem explicação) ou como
  alguém que uma clue EXCLUI (evidência que não bate com ele).
- Nas questions: cada pergunta deve dividir os 5 suspeitos de forma
  DESIGUAL (nunca todos true ou todos false — isso não elimina ninguém e
  é uma pergunta inútil).
  O conjunto de respostas de CADA suspeito, olhando TODAS AS PERGUNTAS
  GERADAS juntas, precisa ser ÚNICO. — nenhum par de
  suspeitos pode ter exatamente o mesmo padrão de true/false em todas as
  perguntas, senão fica impossível distinguir os dois.
- Baseie as respostas nos poderes/afiliação/perfil REAIS de cada
  personagem fornecido — nunca invente características aleatórias.
- O crime_moment do culpado deve ter uma inconsistência sutil e específica
  (horário que não fecha, poder que não explica o método, local que
  contradiz uma clue) — sutil o bastante pra não entregar de cara, mas
  real o bastante pra alguma clue apontar pra ela.
- Os crime_moment dos suspeitos inocentes devem ser consistentes entre si
  e com pelo menos uma clue que os isenta (parcial ou totalmente).
- Nunca revele o nome do culpado literalmente em description, clues ou
  questions — a solução vem de cruzar clue + crime_moment + answers, não
  de uma frase que entrega a resposta.
"""


def _to_dict(character) -> dict:
    """Aceita tanto CharacterData (pydantic) quanto dict — não depende de
    quem chama já ter feito .model_dump()."""
    return character.model_dump() if hasattr(character, "model_dump") else character


def _build_prompt(characters: list) -> str:
    characters_json = json.dumps([_to_dict(c) for c in characters], ensure_ascii=False, indent=2)
    return f"""
Você é o responsável por criar casos investigativos para o jogo Shield Noir.

Crie um caso de investigação usando exclusivamente os personagens fornecidos.

REGRAS:
- Existem exatamente 5 suspeitos.
- Exatamente 1 dos 5 suspeitos é o culpado.
- Todos os suspeitos devem ser personagens fornecidos.
- Cada suspeito deve possuir um crime_moment plausível.
- Crie exatamente 3 pistas (clues).
- Crie PELO MENOS {MIN_QUESTIONS} perguntas (questions) de sim/não — o ideal
  é {N_QUESTIONS}, mas nunca menos que {MIN_QUESTIONS} — criadas
  especificamente pra ESTE elenco de suspeitos (não use sempre as mesmas
  perguntas — varie categoria: arma/poder, equipe, época de origem, local,
  personalidade, história, o que fizer sentido pros personagens sorteados).
- culprit_id deve corresponder ao id de um dos suspeitos.
- Não invente personagens que não estejam na lista fornecida.
- Retorne exclusivamente um JSON válido seguindo a estrutura fornecida.

{SOLVABILITY_RULES}

{JSON_PROMPT}

PERSONAGENS:
{characters_json}
"""


gemini_client = genai.Client(api_key=GEMINI_API_KEY)
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
openrouter_client = AsyncOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url="https://openrouter.ai/api/v1",
)


def _validar_e_reparar(case: InvestigationCase) -> InvestigationCase:
    """Rede de segurança: valida clues/culpado (isso sim invalida o caso
    inteiro, é estrutural) e FILTRA perguntas individualmente defeituosas
    (suspeito faltando, pergunta que não divide ninguém) em vez de jogar
    fora o caso todo por causa de 1 pergunta ruim entre 12 — a IA erra uma
    pergunta ocasional bem mais que erra o caso inteiro, então vale
    aproveitar o resto."""
    suspeitos_ids = {s.id for s in case.suspects}

    clues_do_culpado = [c for c in case.clues if case.culprit_id in c.related_suspects]
    if not clues_do_culpado:
        raise ValueError("Caso gerado sem nenhuma clue apontando pro culprit_id — não solucionável.")

    suspeitos_com_clue = {sid for c in case.clues for sid in c.related_suspects}
    sem_clue = suspeitos_ids - suspeitos_com_clue
    if sem_clue:
        raise ValueError(f"Suspeitos sem nenhuma clue relacionada: {sem_clue} — dedução incompleta.")

    perguntas_boas: list[Question] = []
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
            continue  # não divide ninguém (todos true/false) — inútil, descarta só ela

        perguntas_boas.append(Question(id=q.id, text=q.text, answers=respostas_limpas))

    if len(perguntas_boas) < MIN_QUESTIONS:
        raise ValueError(f"Só sobraram {len(perguntas_boas)} perguntas boas depois de filtrar defeituosas, precisa de pelo menos {MIN_QUESTIONS}.")

    padroes_por_suspeito: dict[int, tuple] = {sid: () for sid in suspeitos_ids}
    for q in perguntas_boas:
        respostas_por_id = {a.suspect_id: a.answer for a in q.answers}
        for sid in suspeitos_ids:
            padroes_por_suspeito[sid] += (respostas_por_id[sid],)

    if len(set(padroes_por_suspeito.values())) < len(padroes_por_suspeito):
        raise ValueError("Dois suspeitos têm o mesmo padrão de respostas em todas as perguntas — não dá pra distinguir.")

    return case.model_copy(update={"questions": perguntas_boas})


async def _com_retry(gerar, characters: list, tentativas: int = 3) -> InvestigationCase:
    """Regenera até `tentativas` vezes no MESMO provedor antes de desistir —
    uma falha de validação é sobre o conteúdo gerado (estocástico), não
    sobre o provedor estar fora do ar, então vale tentar de novo primeiro."""
    ultimo_erro: Exception | None = None
    for _ in range(tentativas):
        try:
            case = await gerar(characters)
            return _validar_e_reparar(case)
        except Exception as erro:
            ultimo_erro = erro
    raise ultimo_erro


async def _gerar_gemini(characters: list) -> InvestigationCase:
    response = await gemini_client.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_prompt(characters),
        config={
            "response_mime_type": "application/json",
            "response_schema": InvestigationCase,
        },
    )
    return InvestigationCase.model_validate_json(response.text)


async def _gerar_groq(characters: list) -> InvestigationCase:
    response = await groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": _build_prompt(characters)}],
        response_format={"type": "json_object"},
        max_tokens=4096,  # sem isso, o JSON pode ser cortado no meio (12 perguntas x 5 respostas + suspeitos + pistas é payload grande)
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Groq não retornou conteúdo.")
    return InvestigationCase.model_validate_json(content)


async def _gerar_openrouter(characters: list) -> InvestigationCase:
    response = await openrouter_client.chat.completions.create(
        model=OPENROUTER_MODEL,
        messages=[{"role": "user", "content": _build_prompt(characters)}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "investigation_case",
                "strict": True,
                "schema": InvestigationCase.model_json_schema(),
            },
        },
        max_tokens=8192,
    )

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("OpenRouter não retornou conteúdo.")

    return InvestigationCase.model_validate_json(content)


def _caso_fallback_estatico() -> InvestigationCase:
    """Última rede de segurança: se Gemini, Groq E OpenRouter falharem
    juntos (rate limit, fora do ar, o que for), o jogo continua jogável
    com este caso fixo em vez de dar 500 pro jogador."""
    payload = {
        "id": 0,
        "culprit_id": 1,
        "description": "Um artefato desapareceu do cofre da base durante um blecaute breve.",
        "suspects": [
            {"id": 1, "name": "Suspeito A", "description": "Presente no evento.", "crime_moment": "Alega ter ficado sozinho na sala de controle no horário do apagão."},
            {"id": 2, "name": "Suspeito B", "description": "Presente no evento.", "crime_moment": "Diz que estava acompanhado por testemunhas o tempo todo."},
            {"id": 3, "name": "Suspeito C", "description": "Presente no evento.", "crime_moment": "Afirma ter chegado depois do horário do roubo."},
            {"id": 4, "name": "Suspeito D", "description": "Presente no evento.", "crime_moment": "Relata ter saído do local antes do apagão."},
            {"id": 5, "name": "Suspeito E", "description": "Presente no evento.", "crime_moment": "Conta que estava monitorando as câmeras externas."},
        ],
        "clues": [
            {"id": 1, "description": "O painel elétrico foi desligado manualmente, exigindo acesso restrito.", "related_suspects": [1]},
            {"id": 2, "description": "Câmeras externas não registraram nada de anormal.", "related_suspects": [5]},
            {"id": 3, "description": "Uma testemunha confirma a presença constante do Suspeito B ao seu lado.", "related_suspects": [2]},
        ],
        "questions": [
            {"id": i, "text": t, "answers": [{"suspect_id": sid, "answer": a} for sid, a in zip([1, 2, 3, 4, 5], vals)]}
            for i, (t, vals) in enumerate([
                ("O suspeito tinha acesso à sala de controle?", [True, False, False, False, False]),
                ("O suspeito foi visto por alguma testemunha no horário do crime?", [False, True, False, True, True]),
                ("O suspeito chegou ao local antes do apagão?", [True, True, False, True, True]),
                ("O suspeito permaneceu no local depois do apagão?", [True, True, True, False, True]),
                ("O suspeito tem algum vínculo direto com o sistema elétrico do prédio?", [True, False, False, False, True]),
            ], start=1)
        ],
    }
    return InvestigationCase.model_validate(payload)


async def generate_case(characters: list[dict]) -> InvestigationCase:
    try:
        return await _com_retry(_gerar_gemini, characters, tentativas=3)
    except Exception as gemini_error:
        print(f"Gemini falhou, erro: {gemini_error}")

    try:
        return await _com_retry(_gerar_groq, characters)
    except Exception as groq_error:
        print(f"Groq falhou, erro: {groq_error}")

    try:
        return await _com_retry(_gerar_openrouter, characters)
    except Exception as openrouter_error:
        print(f"OpenRouter falhou, erro: {openrouter_error}")

    print("Todos os provedores de IA falharam — usando caso fallback estático.")
    return _caso_fallback_estatico()