# Лучшие коммерчески применимые модели для ролей торгового агента

## Ключевой вывод

Если смотреть не на абстрактные бенчмарки, а на **реальный продовый стек для trading agent**, то наилучший баланс сейчас выглядит так: **Qwen** как основной открытый рабочий “мозг” для рынка и новостей, **DeepSeek** как дешёвый и сильный оппонент в debate-слое, **OpenAI GPT-5.5** как финальный арбитр, **GPT-4.1 mini** как быстрый schema-first верификатор, и **Ministral 3 3B** как локальный summarizer/reflector для памяти. Такой выбор лучше соответствует вашим критериям по лицензиям, JSON, русскому финансовому тексту, latency/cost и доступности через polza.ai или OpenAI-совместимые интерфейсы. Текущий публично доступный каталог polza.ai уже показывает у себя, среди прочего, `openai/gpt-5.5`, `openai/gpt-5.5-mini`, `qwen/qwen3.6-35b-a3b-thinking`, `qwen/qwen3.5-70b`, `deepseek/deepseek-r1-250528`, `mistralai/mistral-medium-3.5` и поддержку capability-полей вроде `structured_outputs` и `reasoning_effort`, что делает capability-based routing практичным прямо сейчас. citeturn1view0turn1view1

Для **русских финансовых новостей** у Qwen и Mistral самая сильная и наиболее явно задокументированная позиция из открытых семейств, которые я проверил: Qwen прямо перечисляет русский среди 119 поддерживаемых языков, а Mistral Small 3.1 прямо указывает русский в списке поддерживаемых языков и заявляет native function calling и JSON output. У DeepSeek сильная reasoning/agentic-позиция и отличный API, но в публичных источниках, которые я проверил, акцент сделан на reasoning, agentic use и контекст, а не на документированное преимущество именно по русскому. Поэтому для **News Analyst** я ставлю Qwen/Mistral выше DeepSeek. citeturn9view0turn14view1turn7view0turn7view2turn7view3

Для **JSON-надежности** ключевой вывод простой: если слой должен выдавать канонический execution schema, risk schema или compliance schema, лучше всего опираться на модели и API, где есть **явная поддержка strict schema**. У OpenAI это официально задокументировано как Structured Outputs с JSON Schema; у Mistral Small 3.1 и Medium 3.5 задокументированы native function calling и JSON output; у polza.ai capability-поля `structured_outputs` видны на уровне каталога. Поэтому аналитиков и дебаты можно держать на дешёвых/open-weight моделях, а финальное решение и верификацию — на schema-first моделях. citeturn27view2turn27view1turn27view0turn14view1turn14view3turn1view0turn1view1

Ниже я считаю модель “допустимой” только если нашёл либо **перемиссивную/open-weight лицензию**, либо **API/business terms**, явно разрешающие коммерческое встраивание в продукт. Так как конкретный регламент вашего хакатона в сообщении не приложен, все спорные случаи я помечаю как **“нужна ручная проверка”**, а не считаю автоматически разрешёнными.

## Рекомендации по ролям

| Роль | Основной выбор | Резервный выбор | Локальный/открытый запасной вариант |
|---|---|---|---|
| Market Analyst | **Qwen3.6-35B-A3B-Thinking** | **GPT-5.5** | **Qwen3 30B/32B self-host** |
| News Analyst | **Qwen3.6-35B-A3B-Thinking** | **Mistral Small 3.1** | **Gemma 4 31B / Gemma 3 27B** |
| Bull/Bear Debate | **Пара: DeepSeek-R1-250528 + Qwen3.6-35B-A3B-Thinking** | **GPT-5.5 + DeepSeek-R1** | **DeepSeek-R1-Distill-Qwen + Qwen self-host** |
| Trader / final decision maker | **GPT-5.5** | **GPT-5.5 pro** | **Mistral Medium 3.5** |
| Risk Officer / verifier | **GPT-4.1 mini** | **Mistral Small 3.1** | **Gemma 4 / FunctionGemma при строгом schema-gating** |
| Reflection / memory summarizer | **Ministral 3 3B Instruct** | **Llama 3.2 3B Instruct** | **Gemma 3 4B** |

