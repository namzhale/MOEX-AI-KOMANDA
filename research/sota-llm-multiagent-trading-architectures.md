# SOTA-архитектуры LLM- и multi-agent trading systems для short-horizon MOEX бота

## Итоговый тезис

Если ранжировать не “по хайпу”, а по **полезности для короткого горизонта на акциях MOEX**, то лучший архитектурный шаблон на 2025–2026 годы — это не “большая болтливая команда агентов”, а **узкий и типизированный graph**: структурированный market snapshot, несколько специализированных аналитических узлов, **жёсткий risk gate**, отдельный portfolio manager, отдельный execution/validation layer и **очень дешёвая reflection/memory-петля**. Именно к этому одновременно подталкивают: price-driven архитектура QuantAgent для HFT/short-horizon, manager-analyst и risk design из FinCon, structured communication из TradingAgents, минималистичный trading workflow из StockBench и вывод AI-Trader, что кросс-рыночную устойчивость определяет прежде всего риск-контроль, а не “общий IQ” модели. citeturn33view0turn24view0turn26view2turn18view3turn29view1turn34view1

Самый важный вывод из публичных бенчмарков: **сильная финансовая QA/NLP-модель ещё не равна сильному trading agent**. StockBench прямо показывает разрыв между качеством на статических финансовых задачах и реальной последовательной торговлей; InvestorBench показывает, что проприетарные backbone-модели обычно сильнее в sequential trading, а domain-finetuned финансовые модели сами по себе не дают решающего преимущества; оценка FinGPT в 2025 году отдельно указывает, что FinGPT хорош на классификации, но ощутимо слабее на сложном reasoning, summarization и числовой аккуратности. Для hackathon-бота отсюда следует практический вывод: **LLM должен быть узким reasoning/orchestration слоем поверх детерминированных рыночных признаков, а не “мозгом всего”**. citeturn29view4turn28view1turn8academia3

Для short-horizon MOEX это особенно важно: вам нужен не sell-side equity research stack, а **быстрый decision stack по цене, объёму, режиму, событийному фону и исполнимости**. Это уже подтверждается и позитивными абляциями FinAgent по reflection/retrieval, и отрицательными примерами слишком тяжёлых или неподходящих модулей: у FinAgent stock-specific auxiliary tools ухудшали ETH, а StockBench специально проектирует benchmark с **minimal workflow**, потому что сложные агентные схемы могут добавлять стоимость и индуктивные bias’ы быстрее, чем реальный alpha. citeturn20view0turn29view1turn30view0

## Ранжирование архитектур

Ниже — **ранг именно по полезности как архитектурного донора для short-horizon MOEX equity bot**, а не по “общей академической значимости”.

