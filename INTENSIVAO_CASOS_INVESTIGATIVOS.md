# Shield Noir — Intensivão de Casos Investigativos

## Objetivo

Fazer o `POST /case/generate` produzir casos que sejam investigações jogáveis,
e não apenas JSON válido ou quiz de poderes.

O jogador precisa conseguir cruzar:

1. janela e local do crime;
2. oportunidade e acesso;
3. método e vestígio físico/técnico;
4. álibis e registros independentes;
5. contradições ou hipóteses concorrentes;
6. perguntas que ajudem a separar os suspeitos naquele caso.

O culpado continua sendo escolhido pelo Python, de forma uniforme entre os dez
personagens. A IA não pode trocar o `culprit_id`.

## Diagnóstico confirmado

O pipeline anterior tinha quatro problemas:

- `questions` eram calculadas apenas a partir de poderes/equipes da Comic Vine,
  então podiam ser factualmente plausíveis e ainda assim inúteis para o crime;
- `clues.related_suspects` era validado apenas estruturalmente, permitindo pistas
  genéricas sobre habilidades sem relação com o desaparecimento;
- o texto livre de `deck` da Comic Vine podia conter uma variante ou entidade
  incompatível com o personagem controlado;
- a ausência de um orçamento de tempo deixava providers gratuitos demorando demais
  antes do fallback.

## Decisão de arquitetura

### A IA gera o contexto; o Python garante a lógica

A chamada principal recebe os dez perfis compactos e devolve:

- descrição do crime;
- cinco clues curtas, com categorias variadas;
- dez depoimentos/álibis;
- até dezoito perguntas candidatas;
- para cada pergunta, somente `text` e `basis` factual (`clue:N` ou `profile:N`).

O provider não produz booleanos. O Python resolve a base referenciada, completa os
`false`, confere IDs, elimina divisões ruins, elimina duplicatas e escolhe as dez
finais.

O contrato público continua usando `answers` com dez objetos por pergunta.

### Perguntas de caso, não trivia

Uma pergunta só é aceita quando:

- tem 3–7 respostas verdadeiras;
- não contém identidade, nome ou alter ego;
- não é composta por alternativas;
- não é mera pergunta de perfil como “possui superforça?”;
- menciona ou se conecta a oportunidade, acesso, tempo, álibi, registro,
  método, vestígio, localização ou capacidade relacionada ao método;
- é diferente das demais e cria separação útil entre suspeitos.

Poderes e equipes continuam permitidos quando o crime realmente depende deles,
mas deixam de ser a fonte principal das perguntas.

### Clues investigativas

O caso deve pedir clues que misturem categorias, sem revelar o culpado:

- temporalidade/janela;
- acesso/credencial/rota;
- câmera, sensor, log ou testemunha;
- vestígio físico/material;
- álibi ou contradição;
- capacidade compatível com o método, quando necessário.

Pistas que apenas dizem que uma ficha confirma habilidades compartilhadas são
descartadas. Deve existir pelo menos uma clue relacionada ao culpado e a outros
suspeitos plausíveis.

### Personagens

O nome e `name_pt` do catálogo controlado continuam sendo a autoridade pública.
O `deck` livre não é usado para montar automaticamente a descrição final do
suspeito nem é tratado como verdade absoluta. A descrição final prioriza os
campos estruturados de origem, equipes e poderes disponíveis.

## Implementação

1. Criar modelos internos para perguntas candidatas.
2. Fazer o provider retornar apenas `text` e `basis` (`clue:N` ou `profile:N`),
   nunca a matriz de respostas.
3. Derivar todas as respostas no Python a partir da clue referenciada ou do fato
   estruturado correspondente; o provider não pode inventar `true/false`.
4. Verificar perguntas de localização contra os `crime_moment` e rejeitar
   perguntas especulativas de capacidade ou método.
5. Completar uma resposta curta do provider com perguntas derivadas das clues e
   dos álibis já presentes, sem nova chamada de IA.
6. Remover da description conclusões que entreguem o mecanismo antes das clues.
7. Filtrar e ranquear perguntas específicas do caso.
8. Validar clues por conteúdo mínimo e diversidade simples, sem NLP pesado.
9. Manter o fallback jogável com dez perguntas investigativas, não trivia de poderes.
10. Aplicar timeout individual e deadline global aos providers; quota não deve
   ser repetida indefinidamente.
11. Preservar `image_url`, `culprit_id`, `MARVEL_CHARACTERS`, `name_pt`, Android,
   providers e ordem Gemini → Groq → OpenRouter → fallback.

## Critérios de aceite

Para cada caso válido:

- exatamente dez suspeitos controlados;
- `culprit_id` pertence ao elenco e não é escolhido por alinhamento moral;
- descrição inclui uma janela ou marco temporal identificável;
- cada suspeito tem depoimento concreto, não uma lista de poderes;
- pelo menos três clues não duplicadas e não genéricas;
- pelo menos uma clue relaciona o culpado a alternativas plausíveis;
- exatamente dez perguntas finais com dez respostas booleanas;
- divisões apenas 3/7, 4/6, 5/5, 6/4 ou 7/3;
- perguntas predominantemente relacionadas ao caso;
- sem perguntas de identidade ou nomes próprios;
- padrões de respostas distintos quando os dados permitirem;
- `image_url` é preservado por ID;
- falha/quota de um provider passa ao próximo sem travar o endpoint;
- fallback continua retornando JSON jogável.

## Testes obrigatórios

- compile/import do backend;
- teste unitário local da conversão de `basis` factual → dez respostas;
- teste de filtragem de perguntas genéricas, 1/9 e duplicadas;
- teste de clues genéricas, incompletas e relacionadas ao culpado;
- teste de fallback com todos os providers falhando;
- teste com payload de provider omitindo `questions`;
- múltiplos elencos reais ou mocks equivalentes;
- `GET /health` e `POST /case/generate` quando as credenciais estiverem disponíveis.

Não fazer deploy automaticamente nesta tarefa.

## Aplicação desta edição

Aplicado no backend:

- perguntas candidatas compactas com `basis` factual e reconstrução Python;
- respostas calculadas exclusivamente a partir da clue/fato referenciado;
- rejeição de perguntas que confundem possibilidade com fato ou contradizem álibis;
- complemento local de perguntas a partir de clues e padrões factuais dos álibis;
- neutralização da description quando o provider revela o método do crime;
- índice de `PROFILE_FACTS` determinístico entre prompt e validação;
- filtro de perguntas genéricas de perfil, divisões ruins e duplicatas;
- prompt orientado por janela, acesso, álibi, vestígio e hipóteses concorrentes;
- rejeição de clues genéricas e exigência de mais de uma categoria investigativa;
- descrições de suspeitos ancoradas em campos estruturados quando o provider diverge;
- fallback com cenários, janelas e pistas investigativas variadas;
- timeout por provider e deadline global de geração.

Não aplicado: deploy, alteração do Android, alteração do catálogo ou mudança do
schema público do caso.