**Почему именно так.** Для **Market Analyst** и **News Analyst** я рекомендую Qwen как основной default, потому что у него одновременно есть permissive Apache 2.0 лицензия, явная поддержка 119 языков с русским, thinking/non-thinking режимы, OpenAI-compatible self-host через SGLang/vLLM и очень сильная цена в текущем каталоге polza.ai. Это делает Qwen лучшим сочетанием “качество на рубль/доллар + русский + открытость”. citeturn9view0turn9view1turn11search4turn1view0

Для **Bull/Bear Debate** я сознательно рекомендую **не одну модель два раза**, а **разнородную пару**. У DeepSeek-R1 есть MIT-лицензия/коммерческая разрешённость в официальном README, OpenAI-compatible API, JSON/tool support в официальной документации и сильная reasoning-ориентация; у Qwen — лучшая документированная multilingual/Russian база. В debate-слое важнее не только IQ, но и **разные error modes**, поэтому гетерогенная пара обычно полезнее, чем “один и тот же Qwen с двумя системными промптами”. Это уже инженерный вывод из лицензий, возможностей API и языковой специализации. citeturn7view4turn7view3turn7view0turn1view0turn9view0turn9view1

Для **Trader / final decision maker** лучший выбор сейчас — **GPT-5.5**, а не o3. Причина проста: OpenAI уже позиционирует o3 как модель, которую сменил GPT-5, тогда как GPT-5.5 — текущая frontier-модель для сложной профессиональной работы; она поддерживает reasoning effort, 1.05M context, structured outputs, function calling и набор встроенных tool-capabilities. Если вы хотите максимальный потолок качества и не привязаны строго к polza, тогда **GPT-5.5 pro** — самый сильный, но и самый медленный/дорогой вариант, который я смог верифицировать. citeturn27view0turn29view0turn29view1

Для **Risk Officer / verifier** важнее не максимальный raw reasoning, а **быстрый, дешёвый и очень дисциплинированный schema-first контрольный контур**. Именно поэтому здесь лучше всего выглядит **GPT-4.1 mini**: OpenAI документирует fast/low-latency поведение, 1M context, сильное instruction following и tool calling, плюс formal Structured Outputs. Если вы хотите уйти в открытый стек, **Mistral Small 3.1** — лучший задокументированный open-weight кандидат: русский есть явно, JSON/function calling заявлены нативно, лицензия Apache 2.0. citeturn27view1turn27view2turn14view1

Для **Reflection / memory summarizer** лучший локальный выбор — **Ministral 3 3B Instruct**: Mistral прямо пишет, что 3B-вариант рассчитан на edge deployment, имеет 256K context, Apache 2.0 и может уместиться менее чем в 8 GB RAM/VRAM в quantized-виде. Это намного убедительнее для вашего ограничения **4 vCPU / 16 GB RAM**, чем любые 7B+ reasoning-модели. Второй лучший локальный вариант — **Llama 3.2 3B**, потому что Meta прямо позиционирует 1B/3B как edge/mobile модели с 128K context, хотя по лицензии Llama заметно менее “чистая”, чем Apache/MIT. citeturn17view0turn17view1turn19view2turn19view0

## Сравнение семейств моделей

**Qwen.** Это мой **лучший default-class open-weight выбор** для вашего проекта. Причины: все open-weight модели Qwen3/Qwen3.6 задокументированы как Apache 2.0; Qwen3 поддерживает 119 языков и диалектов, включая русский; семейство поддерживает hybrid thinking; self-hosting через SGLang и vLLM даёт OpenAI-compatible API. В practical agent-stack это означает: хороший русский, хорошая reasoning-эластичность, нормальный tool use и предсказуемая коммерческая разрешённость. Именно поэтому Qwen у меня побеждает как **Market Analyst** и **News Analyst**. citeturn9view0turn9view1turn11search4

