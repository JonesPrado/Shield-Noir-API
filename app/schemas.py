from pydantic import BaseModel

class CharacterData(BaseModel):
    id: int
    name: str
    real_name: str | None = None
    deck: str | None = None
    powers: list[dict] = []

class Suspect(BaseModel):
    id: int
    name: str
    description: str
    crime_moment: str


class Clue(BaseModel):
    id: int
    description: str
    related_suspects: list[int]


class SuspectAnswer(BaseModel):
    suspect_id: int
    answer: bool


class Question(BaseModel):
    id: int
    text: str
    # lista de objetos, NUNCA dict — Gemini Developer API rejeita
    # "additionalProperties" (o que um dict vira em JSON Schema) no modo
    # de saída estruturada.
    answers: list[SuspectAnswer]


class InvestigationCase(BaseModel):
    id: int
    culprit_id: int
    description: str
    suspects: list[Suspect]
    clues: list[Clue]
    questions: list[Question]