| Место | Проект | Что в нём действительно ценно | Почему это полезно для MOEX short-horizon | Вердикт |
|---|---|---|---|---|
| 1 | **QuantAgent** | Четыре price-driven агента: Indicator, Pattern, Trend, Risk; работает на OHLC/техсигналах; построен на LangGraph; фокус на 1h/4h горизонтах и structured reasoning. citeturn33view0 | Это самый близкий шаблон к короткому горизонту: меньше текста, меньше latency, больше детерминируемых признаков и понятная risk-first композиция. citeturn33view0 | **Копировать первым** |
| 2 | **TradingAgents** | Зрелая роль-ориентированная graph-архитектура: analyst team → bull/bear researchers → trader → risk team → portfolio manager; structured documents вместо чистого чата; публичный LangGraph-репозиторий и память/рефлексия в repo. citeturn10view0turn14view0turn14view3 | Отличный шаблон orchestration и typed state; debate и PM approval можно оставить в урезанном виде. Полный “торговый офис” копировать нельзя, но каркас очень хорош. citeturn18view3turn14view0 | **Копировать выборочно** |
| 3 | **FinCon** | Manager-analyst hierarchy + selective communication + dual risk control: внутри эпизода CVaR, между эпизодами belief updates через conceptual verbal reinforcement. Абляции показывают мощный вклад risk control. citeturn24view0turn26view2turn26view4 | Самая сильная доказательная база именно по тому, что **risk overlay и иерархия коммуникаций реально меняют результат**. Для trading graph это очень ценно. citeturn26view2turn26view4 | **Копировать risk/иерархию, не копировать обучение belief’ов онлайн** |
| 4 | **FinAgent** | Market intelligence + diversified retrieval + low-level reflection + high-level reflection + tool augmentation. По таблицам опережает FinMem/FinGPT/RL-бэйзлайны; абляции показывают вклад reflection и retrieval. citeturn11view0turn20view2turn20view0turn19view4 | Очень полезны идеи “separate retrieval query field” и “двухуровневая reflection”. Но полный multimodal/generalist stack тяжёлый для hackathon-бота. citeturn19view4turn20view0 | **Копировать retrieval/reflection, не весь стек** |
| 5 | **StockBench** | Один из лучших источников архитектурной трезвости: contamination-free benchmark, minimal workflow, execution/validation stage, режимный анализ upturn/downturn, абляция news vs fundamentals. citeturn25view0turn29view1turn30view0 | Не даёт готовую архитектуру, но даёт очень важное правило: **чем сложнее workflow, тем выше риск переобучения и ложного ощущения “ума”**. Для MOEX это must-read. citeturn29view1turn30view0 | **Копировать как benchmark philosophy** |
| 6 | **FinMem** | Layered memory, working memory, self-adaptive risk character; статистически значимые улучшения против бэйзлайнов; self-adaptive risk profile лучший среди профилей. citeturn21view2turn22view1turn21view1 | Идея memory-by-timescale очень сильна, но исходные слои FinMem заточены под 10-K/10-Q/news. Для short-horizon надо радикально переопределять memory tiers. citeturn22view2turn21view2 | **Копировать идею, не буквальную реализацию** |
| 7 | **FinRL-X** | Не LLM-архитектура, а инженерный backbone: weight-centric interface, единый контракт stock selection → allocation → timing → risk overlay → execution, deployment consistency между backtest и live. citeturn35view0turn38view2 | Для production-like MOEX бота это почти так же важно, как и reasoning: graph должен выпускать **target position / target weight**, а не текстовый “buy/sell/hold”. citeturn35view0turn38view2 | **Копировать интерфейс и execution semantics** |
| 8 | **AI-Trader** | Live, automated, data-uncontaminated benchmark; minimal information paradigm; вывод, что risk control определяет cross-market robustness. citeturn34view1 | Полезно как проверка на реальность: высокий “общий интеллект” не гарантирует торговый результат, а жидкие рынки прощают больше, чем policy-driven. citeturn34view1 | **Копировать benchmark mindset, не live-autonomy целиком** |
| 9 | **InvestorBench** | Широкий benchmark по stocks/crypto/ETF; агент с Brain/Perception/Profile/Memory/Action; layered memory; показал преимущество проприетарных моделей в sequential trading. citeturn27view4turn27view3turn28view1 | Полезен для evaluation harness и sanity-check’ов, но как donor для короткого горизонта даёт меньше, чем QuantAgent/TradingAgents/FinCon. citeturn27view3turn28view1 | **Копировать evaluation, не всю когнитивную схему** |
| 10 | **FinGPT** | Сильный open-source финансовый LLM и экосистема; дешевое fine-tuning; хорош для sentiment/classification; в 2025-м отдельно показаны ограничения на reasoning/generation. citeturn8academia0turn38view3turn8academia3 | Полезен как **специализированный текстовый submodel**, особенно для news/sentiment scoring. Как центральный decision-maker для торгов — слабоват. citeturn8academia3turn8academia2 | **Использовать как tool, не как PM** |
| 11 | **FinRobot** | Полноценный equity research / valuation stack: Data-CoT, Concept-CoT, Thesis-CoT; в repo есть финансовый анализ, прогнозы, DCF, peer comparison, 8 агентов, 15+ chart types. citeturn12view1turn13view0 | Для short-horizon MOEX это слишком медленно и слишком фундаментально. Полезно только как offline research assistant, не как live trading graph. citeturn12view1turn13view0 | **Почти не копировать** |
| 12 | **QuantAgents** | Multi-agent system с simulated trading analyst, risk control analyst, market news analyst и manager; двойная обратная связь от реального и simulated trading. citeturn34view0 | Идея интересная, но для hackathon short-horizon это уже слишком тяжёлый outer-loop. Больше подходит для research pipeline, чем для быстрого торгового графа. citeturn34view0 | **Оставить на потом** |

