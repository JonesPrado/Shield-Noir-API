from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt

class CharacterData(BaseModel):
    id: StrictInt
    name: str
    name_pt: str = ""
    image_url: str | None = None
    real_name: str | None = None
    deck: str | None = None
    powers: list[dict] = []

class Suspect(BaseModel):
    id: StrictInt
    name: str
    name_pt: str = ""
    image_url: str | None = None
    description: str
    crime_moment: str


class Clue(BaseModel):
    id: StrictInt
    description: str
    related_suspects: list[StrictInt]


class SuspectAnswer(BaseModel):
    suspect_id: StrictInt
    answer: StrictBool


class Question(BaseModel):
    id: StrictInt
    text: str
    # lista de objetos, NUNCA dict — Gemini Developer API rejeita
    # "additionalProperties" (o que um dict vira em JSON Schema) no modo
    # de saída estruturada.
    answers: list[SuspectAnswer]


class InvestigationCase(BaseModel):
    id: StrictInt
    culprit_id: StrictInt
    description: str
    suspects: list[Suspect]
    clues: list[Clue]
    questions: list[Question]


class AdditionalQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[Question]
