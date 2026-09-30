# Shield Noir — Contexto Completo para Continuação por Outra IA

> Este arquivo é o contexto operacional do projeto Shield Noir. Leia-o inteiro antes de alterar qualquer arquivo.
>
> Data da última consolidação: 2026-09-29.
>
> Nunca incluir neste arquivo, em commits ou em respostas: valores de `.env`, chaves de Comic Vine, chaves Gemini, Groq ou OpenRouter.

## 1. Resumo do produto

# Shield Noir — Contexto Completo para Continuação por Outra IA

> Este arquivo é o contexto operacional do projeto Shield Noir. Leia-o inteiro antes de alterar qualquer arquivo.
>
> Data da última consolidação: 2026-09-29.
>
> Nunca incluir neste arquivo, em commits ou em respostas: valores de `.env`, chaves de Comic Vine, chaves Gemini, Groq ou OpenRouter.

## 1. Resumo do produto

Shield Noir é um jogo Android de dedução no estilo Cluedo, com personagens Marvel reais obtidos por um backend próprio.

O jogador recebe um caso com 10 suspeitos, lê o crime, pistas e álibis/depoimentos, faz perguntas de sim/não e pode acusar qualquer suspeito ativo a qualquer momento.

Stack:

- Android em Kotlin.
- View System/XML, não Compose.
- `minSdk = 33`.
- Backend em FastAPI/Python.
- Backend implantado na Vercel.
- Comic Vine fornece os dados dos personagens.
- Providers de IA, nesta ordem: Gemini → Groq → OpenRouter → fallback estático.
- O app Android consome `POST /case/generate`.

Projetos:

- Mobile: `C:\Mobile\ShieldNoir\ShieldNoir`
- Backend: `C:\Mobile\ShieldNoir\shield-noir-ai`

O arquivo está na raiz do mobile apenas por organização, mas documenta os dois projetos.

---

## 2. Contrato público do backend

### Endpoint de saúde

```http
GET /health
```

Resposta:

```json
{"status":"ok"}
```

### Geração de caso

```http
POST /case/generate
```

Sem parâmetros e sem body obrigatório.

URL pública atualmente usada pelo Android:

```text
https://shield-noir-api.vercel.app/
```

URL completa:

```text
https://shield-noir-api.vercel.app/case/generate
```

O backend local antigo usava:

```text
http://10.0.2.2:8000/
```

`10.0.2.2` só serve para o emulador acessar o backend rodando no notebook. Não usar `localhost` nem `127.0.0.1` no Android. Em produção, não trocar o domínio da Vercel por IP: o HTTPS/certificado depende do domínio.

### Schema JSON do caso