**DeepSeek.** Это мой **лучший дешёвый “второй мозг” для reasoning и debate**, но не первый выбор для сырого русского news parsing. По официальным источникам DeepSeek-R1 коммерчески разрешён под MIT, DeepSeek API OpenAI-compatible, у DeepSeek есть JSON Output и Tool Calls, а актуальная V4-линейка идёт с 1M context и агентными оптимизациями. Для bull/bear-слоя это очень сильный профиль. Но в публичных материалах, которые я проверил, DeepSeek сильнее продаётся как reasoning/agentic/long-context семейство, чем как явно мультиязычный русский news-model; поэтому я не делаю его первым news-parser’ом. citeturn7view4turn7view3turn7view0turn7view2turn7view5

**Mistral.** Здесь важно разделять **Small 3.1** и **Medium 3.5**. **Mistral Small 3.1** — один из самых удобных open-weight verifier’ов: Apache 2.0, русский явно в списке языков, native function calling и JSON output, 128K context, и хороший multilingual результат в официальной карте модели. **Mistral Medium 3.5** — очень сильный final/open fallback для сложных агентных задач, 256K context и reasoning effort, но лицензия у него **Modified MIT**, то есть это уже не такой “чистый” коммерческий профиль, как Apache 2.0. Поэтому Small 3.1 я люблю как verifier, а Medium 3.5 — как сильный open-weight final decider, если по лицензии он вам подходит. citeturn14view1turn14view0turn14view3turn15search1

**Llama.** Сильные стороны Llama для вашего проекта — это локальная экосистема и edge-варианты: Meta прямо пишет, что Llama 3.2 1B и 3B предназначены для on-device/edge use cases и имеют 128K context. Но по лицензии Llama — это **custom community license**, а не Apache/MIT; там есть требования к атрибуции, “Built with Llama”, и отдельные коммерческие условия для сверхкрупных продуктов. Дополнительно Meta отдельно оговаривает EU-ограничение для мультимодальных моделей Llama 3.2. Для вашего use case это не блокирует текстовые маленькие модели как summarizer, но делает Llama менее “чистой” лицензией, чем Qwen или Mistral Small 3.1. Плюс в карточке Llama 3.3 русский не заявлен как один из официально поддерживаемых языков. citeturn19view0turn19view2turn19view3

**Gemma.** У Gemma сейчас двойственная, но интересная позиция. **Gemma 3** имеет open weights, 128K context, 140+ языков, structured outputs и function calling, но коммерческая модель распространяется через **Gemma Terms of Use**, а не через Apache 2.0. Зато **Gemma 4** уже объявлена под **Apache 2.0**, с agentic/workflow-профилем, native function-calling, structured JSON и до 256K контекста на крупных вариантах. Поэтому семейство Gemma стало гораздо привлекательнее по лицензии, но в вашем конкретном стеке я всё равно ставлю его ниже Qwen/Mistral, потому что Qwen/Mistral сейчас лучше документированы именно для русского и лучше вписываются в уже видимый polza.ai-каталог и OpenAI-compatible маршрутизацию. citeturn21view0turn21view1turn21view2turn21view3turn30view2