Вне таблицы я бы ещё держал в поле зрения **Trading-R1** как важный сигнал направления рынка: авторы из экосистемы TradingAgents показывают, что **reasoning, дообученный под trading principles и volatility-adjusted decision making**, может улучшать risk-adjusted returns и drawdowns. Но это скорее идея для следующего цикла, чем для hackathon-реализации сейчас. citeturn41academia0

## Что добавить в develop_2

Самый сильный practical mix для вашего LangGraph — это **QuantAgent-подобный price core**, **TradingAgents/FinCon-подобный orchestration layer** и **FinRL-X-подобный target-position/execution contract**. В исследованиях именно такая комбинация лучше всего выглядит по соотношению “полезность / стоимость / скорость / интерпретируемость”. citeturn33view0turn24view0turn14view0turn35view0

Ниже — skeleton, который я бы реально закладывал в `develop_2`:

```text
MarketDataIngest
  -> SnapshotBuilder
  -> {IndicatorAgent, PatternAgent, TrendAgent, OptionalNewsAgent}
  -> SignalAggregator
  -> ConditionalChallenge
  -> RiskAgent
  -> PortfolioManager
  -> ExecutionValidator
  -> ExecutionAgent
  -> PostTradeLogger
  -> ReflectionMemory
```

Ключевой architectural choice: агенты должны общаться **не историей длинных сообщений**, а **типизированным global state**. TradingAgents именно это и делает через structured reports; FinCon показывает, что manager-analyst hierarchy лучше полного peer-to-peer общения по стоимости и фокусу; StockBench вообще сознательно держит workflow минимальным. citeturn10view0turn26view4turn29view1

### Точные узлы, которые стоит добавить