```json
{
  "id": 987654,
  "culprit_id": 4578,
  "description": "Descrição do crime",
  "suspects": [
    {
      "id": 4578,
      "name": "Taskmaster",
      "name_pt": "Taskmaster",
      "image_url": "https://... ou null",
      "description": "Descrição factual do personagem",
      "crime_moment": "Álibi/depoimento do que ele afirma estar fazendo no momento do crime"
    }
  ],
  "clues": [
    {
      "id": 1,Shield Noir é um jogo Android de dedução no estilo Cluedo, com personagens Marvel reais obtidos por um backend próprio.

O jogador recebe um caso com 10 suspeitos, lê o crime, pistas e álibis/depoimentos, faz perguntas de sim/não e pode acusar qualquer suspeito ativo a qualquer momento.

Stack:

- Android em Kotlin.
- View System/XML, não Compose.
- `minSdk = 33`.
- Backend em FastAPI/Python.
- Backend implantado na Vercel.
- Comic Vine fornece os dados dos personagens.
- Providers de IA, nesta ordem: Gemini → Groq → OpenRouter → fallback estático.
- O app Android consome `POST /case/generate`.

Projetos:

- Mobile: `C:\Mobile\ShieldNoir\ShieldNoir`
- Backend: `C:\Mobile\ShieldNoir\shield-noir-ai`

O arquivo está na raiz do mobile apenas por organização, mas documenta os dois projetos.

---

## 2. Contrato público do backend

### Endpoint de saúde

```http
GET /health
```

Resposta:

```json
{"status":"ok"}
```

### Geração de caso

```http
POST /case/generate
```

Sem parâmetros e sem body obrigatório.

URL pública atualmente usada pelo Android:

```text
https://shield-noir-api.vercel.app/
```

URL completa:

```text
https://shield-noir-api.vercel.app/case/generate
```

O backend local antigo usava:

```text
http://10.0.2.2:8000/
```

`10.0.2.2` só serve para o emulador acessar o backend rodando no notebook. Não usar `localhost` nem `127.0.0.1` no Android. Em produção, não trocar o domínio da Vercel por IP: o HTTPS/certificado depende do domínio.

### Schema JSON do caso

```json
{
  "id": 987654,
  "culprit_id": 4578,
  "description": "Descrição do crime",
  "suspects": [
    {
      "id": 4578,
      "name": "Taskmaster",
      "name_pt": "Taskmaster",
      "image_url": "https://... ou null",
      "description": "Descrição factual do personagem",
      "crime_moment": "Álibi/depoimento do que ele afirma estar fazendo no momento do crime"
    }
  ],
  "clues": [
    {
      "id": 1,
      "description": "Pista em texto",
      "related_suspects": [4578, 1493]
    }
  ],
  "questions": [
    {
      "id": 1,
      "text": "O suspeito possui determinada característica?",
      "answers": [
        {"suspect_id": 4578, "answer": true}
      ]
    }
  ]
}
```

Regras estruturais:

- O caso tem exatamente 10 suspeitos distintos.
- Todos os 10 IDs devem estar na lista controlada `MARVEL_CHARACTERS`.
- `culprit_id` deve ser um dos 10 IDs.
- Cada pergunta válida tem exatamente 10 respostas, uma por suspeito.
- `answer` é booleano JSON real, nunca a string `"true"`/`"false"`.
- `clues.related_suspects` contém IDs existentes no elenco.
- `image_url` vem da Comic Vine e pode ser `null`.
- `crime_moment` é um depoimento/álibi plausível, não uma habilidade nem uma descrição genérica.

---

## 3. Mecânica do jogo — não quebrar

A mecânica é baseada na resposta do culpado, não em uma interpretação local da pergunta.

Para cada pergunta escolhida pelo jogador:

1. Encontrar a resposta daquela pergunta para `culprit_id`.
2. Para cada suspeito, comparar a resposta dele com a resposta do culpado.
3. Se forem diferentes, eliminar o suspeito.
4. Se forem iguais, ele continua ativo.

Formalmente:

```text
resposta_culpado = question.answers[culprit_id]
eliminar(suspeito) se question.answers[suspeito_id] != resposta_culpado
```

O jogador não precisa eliminar todos os outros suspeitos antes de acusar. Pode acusar qualquer suspeito ainda ativo a qualquer momento.

Patente no Android, baseada na quantidade de perguntas usadas:

- Acertou e usou `<= 3`: `DETETIVE LENDÁRIO`
- Acertou e usou `<= 6`: `DETETIVE SÊNIOR`
- Acertou e usou `<= 9`/caso restante: `DETETIVE JÚNIOR`
- Errou: `ESTAGIÁRIO REBAIXADO`

O limite atual no mobile é 10 perguntas.

---

## 4. Backend — estado atual

Arquivos principais:

- `shield-noir-ai/app/routes/ai.py`
- `shield-noir-ai/app/routes/case.py`
- `shield-noir-ai/app/schemas.py`
- `shield-noir-ai/app/services/comic_vine.py`
- `shield-noir-ai/app/data/marvel_characters.py`
- `shield-noir-ai/app/config.py`
- `shield-noir-ai/vercel.json`

### Catálogo de personagens

`MARVEL_CHARACTERS` é a lista controlada de personagens. Ela contém atualmente personagens heróis, anti-heróis e vilões.

Não há filtro por alinhamento moral. Todos os personagens selecionados são elegíveis para serem culpados.

O catálogo possui campos básicos:

```python
{"name": "Iron Man", "id": 1455, "name_pt": "Homem de Ferro"}
```

Não alterar `MARVEL_CHARACTERS` nem `name_pt` sem uma tarefa explícita.

### Seleção e dados Comic Vine

`get_random_marvel_characters(amount=10)`:

- seleciona 10 entradas aleatórias com `random.sample`;
- consulta os IDs na Comic Vine em lote;
- exige que todos os IDs retornem;
- preserva a associação com o catálogo;
- cria `CharacterData`.

`CharacterData` atual:

```python
class CharacterData(BaseModel):
    id: StrictInt
    name: str
    name_pt: str = ""
    image_url: str | None = None
    real_name: str | None = None
    deck: str | None = None
    powers: list[dict] = []