**FinGPT, Fin-R1 и finance-specific семейства.** Здесь важно быть строгим. **FinGPT** интересен как экосистема и research toolkit, но коммерчески он не настолько “чист”, как кажется по README: репозиторий помечен MIT, однако там же есть формулировка об “academic purposes under the MIT education license”, а сами базовые модели в проекте преимущественно привязаны к английским и китайским финансовым рынкам. Для production shortlist я бы считал FinGPT **лицензионно неоднозначным** без ручной проверки конкретного чекпойнта. **Fin-R1** выглядит лучше: он построен на Qwen2.5-7B-Instruct, позиционируется как open-sourced, показывает сильные результаты на финансовых reasoning-бенчмарках, а в модели на Hugging Face есть Apache 2.0 badge. Но его открыто задокументированная датасетная база — это прежде всего **китайский и английский** финансовый домен, а пример serving-конфига идёт с `max-model-len 16384`; для русского финансового news-flow и продового verifier-слоя у меня нет такого же уровня уверенности, как для Qwen/Mistral/OpenAI. Поэтому finance-specific модели я рассматриваю как **узкие эксперименты**, а не как core production picks. Интересный отдельный пример — **Qwen DianJin / DianJin-R1**, который Alibaba открыто продвигает как financial-industry семейство с задачами вроде summarization research reports и news IE, но в доступных мне открытых страницах я не смог так же надёжно верифицировать точный license text, как смог для Qwen, Mistral, DeepSeek и OpenAI, поэтому в основной shortlist его не включаю. citeturn32view0turn34view0turn34view1turn35search8turn36view0

**OpenAI general/reasoning семейство.** Для production-critical слоёв оно сейчас самое сильное. OpenAI официально разрешает бизнес-клиентам встраивать API в свои customer applications, закрепляет за клиентом права на output, а модели GPT-5.5 и GPT-4.1 mini имеют formal structured outputs и tool support. Поэтому если вам нужен **один слой, которому можно доверить финальную агрегацию сигналов и строгую схему ответа**, OpenAI здесь всё ещё лучший выбор. Это не open-weight и не “бесплатная” коммерция, но это **самая чистая комбинация capability + schema reliability + business terms**, которую я проверил. citeturn29view2turn29view0turn27view1turn27view2

## Самый дешёвый жизнеспособный стек

| Роль | Модель |
|---|---|
| Market Analyst | **Qwen3.6-35B-A3B-Thinking** |
| News Analyst | **Qwen3.6-35B-A3B-Thinking** |
| Bull | **DeepSeek-R1-250528** |
| Bear | **Qwen3.6-35B-A3B-Thinking** |
| Trader | **GPT-4.1 mini** |
| Risk Officer | **GPT-4.1 mini** |
| Reflection / memory | **Ministral 3 3B Instruct локально** |

Этот стек я считаю **самым дешёвым из тех, что всё ещё выглядят инженерно разумно**, а не просто “самым минимальным по прайсу”. Причина: полная монокультура на одной дешёвой модели выйдет ещё дешевле, но резко повысит correlation risk. В текущем polza.ai-каталоге у `qwen/qwen3.6-35b-a3b-thinking` видна очень низкая цена относительно остальных сильных кандидатов; `openai/gpt-5.5` и `mistralai/mistral-medium-3.5` заметно дороже; у OpenAI `gpt-4.1-mini` остаётся дешёвым и быстрым schema-first выбором для финального режима проверки. Локальный `Ministral 3 3B` позволяет вообще убрать memory/reflection из токен-расхода API. citeturn1view0turn27view1turn17view0turn17view1

Если хочется **ещё сильнее ужать бюджет**, можно перевести и `Trader`, и `Risk Officer` на один и тот же `Qwen3.6-35B-A3B-Thinking`, а `Reflection` оставить локальным. Но я бы не делал так для реальной торговли: вы слишком многое выигрываете от formal Structured Outputs и более дисциплинированного verifier-слоя у OpenAI. citeturn27view1turn27view2turn9view0turn9view1

## Стек максимального качества

| Роль | Модель |
|---|---|
| Market Analyst | **Qwen3.6-35B-A3B-Thinking** |
| News Analyst | **Qwen3.6-35B-A3B-Thinking** |
| Bull | **DeepSeek-R1-250528** |
| Bear | **GPT-5.5** |
| Trader | **GPT-5.5** |
| Risk Officer | **GPT-4.1 mini** |
| Reflection / memory | **Ministral 3 3B локально** |