| Компонент | Что делает | Откуда идея | Что именно добавить в graph | Приоритет |
|---|---|---|---|---|
| **SnapshotBuilder** | Собирает единый typed snapshot рынка | TradingAgents structured reports; StockBench portfolio overview; FinRL-X unified pipeline. citeturn10view0turn29view1turn35view0 | Поля `ohlcv_features`, `intraday_regime`, `spread/liquidity proxy`, `recent_news_top_n`, `current_position`, `cash`, `pending_orders`. | Очень высокий |
| **IndicatorAgent** | Считает и интерпретирует RSI/MACD/ROC/WILLR и аналогичные признаки | QuantAgent. citeturn33view0 | Возвращает JSON-объект с `bias`, `confidence`, `signal_strength`, `invalidity_level`. | Очень высокий |
| **PatternAgent** | Выделяет price-patterns | QuantAgent. citeturn33view0 | Для hackathon я бы делал его **не vision-first**, а deterministic-first: double top/bottom, breakout, compression, gap-follow/fade. LLM нужен для short narrative, не для OCR/vision. Это уже мой инженерный вывод из price-driven дизайна QuantAgent и минималистичной философии StockBench. citeturn33view0turn29view1 | Высокий |
| **TrendAgent** | Оценивает direction / channel / regime | QuantAgent. citeturn33view0 | Отдельно вернуть `regime={trend, mean-revert, noisy, event}` и `trend_confidence`. | Высокий |
| **OptionalNewsAgent** | Сжимает только действительно свежий новостной контекст | StockBench top-5 news; FinGPT как text-specialist; TradingAgents sentiment/news analyst. citeturn29view3turn8academia3turn14view0 | Триггерить **только** если есть новости/корпоративное событие/аномальный гэп. Иначе узел пропускать. | Средний |
| **SignalAggregator** | Пишет короткие structured signal cards | TradingAgents structured communication; FinAgent separate retrieval fields. citeturn10view0turn19view4 | Пусть каждый analyst отдаёт не prose, а `{view, strength, evidence, failure_mode, retrieval_key}`. | Очень высокий |
| **ConditionalChallenge** | Встроенный bull/bear challenge, но только при конфликте сигналов | TradingAgents bull/bear debate; StockBench minimal workflow. citeturn18view3turn29view1 | Делать **не всегда**. Запускать, если `signal_conflict > threshold`, либо `news_bias` против `price_bias`. Один раунд, жёсткий лимит токенов. | Высокий |
| **RiskAgent** | Превращает signal stack в risk-aware trade envelope | QuantAgent RiskAgent; FinCon CVaR/risk control; TradingAgents risk team. citeturn33view0turn26view2turn14view0 | Возвращать `max_position`, `stop`, `take_profit`, `do_not_trade`, `reason_codes`. | Очень высокий |
| **PortfolioManager** | Конвертирует сигнал в target position | TradingAgents fund/portfolio manager; FinRL-X weight-centric interface. citeturn14view0turn35view0turn38view2 | Главное: выход не текст, а `target_inventory` или `target_weight`. Для single-name short-horizon это может быть `target_shares` или `target_exposure_rub`. | Очень высокий |
| **ExecutionValidator** | Проверяет исполнимость и ограничения | StockBench execution & validation; FinRL-X pre-trade risk checks. citeturn25view0turn38view2 | Проверки наличности, лотов, лимитов, turnover, max slippage budget, cooldown после стопа. | Очень высокий |
| **ExecutionAgent** | Выбирает способ выставления заявки | FinRL-X execution split; TradingAgents approval/execution separation. citeturn35view0turn14view0 | `market_if_urgent`, `limit_join_spread`, `slice_twap`, `cancel_if_not_filled`. | Высокий |
| **PostTradeLogger** | Фиксирует outcome | TradingAgents decision log / realized return memory. citeturn14view3 | Хранить только компактный trade record и outcome, а не весь chat trace. | Высокий |
| **ReflectionMemory** | Делает очень дешёвую post-trade reflection | FinMem layered memory; FinAgent dual reflection; TradingAgents repo reflection log. citeturn21view2turn20view0turn14view3 | Один short note на сделку/день: `setup`, `what worked`, `what failed`, `regime`, `next-time rule`. | Высокий |

### Что я бы положил в state schema

Для `develop_2` я бы закрепил следующие ключи state:

- `market_snapshot`
- `signal_cards`
- `challenge_result`
- `risk_report`
- `target_position`
- `execution_plan`
- `fills`
- `trade_outcome`
- `reflection_note`
- `memory_keys`

Это не просто “хороший стиль”. TradingAgents, FinAgent и StockBench в разных формах приходят к одной и той же практической мысли: **чем более structured и проверяемы промежуточные артефакты, тем меньше telephone effect, меньше schema drift и меньше скрытых ошибок исполнения**. citeturn10view0turn19view4turn29view1

## Что не копировать

### Неподходящие элементы для hackathon short-horizon бота