```

### `image_url`

A URL é extraída diretamente de `character["image"]`, nesta ordem:

```text
super_url
screen_large_url
screen_url
medium_url
small_url
icon_url
```

Não inventar placeholder nem URL. Se nenhuma existir, retornar `null`.

O backend preserva a URL por ID até o `InvestigationCase` final. A IA não deve gerar nem ser responsável por preservar `image_url`.

### Sorteio do culpado

O culpado agora é sorteado no Python antes da chamada a qualquer provider:

```python
culprit_id = random.choice(character_ids)
```

Isso foi feito porque a IA tinha viés narrativo para escolher vilões/criminosos. Não há mais escolha livre do culpado pelo modelo.

O ID sorteado é:

- inserido no prompt principal;
- passado ao Gemini, Groq e OpenRouter;
- validado depois da resposta;
- reutilizado se houver troca de provider;
- reutilizado pelo fallback.

Se um provider devolver outro `culprit_id`, o caso é rejeitado. Não substituir silenciosamente o sorteio, porque isso quebraria a garantia de distribuição uniforme.

O fallback usa o culpado sorteado quando ele pertence ao elenco. Se for chamado isoladamente com dados inválidos, escolhe um ID válido do catálogo de fallback.

### Perguntas

Constantes atuais em `app/routes/ai.py`:

```python
N_SUSPECTS = 10
N_QUESTIONS = 10
MIN_QUESTIONS = 10
QUESTIONS_PER_CALL = 7
MAX_PROFILE_CHARS = 300
```

O motivo de `QUESTIONS_PER_CALL = 7` é limitar a resposta de cada chamada. O caso precisa acumular pelo menos 10 perguntas, normalmente em uma geração principal mais rodadas complementares.

O fluxo incremental:

1. Provider gera no máximo 7 perguntas principais e 3 clues.
2. Python valida e acumula as perguntas aprovadas.
3. Se ainda houver menos de 10, pede até 7 perguntas complementares.
4. Repite dentro do número limitado de tentativas do provider.
5. Se não atingir 10 perguntas válidas, o provider falha e o próximo é tentado.

Não implementar limite de 7 perguntas no jogador. O jogador continua podendo usar até 10.

### Distribuição permitida das perguntas

Com 10 suspeitos, a seleção final aceita somente divisões:

- 5 true / 5 false;
- 4 true / 6 false;
- 6 true / 4 false;
- 3 true / 7 false;
- 7 true / 3 false.

Não aceitar:

- 0/10;
- 1/9;
- 2/8;
- 8/2;
- 9/1;
- 10/0.

`_selecionar_perguntas()` prioriza 5/5, depois 4/6 ou 6/4, depois 3/7 ou 7/3, e retorna no máximo `N_QUESTIONS`.

Perguntas com nome, identidade, alter ego, característica claramente exclusiva, texto composto ou duplicidade semântica simples são filtradas.

As respostas são dados estruturados vindos do modelo, mas a validação Python garante:

- IDs completos e sem duplicatas;
- exatamente 10 respostas;
- booleanos;
- pelo menos dois grupos;
- perguntas não vazias;
- perguntas não compostas;
- ausência de identidade explícita;
- deduplicação simples por termos significativos.

Não criar NLP, embeddings, segundo agente ou dezenas de regexes sem necessidade clara. A prioridade atual é estabilidade e custo baixo.

### `crime_moment`

`crime_moment` deve ser dinâmico para cada personagem. É o que o suspeito afirma que estava fazendo no momento do crime para se defender.

Um bom `crime_moment` pode incluir:

- desculpa/versão do suspeito;
- horário aproximado;
- local;
- atividade;
- testemunha;
- câmera, log, credencial ou outro registro;
- uma inconsistência plausível, quando apropriado.

Não transformar `crime_moment` em uma lista de poderes ou em texto idêntico para todos. O prompt atual pede 1–2 frases, variação e plausibilidade.

### Prompts

`_build_prompt()` envia uma representação compacta dos personagens, não o objeto completo da Comic Vine.

Campos enviados para a IA:

- `id`;
- `name`;
- `name_pt`;
- `real_name`;
- `deck`, limitado a `MAX_PROFILE_CHARS`;
- até cinco poderes compactados.

Não enviar imagens, URLs, metadados, listas enormes ou dados irrelevantes para investigação.

O prompt exige:

- português brasileiro;
- exatamente 7 perguntas por chamada;
- clues curtas;
- perguntas simples de sim/não;
- uma característica por pergunta;
- fatos sustentados pelos perfis;
- nenhuma identidade direta;
- respostas completas para os 10 IDs;
- JSON válido sem markdown;
- `culprit_id` previamente sorteado pelo backend.

O prompt não deve voltar a conter exemplos gigantes do schema nem explicações redundantes das validações já feitas em Python.

### Providers e fallback

Ordem obrigatória:

```text
Gemini → Groq → OpenRouter → fallback estático
```

Não trocar provider, ordem ou arquitetura sem tarefa explícita.

Gemini:

- usa `response_mime_type = application/json`;
- usa `response_schema` Pydantic;
- usa `max_output_tokens = 6144`;
- quota excedida é encerrada sem insistência.

Groq:

- modelo definido em `app/config.py`;
- usa `reasoning_effort = "low"`;
- usa `max_tokens = 6144`;
- `message.content` é extraído de forma compatível com string/lista.

OpenRouter:

- usa `openrouter/free` conforme `app/config.py`;
- os modelos gratuitos podem mudar a cada chamada;
- alguns exigem raciocínio;
- outros funcionam melhor sem raciocínio;
- `_chamar_openrouter()` tenta `reasoning_effort = "none"`;
- se o modelo responder que raciocínio é obrigatório, tenta `low`;
- se retornar conteúdo vazio, tenta `low`;
- `choices=None` é tratado como conteúdo vazio, sem `TypeError`.

Retries atuais:

- Gemini: 3 tentativas configuradas;
- Groq: 4 tentativas configuradas;
- OpenRouter: 4 tentativas configuradas;
- quota/429: interromper imediatamente;
- erros transitórios: podem repetir;
- OpenRouter com conteúdo vazio ou JSON inválido pode consumir tentativa limitada.

O fallback estático é uma rede de segurança. Ele deve continuar retornando um caso jogável com 10 suspeitos, clues e 10 perguntas, mesmo sem IA. Não degradar a qualidade dele apenas para responder mais rápido.

### Latência conhecida do backend

O `GET /health` público respondeu corretamente `{"status":"ok"}`.

Porém, em teste posterior, um `POST /case/generate` público ficou sem resposta por mais de 180 segundos. Isso pode ocorrer quando providers estão sem quota, OpenRouter troca de modelo gratuito, há rodadas complementares ou chamadas sem timeout explícito.

A conclusão importante:

- o domínio e a internet do emulador estão funcionando;
- o problema do mobile não é `localhost`, `10.0.2.2` ou DNS básico;
- o `POST /case/generate` pode estar demorando indefinidamente;
- não resolver isso apenas aumentando o timeout do Android;
- o pipeline do backend precisa de orçamento de tempo por provider e um limite global, mantendo o fallback completo quando o limite acabar.

Próximo ajuste recomendado no backend, quando autorizado:

1. timeout explícito em cada cliente/provider;
2. abortar imediatamente quotas 429;
3. poucas tentativas para erros realmente transitórios;
4. deadline global de `/case/generate`;
5. ao atingir a deadline, retornar o fallback estático de alta qualidade;
6. não deixar chamada OpenRouter sem timeout.

Não fazer um fallback “mais pobre” para ficar rápido. O objetivo é interromper o bloqueio, não remover conteúdo.

---

## 5. Android — estado atual

Arquivos principais:

- `ShieldNoir/app/src/main/java/com/example/shieldnoir/MainActivity.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/network/RetrofitClient.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/network/CaseApiService.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/network/CaseDto.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/network/CaseMapper.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/ui/ShieldNoirViewModel.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/ui/EstadoJogo.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/model/*.kt`
- `ShieldNoir/app/src/main/java/com/example/shieldnoir/ui/adapter/SuspeitoAdapter.kt`
- `ShieldNoir/app/src/main/res/layout/activity_main.xml`
- `ShieldNoir/app/src/main/res/layout/item_suspeito.xml`
- `ShieldNoir/app/src/main/AndroidManifest.xml`

### Retrofit atual

```kotlin
private const val BASE_URL = "https://shield-noir-api.vercel.app/"
```

Service:

```kotlin
interface CaseApiService {
    @POST("case/generate")
    suspend fun generateCase(): InvestigationCaseDto
}
```

O Retrofit usa Gson e OkHttp.

Timeouts atuais:

```kotlin
connectTimeout(60, TimeUnit.SECONDS)
readTimeout(60, TimeUnit.SECONDS)
```

Há `HttpLoggingInterceptor.Level.BODY` ativo.

O `readTimeout` de 60 segundos é menor que a latência potencial observada no endpoint. Mas não aumentar cegamente antes de limitar o backend; um timeout enorme apenas deixa o usuário esperando.

### Manifest obrigatório

O manifest atual já possui:

```xml
<uses-permission android:name="android.permission.INTERNET" />
```

E:

```xml
<application android:usesCleartextTraffic="true" ...>
```

O domínio de produção usa HTTPS. `usesCleartextTraffic` é necessário para o backend local HTTP, mas não é a causa do problema HTTPS atual.

### Gradle atual

O projeto mobile mantém:

- AGP: `9.0.1`;
- Gradle wrapper: `9.1.0`;
- `compileSdk = 36`;
- `targetSdk = 36`;
- `minSdk = 33`;
- Kotlin/View System XML;
- `androidx.core:core-ktx = 1.18.0`;
- `androidx.activity:activity = 1.13.0`;
- `androidx.activity:activity-ktx = 1.13.0`;
- Retrofit `3.0.0`;
- OkHttp `5.1.0`;
- Coroutines `1.10.2`.

O problema anterior era `androidx.core:core-ktx/core:1.19.0` exigindo compileSdk 37 e AGP 9.1.0, enquanto o projeto usava AGP 9.0.1/compileSdk 36. A dependência foi mantida em `1.18.0` para a menor alteração compatível. Não atualizar novamente sem analisar compatibilidade.

O delegate `by viewModels()` vem de:

```kotlin
import androidx.activity.viewModels
```

E exige a dependência `androidx.activity:activity-ktx`.

### DTOs Android

O DTO já contempla a imagem:

```kotlin
data class SuspectDto(
    val id: Int,
    val name: String,
    @SerializedName("name_pt") val namePt: String? = null,
    @SerializedName("image_url") val imageUrl: String? = null,
    val description: String,
    @SerializedName("crime_moment") val crimeMoment: String,
)
```

O mapper leva `imageUrl` para o modelo de domínio `Suspeito`.

Se imagens forem exibidas, será necessária uma biblioteca de carregamento de imagem ou implementação equivalente. Não inventar placeholder no backend; `null` é permitido.

### ViewModel atual

`ShieldNoirViewModel`:

- começa em `BRIEFING`;
- ao abrir caso, vai para `CARREGANDO`;
- chama `RetrofitClient.caseApi.generateCase()` dentro de `viewModelScope`;
- converte DTO com `toDomain()`;
- em sucesso vai para `EM_ANDAMENTO`;
- em exceção vai para `ERRO` e mostra `e.message`.

O cálculo das eliminações está correto e deve ser preservado.

O fluxo de acusação também está implementado com diálogo de confirmação na `MainActivity`.

### UI atual

A `MainActivity` usa View Binding, RecyclerView em grid de 3 colunas e views XML/dinâmicas.

Existem estados para:

- briefing;
- carregando;
- erro;
- jogo em andamento;
- resultado.

Pistas e perguntas são adicionadas dinamicamente. O adapter de suspeitos recebe o conjunto de eliminados para exibir visualmente quem saiu.

---

## 6. Diagnóstico atual do erro no mobile

Mensagem observada:

```text
Não deu pra falar com o servidor: failed to connect to shield-noir-api.vercel.app/64.29.17.66.233
```

Investigação já feita:

1. O código Android usa o domínio correto da Vercel.
2. Não há `localhost`, `127.0.0.1` ou `10.0.2.2` no Retrofit de produção.
3. O manifest tem permissão de internet.
4. O usuário abriu no emulador:

   ```text
   https://shield-noir-api.vercel.app/health
   ```

   e recebeu sucesso.

5. O DNS público resolveu o domínio para IPs da Vercel.
6. O `/health` respondeu `200` fora do emulador.
7. Uma chamada pública ao `POST /case/generate` ficou sem resposta por 180 segundos.

Conclusão atual:

O emulador consegue acessar a internet e o domínio. O ponto problemático é a duração/indisponibilidade do `POST /case/generate`, não a URL base do Android.

Não trocar o domínio pelo IP. Não adicionar `10.0.2.2` para produção.

Para diagnosticar definitivamente, capturar no Android Studio Logcat o `Caused by` completo. Diferenciar:

- `UnknownHostException`: DNS;
- `ConnectException`: conexão TCP;
- `SSLHandshakeException`: TLS/certificado;
- `SocketTimeoutException`: backend demorando;
- `HttpException`/código HTTP: backend respondeu erro;
- `JsonSyntaxException`/Gson: schema incompatível.

O teste `/health` sozinho não testa o fluxo real do jogo. O teste importante é o POST.

---

## 7. Procedimento recomendado para continuar

### Antes de editar

1. Ler este arquivo.
2. Verificar `git diff` e preservar alterações do usuário.
3. Não modificar `MARVEL_CHARACTERS`, `name_pt`, schema público ou Android sem escopo explícito.
4. Nunca expor `.env`.
5. Confirmar se o trabalho é backend, mobile ou ambos.

### Correção recomendada da latência

O próximo trabalho mais importante é no backend:

- configurar timeouts explícitos para clientes Gemini/Groq/OpenRouter;
- envolver chamadas individuais com `asyncio.wait_for` ou equivalente;
- aplicar deadline global no pipeline de geração;
- não repetir 429/quota;
- manter poucas tentativas para 503/timeouts;
- ao atingir deadline, chamar o fallback estático completo;
- registrar provider, duração e motivo de desistência;
- manter o fallback com 10 suspeitos, 10 perguntas, clues, álibis e `image_url`.

O fallback não deve ser substituído por uma resposta vazia ou caso incompleto.

Depois disso, o Android pode receber um timeout compatível com o orçamento global do backend, por exemplo 90–120 segundos, mas somente se o backend tiver deadline menor e previsível.

### Testes backend

Testar:

```text
GET /health
POST /case/generate
```

Para múltiplos casos, verificar:

- 10 suspeitos;
- culprit entre os 10;
- culpado pode ser qualquer perfil, não apenas vilão;
- pelo menos 10 perguntas;
- no máximo 7 por rodada interna;
- divisões permitidas;
- 10 respostas por pergunta;
- clues presentes;
- `crime_moment` individual e plausível;
- `image_url` preservado;
- JSON compatível.

### Testes mobile

1. Build de debug.
2. Abrir `/health` no navegador do emulador.
3. Abrir um caso no app.
4. Capturar Logcat completo se falhar.
5. Conferir que a chamada é `POST /case/generate`.
6. Validar Gson com o JSON real do endpoint.
7. Testar perguntas, eliminações, acusação e resultado.

---

## 8. Decisões que não devem ser desfeitas

- Não retornar ao elenco de 5 suspeitos.
- Não retornar o mínimo de 7 perguntas: o mínimo do caso é 10.
- Não exigir 10 perguntas em uma única chamada: cada chamada pode gerar no máximo 7; o fluxo incremental completa o mínimo.
- Não deixar a IA escolher livremente o culpado.
- Não filtrar culpados por herói/vilão.
- Não usar `clues` para lógica de eliminação.
- Não fazer a resposta do culpado depender da interpretação do texto da pergunta.
- Não aceitar perguntas 1/9 ou 2/8 apenas para preencher slots.
- Não reintroduzir exemplos JSON gigantes nos prompts.
- Não enviar o objeto completo da Comic Vine para a IA.
- Não pedir à IA para gerar `image_url`.
- Não adicionar segundo agente, embeddings, NLP complexo ou novas chamadas de validação sem necessidade.
- Não trocar Gemini/Groq/OpenRouter nem a ordem do fallback.
- Não fazer deploy automaticamente sem autorização explícita.
- Não substituir a URL pública por IP.

---

## 9. Resumo executivo para a próxima IA

O produto está funcional conceitualmente. O backend já possui contrato correto, 10 suspeitos, culpado sorteado de forma imparcial, perguntas incrementais, validação, clues, álibis dinâmicos, imagens Comic Vine e fallback estático.

O Android já aponta para a URL pública correta e consegue acessar `/health`. O problema atual é que `/case/generate` pode ficar executando por tempo indefinido quando providers estão sem quota ou quando o OpenRouter gratuito demora/troca de modelo. A prioridade é impor deadlines no backend sem empobrecer o fallback. Só depois ajustar o timeout do Android para acompanhar um orçamento previsível.

Ao continuar, preserve o que já funciona e faça uma alteração localizada, mensurável e testada.