Это мой **лучший практический стек качества** при сохранении адекватной архитектуры и расходов. Я **не** ставлю GPT-5.5 на все роли, потому что он дорогой и не даёт настолько большого added value на первом слое обработки русских новостей, как даёт на финальном арбитраже. В news/market-слое сильнее работает Qwen: у него лучше задокументирован русский, хорошая reasoning-гибкость и существенно более мягкая цена. В debate-слое DeepSeek даёт полезную альтернативную линию рассуждения; в финальном арбитраже GPT-5.5 выигрывает за счёт frontier reasoning и tool-rich API; в risk-слое GPT-4.1 mini даёт более экономичный, быстрый и schema-first контур. citeturn9view0turn9view1turn29view0turn27view1turn7view4turn7view3turn1view0

Если вы **не ограничены polza.ai** и готовы ходить напрямую в OpenAI API, то “absolute max quality” версия этого стека — заменить `Trader` на **GPT-5.5 pro**. Но OpenAI прямо предупреждает, что это самый медленный и самый дорогой режим, некоторые запросы могут идти минуты, и для agentic decision loop это уже может быть чрезмерно. Поэтому для большинства trading-agent сценариев я бы остановился на обычном **GPT-5.5**, а не на pro. citeturn29view1

## Лицензии и коммерческие оговорки

| Семейство / модель | Коммерческий статус | Практический вывод |
|---|---|---|
| **Qwen3 / Qwen3.6** | Open-weight, **Apache 2.0**. citeturn9view1turn11search4 | Один из самых “чистых” вариантов для коммерции и self-host. Мой основной open default. |
| **DeepSeek-R1** | **MIT**, README прямо говорит о commercial use; Distill-Qwen наследует Apache 2.0, Distill-Llama — Llama license. citeturn7view4 | Хорошо для коммерческого reasoning-слоя; внимательно смотреть на лицензию именно distill-варианта. |
| **DeepSeek-V3** | Код MIT; use of Base/Chat models идёт под отдельной model license, но README прямо пишет, что серия supports commercial use. citeturn7view5 | Коммерчески пригодно, но по чистоте лицензии чуть менее прозрачно, чем чистый Apache 2.0 у Qwen. |
| **Mistral Small 3.1** | **Apache 2.0**. citeturn14view1 | Отличный open verifier / secondary news model. |
| **Mistral Medium 3.5** | **Modified MIT**, то есть custom license, а не plain MIT. citeturn14view0turn15search1turn15search3 | Коммерчески возможен, но я бы пропускал через legal review, особенно для redistribution/self-hosted product. |
| **Llama 3.3** | **Llama Community License**; коммерция разрешена, но есть attribution/redistribution условия и отдельный MAU-threshold для очень крупных продуктов. citeturn19view0turn19view3 | Для стартапа/хакатона обычно ок, но это не Apache/MIT. |
| **Llama 3.2 multimodal** | Для мультимодальных моделей есть отдельное EU-ограничение на granted rights; на text-only small models это не распространяется. citeturn19view1turn19view2 | Для ваших текстовых summarizer/reflector use cases опасность меньше, но мультимодальные варианты в ЕС я бы не брал по умолчанию. |
| **Gemma 3** | Open weights и ответственное коммерческое использование по **Gemma Terms of Use**, с use restrictions. citeturn21view1turn21view0 | Допустимо, но это не Apache 2.0. |
| **Gemma 4** | Заявлена под **Apache 2.0**. citeturn21view3 | Самый привлекательный Gemma-вариант для новой коммерческой разработки. |
| **OpenAI API** | Business terms разрешают встроить API в customer applications; клиент сохраняет input и владеет output. citeturn24search6turn29view2 | Юридически коммерчески пригодно, но это API-service, а не open weights. |
| **FinGPT** | Репозиторий помечен MIT, но README одновременно пишет про “academic purposes under the MIT education license”; плюс проект завязан на разнородные base models. citeturn32view0 | Для продового short list считаю **лицензионно неоднозначным** без ручной проверки конкретного checkpoint’а. |
| **Fin-R1** | Модель построена на Qwen2.5-7B-Instruct; доступные источники показывают Apache 2.0 badge, но я всё равно рекомендую проверять exact model card/checkpoint перед релизом. citeturn34view0turn35search8 | Потенциально пригодно, но не мой default из‑за языкового профиля и меньшей зрелости. |
| **Qwen DianJin / DianJin-R1** | Открытый релиз и industrial/financial positioning видны, но exact license text я не смог так же надёжно подтвердить в просмотренных источниках. citeturn36view0 | Интересно, но без ручной проверки лицензии я бы не ставил в основной коммерческий стек. |