| Что не копировать буквально | Почему это плохо для short-horizon MOEX | Доказательство |
|---|---|---|
| **Sell-side research stack FinRobot** | DCF, peer comparison, thesis writing, 8 агентов и 15+ графиков — это equity research, а не intraday/short-swing execution. Слишком медленно и слишком фундаментально. | FinRobot paper и repo. citeturn12view1turn13view0 |
| **Полный multimodal/generalist stack FinAgent** | В FinAgent много сильных идей, но весь стек сразу — слишком дорогой по latency, prompt length и интеграциям. К тому же сами авторы показывают, что неподходящие auxiliary tools могут ухудшать результат. | FinAgent architecture и ablations, включая ухудшение на ETH от stock-specific tools. citeturn11view0turn20view0 |
| **All-to-all multi-agent chatter** | FinCon прямо критикует лишние коммуникационные издержки и ставит manager-analyst hierarchy вместо peer-to-peer. | FinCon. citeturn26view4 |
| **Многораундовые дебаты на каждом баре** | Debate полезен как conflict resolver, но StockBench показывает ценность minimal workflow, а TradingAgents repo даже конфигурирует число раундов отдельно. Для short-horizon постоянный debate слишком дорог. | StockBench; TradingAgents repo. citeturn29view1turn14view3 |
| **Фундаментальная long-memory по 10-Q/10-K как в исходном FinMem** | Идея timescale-memory правильная, но сами memory layers FinMem завязаны на quarterly/annual filings. Для short-horizon MOEX это слабый источник alpha относительно price/regime/event memory. | FinMem memory warehouse. citeturn22view2turn21view2 |
| **Онлайновое verbal reinforcement / textual gradient descent** | FinCon показывает, что belief updates полезны, но это уже тяжёлый outer-loop. Для hackathon достаточно rule-based nightly reflection, а не обучения промптов по PnL онлайн. | FinCon. citeturn26view1turn26view2 |
| **Полностью автономный live web-search агент** | AI-Trader использует minimal information paradigm как benchmark, но в реальном short-horizon контуре это несёт latency, нестабильность и плохую воспроизводимость. | AI-Trader. citeturn34view1 |
| **Использовать FinGPT как главный trading brain** | FinGPT хорош как финансовый NLP-model/tool, но отдельная оценка в 2025-м показывает провалы на reasoning/generation/numerical accuracy. | FinGPT papers and assessment. citeturn8academia0turn8academia3turn38view3 |
| **R&D-Agent(Q)-стиль codegen/backtest loop в боевом graph** | Это сильный research automation framework, но для hackathon-trader он избыточен и медленен: factor-model co-optimization полезнее offline, чем в decision loop. | RD-Agent(Q). citeturn42academia0 |

Самый опасный соблазн здесь — скопировать внешне “умную” организацию из papers, но не заметить, что бенчмарки 2025–2026 всё чаще поощряют **простые, проверяемые, режимно-устойчивые workflows**, а не максимально сложные агентные спектакли. StockBench и AI-Trader особенно сильны именно как холодный душ против лишней агентности. citeturn29view4turn34view1

## Рекомендуемые варианты реализации

### Minimal

Это вариант, который я бы выбрал, если цель — **собрать рабочего MOEX-бота за минимум времени**.

Архитектура: `SnapshotBuilder -> IndicatorAgent -> TrendAgent -> RiskAgent -> PortfolioManager -> ExecutionValidator -> ExecutionAgent -> ReflectionMemory`.

В этом варианте LLM почти не спорит и почти не ищет новости. Он нужен для **сводного reasoning над уже посчитанными признаками**, а не для генерации сигналов с нуля. Это максимально соответствует выводу QuantAgent о ценности price-driven decomposition и выводу StockBench о предпочтительности minimal workflow. citeturn33view0turn29view1

Что обязательно включить:
- target-position output, а не `BUY/SELL/HOLD` текст;
- risk gate до исполнения;
- post-trade reflection одним абзацем по каждой сделке;
- строгий JSON schema на каждом узле.

Что вы получите: самый высокий шанс на **скорость, контролируемость и воспроизводимость**. Что потеряете: часть event-driven edge и часть интерпретируемой “дискуссии”. Это нормально для hackathon. citeturn35view0turn14view3

### Balanced

Это мой **основной рекомендованный вариант**.

Архитектура: `SnapshotBuilder -> {IndicatorAgent, PatternAgent, TrendAgent, OptionalNewsAgent} -> SignalAggregator -> ConditionalChallenge -> RiskAgent -> PortfolioManager -> ExecutionValidator -> ExecutionAgent -> ReflectionMemory`.

Здесь вы уже берёте:
- price-specialization из QuantAgent;
- structured orchestration и manager approval из TradingAgents;
- selective communication и risk-first логику из FinCon;
- лёгкую retrieval/reflection идею из FinAgent/FinMem. citeturn33view0turn14view0turn26view4turn20view0turn21view2

Правила, без которых balanced-вариант быстро деградирует:
- **debate только по условию**, не чаще одного раунда;
- `OptionalNewsAgent` пропускается, если нет свежего события;
- memory хранит **уроки**, а не chat transcripts;
- execution layer имеет собственные правила и может отклонить решение PM.

Именно этот вариант, на мой взгляд, даёт лучший компромисс между **реальным edge, объяснимостью и стоимостью**. Он уже достаточно “агентный”, чтобы использовать LLM там, где он полезен, но ещё не настолько сложный, чтобы тонуть в latency и prompt-sprawl. Это лучше всего согласуется с наблюдениями StockBench и AI-Trader. citeturn29view1turn34view1

### Aggressive

Это вариант для команды, которая уже имеет working bot и хочет **сделать research-forward систему**, а не просто hackathon MVP.

Я бы добавлял сюда только:
- упрощённую FinCon-подобную **belief update** петлю, но **не онлайн**, а nightly/offline;
- multi-timescale memory c коротким горизонтом: `intraday events`, `multi-day regime`, `instrument behavior`;
- симуляционный/benchmark harness по духу InvestorBench/StockBench/AI-Trader;
- возможно отдельный event/regime classifier. citeturn26view2turn27view4turn25view0turn34view1

Чего я бы **не** делал даже в aggressive-варианте на первом проходе:
- full verbal reinforcement training;
- full autonomic web-search loop;
- multimodal chart-vision pipeline как основной канал сигналов;
- auto-code-generation quant loop в live stack. citeturn26view1turn34view1turn42academia0

Aggressive-вариант имеет смысл, только если вы уже доказали, что balanced-вариант переживает backtest и paper/live simulation без катастроф по regime shift, slippage и error handling. Иначе он просто увеличит сложность без гарантий alpha. citeturn31academia1turn29view4

## Риски и антипаттерны

| Риск | Как он проявится | Почему это вероятно | Что делать |
|---|---|---|---|
| **Over-agenting** | Красивые рассуждения, но хуже PnL и больше latency | StockBench сознательно минимизирует workflow; AI-Trader показывает, что “общий интеллект” не гарантирует trading performance. citeturn29view1turn34view1 | Держать graph узким, debate — только по конфликту |
| **Confusing QA skill with trading skill** | Модель хорошо отвечает на финансовые вопросы, но плохо торгует | StockBench, InvestorBench и оценка FinGPT показывают этот разрыв. citeturn29view4turn28view1turn8academia3 | Оценивать только на sequential backtest/paper/live harness |
| **Risk afterthought** | Красивый signal stack, но большие просадки и плохая переносимость между режимами | FinCon показал огромный вклад CVaR и belief-update risk control; AI-Trader делает тот же вывод на live benchmark. citeturn26view2turn34view1 | RiskAgent должен быть обязательным gatekeeper, а не advisor |
| **Long chat memory instead of useful memory** | Контекст разрастается, решения становятся шумными | TradingAgents и FinCon уходят от лишних коммуникаций; FinMem/FinAgent показывают ценность компактной retrieval-oriented памяти. citeturn10view0turn26view4turn21view2turn19view4 | Хранить lessons и retrieval keys, а не стенограммы |
| **No execution semantics** | Backtest выглядит хорошо, но live/paper исполнение разваливается | StockBench имеет execution/validation stage; FinRL-X разводит strategy и execution через единый интерфейс. citeturn25view0turn35view0turn38view2 | Всегда разделять target position, validation и order placement |
| **Wrong tools for wrong market** | Модули улучшают один рынок и портят другой | У FinAgent stock-specific auxiliary tools уронили результат на ETH. citeturn20view0 | Делать market-specific feature/tool registry |
| **Schema fragility** | Агент “думает” нормально, но ломает output | StockBench отдельно фиксирует arithmetic/schema errors, причём thinking models чаще ломают schema. citeturn30view0 | Строгие contract tests, parser-retry, validator node |
| **Regime blindness** | Система зарабатывает на ап-тренде и умирает на даунтренде | StockBench показывает, что в downturn окнах все LLM-agents проигрывали baseline. citeturn30view0 | Явный regime flag + risk throttling + do-not-trade mode |