## План на случай изменения каталога polza.ai

Во-первых, я бы сделал **capability router**, а не router по “любимым model names”. У polza.ai официально есть endpoint для получения списка моделей, а в документации описаны поля вроде `supported_parameters`, `supported_generation_methods`, `reasoning_effort`, `structured_outputs` и других capability-флагов. Это значит, что при каждом деплое можно автоматически собирать allowlist только из тех моделей, которые удовлетворяют вашим требованиям к схеме, reasoning, контексту и цене. citeturn1view1turn1view0

Во-вторых, я бы зафиксировал **эквивалентные классы замены**:

- **Финальный арбитр:** `openai/gpt-5.5` → direct OpenAI `gpt-5.5` → `mistralai/mistral-medium-3.5` → `qwen/qwen3.6-35b-a3b-thinking`. citeturn1view0turn29view0turn14view0
- **Новости и рынок:** `qwen/qwen3.6-35b-a3b-thinking` → `qwen/qwen3.5-70b` → `mistral-small-3.1` self-host/OpenAI-compatible endpoint → `gemma-4` custom endpoint. citeturn1view0turn14view1turn21view3
- **Дебаты:** `deepseek/deepseek-r1-250528` → direct DeepSeek `deepseek-v4-flash`/`deepseek-v4-pro` → `Qwen`-reasoning variant. citeturn1view0turn7view0turn7view2turn7view3
- **Verifier:** `gpt-4.1-mini` → `mistral-small-3.1` → любой gateway-model с подтверждённым `structured_outputs` и отдельным schema regression test. citeturn27view1turn27view2turn14view1turn1view0
- **Локальная память:** `Ministral 3 3B` → `Llama 3.2 3B` → `Gemma 3 4B`. citeturn17view0turn17view1turn19view2turn21view0

В-третьих, я бы держал маленький **регрессионный набор именно под ваш trading agent**, а не только смотреть на model cards. Минимум четыре теста:
**schema test** для canonical JSON ордера и risk report; **Russian news test** на 20–50 реальных русскоязычных финансовых заголовках; **debate divergence test** на спорных market setups; **latency budget test** для ваших целевых SLA. Если модель из polza исчезает, router подставляет следующий кандидат только после прохождения этого набора. Это особенно важно потому, что разные семейства сильно отличаются не только по reasoning, но и по тому, где они дисциплинированно держат схему. Под эти требования формальные docs лучше всего выглядят у OpenAI и Mistral, а gateway-capability checks — у polza. citeturn27view2turn14view1turn1view1turn1view0

В сухом остатке: если нужен **один короткий ответ**, то мой совет такой — **берите Qwen как основной открытый аналитический слой, DeepSeek как второй спорящий reasoning-layer, GPT-5.5 как финального трейдера, GPT-4.1 mini как риск-верификатор, и Ministral 3 3B как локальный memory/reflection слой**. Это лучший текущий компромисс между коммерческой пригодностью, JSON-надежностью, русским финансовым контентом, ценой, latency и устойчивостью к изменению каталога. citeturn1view0turn9view0turn9view1turn7view4turn29view0turn27view1turn17view1