## Ссылки и ограничения

### Ключевые papers / repos / docs

| Ресурс | Зачем смотреть |
|---|---|
| **TradingAgents paper**. citeturn9view0turn10view0 | Каркас analyst → debate → trader → risk → PM |
| **TradingAgents repo**. citeturn13view1turn14view0turn14view3 | Практическая LangGraph-реализация, reflection log, checkpointing |
| **FinMem paper**. citeturn9view1turn22view1 | Layered memory и adaptive risk persona |
| **FinAgent paper**. citeturn11view0turn20view0turn20view2 | Dual reflection, diversified retrieval, ablations |
| **FinCon paper**. citeturn24view0turn25view2turn26view2 | Manager-analyst hierarchy, CVaR, belief updates |
| **FinGPT paper / repo**. citeturn8academia0turn37view0turn38view3 | Финансовый NLP-toolkit и open-source backbone |
| **FinRobot papers / repo**. citeturn12view0turn12view1turn13view0 | Equity research automation; полезно как anti-pattern для intraday |
| **InvestorBench**. citeturn24view1turn25view1turn28view1 | Multi-task evaluation harness для stocks/crypto/ETF |
| **StockBench paper / repo**. citeturn24view2turn25view0turn37view2 | Minimal workflow, contamination-free evaluation, regime lessons |
| **QuantAgent paper / repo**. citeturn32view0turn33view0 | Самый близкий к short-horizon donor |
| **QuantAgents**. citeturn34view0 | Simulated trading outer-loop |
| **AI-Trader paper / repo**. citeturn34view1turn37view3 | Live benchmark mindset и выводы про risk robustness |
| **FinRL-X paper / repo**. citeturn35view0turn38view2 | Deployment-consistent execution architecture |
| **Trading-R1**. citeturn41academia0 | Куда, вероятно, движется следующий слой SOTA reasoning-for-trading |
| **RD-Agent(Q)**. citeturn42academia0 | Полезно как offline research automation, не как live stack |
| **StockSim**. citeturn31academia1 | Если захотите честно моделировать latency/slippage/microstructure |

### Открытые вопросы и ограничения

Главное ограничение этой картины в том, что **почти все сильные публичные результаты получены не на MOEX**, а на американских акциях, ETF, крипте, A-shares, Nasdaq futures и синтетических/исторических средах. Поэтому рекомендация для MOEX — это **перенос архитектурных принципов**, а не утверждение, что какая-то paper-архитектура уже доказала edge именно на российских акциях. Дополнительно, заметная часть результатов в papers остаётся backtest- или paper-trading-ориентированной, а более свежие benchmarks вроде StockBench и AI-Trader как раз показывают, что при честной постановке задача намного труднее, чем это часто выглядит по ранним paper-таблицам. citeturn25view0turn34view1turn31academia1

Итоговая рекомендация для `develop_2` в одном предложении: **строить не “мини-хедж-фонд из 12 агентов”, а компактный LangGraph с price-first analyst roles, conditional debate, обязательным RiskAgent, target-position PortfolioManager, отдельным execution/validation layer и дешёвой post-trade memory**. Именно такой набор идей лучше всего подтверждается текущими работами 2025–2026 и при этом остаётся реалистичным для hackathon trading. citeturn33view0turn14view0turn26view2turn29view1turn35view0