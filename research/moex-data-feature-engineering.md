# Практические источники данных и feature engineering для MOEX equity trading bot на базе MOEX ISS и AlgoPack

## Ключевой вывод

Для бота по ликвидным акциям MOEX с решениями каждые 15–30 минут оптимальная архитектура выглядит так: **AlgoPack как основной источник короткого alpha-сигнала**, **ISS как обязательный источник календаря, индексов, статуса торгов, рыночного состояния и, при необходимости, сырого order book / trades**, а **новости и раскрытие — как редкий, но важный слой событийных фильтров и risk-off правил**. Это следует из того, что AlgoPack уже дает агрегированные признаки по потоку сделок, заявок и стакану каждые 5 минут и доступен для online use, тогда как ISS покрывает свечи, сделки, котировки, календарь, индексы и метаданные, но без подписки данные могут приходить с задержкой, а любое использование для извлечения прибыли требует договорных прав на рыночную информацию. В микроструктурной литературе именно **order-flow imbalance, volume imbalance и depth/spread** consistently объясняют краткосрочные движения лучше, чем «голый» торговый объем или набор обычных candle-осцилляторов. citeturn37search0turn30view0turn17search1turn17search17turn19view2

Для вашего сетапа важнее не добавлять еще десяток классических индикаторов по 60-минутным свечам, а **смешать три слоя признаков**:
**направление** через дисбаланс сделок и стакана, **исполняемость** через spread/depth/cancel pressure, и **режим** через 60-минутный price state, индекс/сектор и дневную волатильность. При этом дневные признаки полезны прежде всего как фильтр режима и sizing layer, а не как непосредственный 15–30-минутный триггер входа. citeturn17search1turn22search16turn21search0turn43view3turn10view0

Если нужен **минимум, который можно внедрить быстро**, то первый релиз должен состоять из: `TradeStats.disb`, `OBStats.imbalance_vol_bbo`, `OBStats.spread_bbo`, `OBStats.spread_1mio`, signed net queue pressure из `OrderStats`, rolling `ret_60m`, `range_60m`, relative volume surprise и IMOEX/sector regime. Это даст наибольший прирост по сравнению с текущей candle-only логикой при умеренной сложности интеграции. citeturn4view1turn4view2turn4view4turn39view1turn39view0

## Ранжированный список признаков

1. **Signed trade imbalance из TradeStats**: `disb`, а также раздельные `vol_b/vol_s`, `val_b/val_s`. Это главный кандидат на short-horizon alpha. Официально `disb` — это signed соотношение продавец/покупатель, а в исследованиях именно order flow imbalance устойчиво объясняет краткосрочные изменения цен лучше, чем чистый объем; отдельные работы по LOB показывают, что imbalance помогает предсказывать знак следующего market order и ближайшее изменение цены. Практически: хранить `disb_5m`, `ewm_disb_15m`, `ewm_disb_30m`, а для межбумажной сопоставимости — robust z-score по бумаге и часу дня. citeturn4view1turn17search1turn17search2turn17search17

2. **Best-book imbalance из OBStats**: `imbalance_vol_bbo`, `imbalance_val_bbo`. Это лучший прокси ближайшего давления на цену, потому что он сидит ближе всего к исполнимому bid/ask. Для 15–30-минутного бота полезно хранить не только уровень, но и изменение за последние 2–3 пятиминутки: сильнее всего работают ситуации, где imbalance ускоряется, а не просто остается большим. citeturn4view4turn17search2turn22search16

3. **Liquidity cost и spread-layer**: `spread_bbo`, `spread_1mio`, `spread_lv10`. Эти признаки нужны не только для cost model, но и как модераторы сигнала: один и тот же imbalance гораздо полезнее при узком BBO spread и приемлемом `spread_1mio`, чем при расползшемся стакане. В классической работе Cont–Kukanov–Stoikov наклон price impact обратно связан с глубиной рынка, а официальные поля AlgoPack как раз дают spread в bps на разных слоях ликвидности. citeturn4view2turn4view3turn17search1

4. **Queue pressure из OrderStats**: `put_*`, `cancel_*` по buy/sell сторонам. Здесь самый полезный derived feature —
   `net_queue_pressure = (put_vol_b - cancel_vol_b) - (put_vol_s - cancel_vol_s)`.
   Дополнительно полезен `cancel_asym = cancel_vol_b - cancel_vol_s` и отношения `cancel/put` по сторонам. Это важно потому, что краткосрочную цену двигают не только сделки, но и события книги заявок — новые лимитные заявки и снятия, что прямо показано в исследованиях order book events. citeturn4view1turn4view2turn17search1

5. **Multi-level book state**: `imbalance_vol`, `imbalance_val`, `vol_b`, `vol_s`, `levels_b`, `levels_s`, `vwap_b`, `vwap_s`. Если использовать только BBO, часть информации теряется. Работы по integrated / multi-level OFI показывают, что совмещение нескольких уровней книги объясняет цену лучше, чем лучший уровень сам по себе. В AlgoPack это можно приблизить без собственного order-log: одновременно использовать all-book imbalance, число уровней и разницу между `spread_lv10` и `spread_bbo` как proxy book slope / liquidity curvature. citeturn4view3turn4view4turn22search16turn38search8

6. **Short-horizon volatility state**: `pr_std` из TradeStats плюс rolling realized vol/range по свечам. Это скорее **гейт** и **sizing factor**, чем чистый directional predictor. Высокочастотные данные существенно улучшают измерение и прогнозирование волатильности, а сами microstructure-сигналы работают по-разному в спокойных и stressed режимах. Практически: `pr_std_5m`, `rv_15m`, `rv_60m`, `range_60m / atr_1d`. citeturn4view1turn43view3

7. **Relative volume / turnover surprise**: `trades`, `vol`, `val` из TradeStats, но в виде surprise-метрик, а не raw values. У intraday volume и volatility выраженная связь и сильная time-of-day сезонность, поэтому «объем выше обычного для 11:30 по этой бумаге» намного полезнее, чем просто высокий абсолютный объем. Практически: `log(vol_5m)` и `log(val_5m)` нормализовать относительно rolling median/MAD для той же пятиминутки дня за предыдущие 20–40 сессий. citeturn4view0turn4view1turn26search2turn21search0

8. **Rolling 60-minute price state**: не часовые свечи как единственный входной сигнал, а контекст: `ret_60m_roll`, `range_60m`, `close_location_60m`, `distance_to_roll_vwap`, `breakout_vs_prev_day_high/low`. Это нужно оставить, потому что bot уже живет на 60m логике, но важно понизить вес этих признаков относительно microstructure. Исследования intraday returns показывают, что очень короткие reversals часто загрязнены liquidity imbalance и bid-ask bounce, то есть 60m state полезнее как контекст, чем как самостоятельный edge. citeturn3view0turn21search0turn22search15

9. **Index / sector relative layer**: `stock_ret - IMOEX_ret`, `stock_ret - sector_ret`, `IMOEX_vol_60m`, `sector_vol_60m`, а также universe-level median imbalance. Литература по cross-impact показывает, что лагированные cross-asset OFI действительно помогают в прогнозе будущих intraday returns, хотя эффект быстро затухает. Для MOEX это практично, потому что основной индекс считается intraday раз в секунду, а секторные индексы — каждые 15 секунд. citeturn22search16turn22search10turn39view1turn39view0

10. **Daily regime / concentration layer**: `daily_ret`, `daily_rv`, `atr_1d`, `gap_to_prev_close`, `HI2`. Индекс рыночной концентрации HI2 обновляется ежедневно в 19:00 и лучше подходит для режима типа «узкий рынок / широкий рынок / dominated by few names», чем для intraday timing. На этом горизонте ежедневные признаки должны отвечать на вопрос «можно ли вообще доверять microstructure-сигналам сегодня» и «какой размер позиции допустим», а не «входить ли сейчас». citeturn10view0turn22search11turn43view3

11. **Event flags из календаря и раскрытия**: `session_changed`, `suspended`, `security_attribute_changed`, `dividend_window`, `earnings_window`, `material_fact_window`. Эти признаки редко дают стабильный continuous alpha, но отлично работают как hard filters: не открывать новые intraday позиции рядом с дивидендной отсечкой, изменением параметров бумаги, запретом торгов или существенным disclosure. Причем structured ISS-calendar для этого надежнее, чем сырые текстовые новости. citeturn9view0turn40view0turn35view1turn14news30

12. **MegaAlerts как опциональный overlay**: использовать только факт и тип аномалии, а не готовое поле `Reference`, пока вы не убедитесь, что в historical режиме оно хранится point-in-time. По документации MegaAlerts работает на минутных аномалиях и содержит историческую статистику того, что было после похожих событий через 5/15/30/60 минут; такая сводка может быть полезна онлайн как справка, но в офлайн-обучении легко превратиться в скрытый leakage, если брать неархивированную выгрузку. citeturn10view1turn37search0

**Минимальный стартовый набор для develop_2**:
`disb_5m_z`, `disb_15m_ewm_z`, `imbalance_vol_bbo_z`, `spread_bbo_z`, `spread_1mio_z`, `net_queue_pressure_5m_z`, `pr_std_5m_z`, `ret_60m_roll`, `range_60m_norm`, `rvol_60m_tod_z`, `imoex_ret_60m`, `sector_rel_ret_60m`. Этого достаточно, чтобы проверить, добавляет ли AlgoPack реальный short-horizon edge поверх текущих candle-indicators. citeturn4view1turn4view4turn39view1turn39view0

## Сравнение источников данных

**ISS** — это базовый инфраструктурный слой. Через него доступны свечи, сделки, marketdata, индексы, метаданные и календарь, включая `session_schedule`, `suspended`, `securities changes` и другие служебные таблицы. Для equity bot это незаменимо для торгового расписания, статуса инструмента, индексов, точек входа/выхода и, если есть правильная подписка, для собственного расчета 1m и event-level признаков по trades/orderbook. Критичный нюанс: официальная документация ISS говорит, что без подписки биржевая информация может предоставляться с задержкой, а developer manual отдельно указывает задержку 15 минут для неаутентифицированного equity market data, при этом индексы являются исключением. Кроме того, текущая страница ISS прямо пишет, что использование данных для извлечения прибыли возможно только по договору с MOEX. citeturn30view0turn37search2turn9view0turn39view1turn39view0

**AlgoPack** — лучший практический источник именно для вашего следующего шага. Он уже агрегирует поток сделок, заявок и стакана в Super Candles, обновляемые каждые 5 минут с историей с 2020 года, а также дает дополнительные продукты вроде HI2 и MegaAlerts. Это резко снижает engineering cost по сравнению с самостоятельным расчетом OFI и book features из сырого ISS orderbook. Для бота с решениями каждые 15–30 минут это почти идеальный trade-off между свежестью, объяснимостью и сложностью. citeturn37search0turn2view0turn10view0turn10view1

**Новости и раскрытие** лучше использовать как слой событийных фильтров, а не как основной intraday alpha. Банк России аккредитует агентства раскрытия, а Interfax/e-disclosure поддерживает машиночитаемые сценарии публикации, но сами disclosure-события редки, асинхронны и плохо ведут себя как стабильный непрерывный сигнал на 15–30-минутном горизонте. Более того, в российской среде часть эмитентов может ограничивать раскрытие санкционно-чувствительной информации, поэтому coverage бывает неполным. Даже MOEX-страница record dates помечена как информационная и без гарантий полноты/надежности. В итоге новости и disclosure нужны — но прежде всего для filter / cooldown / hard risk rules вокруг earnings, dividends, buybacks, крупных корпоративных действий и ограничений торгов. citeturn35view1turn34search0turn14news30turn40view0turn26search0

**Практический выбор**:
если цель — быстро улучшить текущий бот, то **AlgoPack = основной alpha-слой**, **ISS = обязательный companion-source**, **news/disclosure = отдельный overlay**. Если позже понадобится глубже уходить в 1m или event-driven logic, тогда уже стоит добавлять raw ISS orderbook/trades и, при необходимости, более низколатентные market-data feeds. Для диапазона 15–30 минут ISS+AlgoPack уже дают достаточную глубину. citeturn37search0turn33search2turn24search0

## Рекомендация по таймфреймам

**Одноминутные признаки** для вашего бота полезны только как промежуточный строительный материал, если вы сами считаете их из raw ISS trades/orderbook. Как основной входной слой они слишком шумные для 15–30-минутного decision cycle, а very-short-term returns особенно подвержены bid-ask bounce и временным liquidity imbalances. Дополнительно Super Candles в AlgoPack идут с шагом 5 минут, а не 1 минуту; из минутного слоя внутри AlgoPack есть только MegaAlerts-аноматии, а не полный набор нормализованных microstructure features. citeturn21search0turn22search15turn37search0turn10view1

**Десятиминутный и 15–30-минутный слой** — лучший практический горизонт для ваших alpha-features. Причина проста: поведенческое содержание order-flow и стакана действительно живет на высоких частотах, но его полезная предсказательная сила быстро затухает; в работах по LOB effective forecasting horizon оказывается коротким, а lagged cross-asset effects проявляются на коротких intraday horizon и быстро исчезают. Поэтому оптимально брать 5-минутные AlgoPack bars и агрегировать их в `last_10m`, `last_15m`, `last_30m`, а не ждать закрытия следующей полноценной 60m свечи. citeturn29search3turn22search16turn19view2turn37search0

**Шестидесятиминутный слой** должен остаться, но как state/regime context. Для вашей текущей архитектуры это естественный мост от существующих candle indicators к новым microstructure-фичам. Важно только перестать трактовать 60m свечу как единственный источник сигнала: гораздо лучше вычислять rolling 60m state каждые 5–15 минут из завершенных 5m блоков, чтобы не торговать на устаревшей часовой информации. citeturn3view0turn37search0turn21search0

**Дневной слой** полезен в роли regime filter: дневная realized/parked volatility, предыдущий gap, daily trend, HI2 и event window around disclosures/dividend dates. Он должен влиять на threshold, sizing и запрет на торговлю в «плохих» режимах, но не быть главным directional predictor для следующего 15-минутного окна. Высокочастотные данные хорошо измеряют ежедневную волатильность, но это уже другой уровень задач — risk and regime, а не fine timing. citeturn43view3turn10view0

**Итоговая рекомендация по частотам**:
- **primary alpha**: 5m признаки, агрегированные в **10m/15m/30m** окна;
- **context**: rolling **60m** state;
- **regime**: **daily** features;
- **1m**: только позднее, если появится необходимость в собственных raw-event features из ISS. citeturn37search0turn19view2turn29search3

## Рекомендуемая схема MarketSnapshot

`MarketSnapshot` должен хранить не просто «значения признаков на момент t», а **точно разделять**:
**когда закончился интервал признака**, **когда биржа/источник опубликовали значение**, **когда вы его получили**, и **когда bot принял решение**. Это критично, потому что ISS и календарные таблицы несут `SYSTIME` / `UPDATETIME`, а разные источники могут приходить с разной задержкой. Без этих timestamp-слоев невозможно надежно доказать отсутствие look-ahead. citeturn3view0turn9view0turn10view0

```python
@dataclass
class MarketSnapshot:
    # identity
    secid: str
    boardid: str
    decision_ts_msk: datetime
    session_id: int
    feature_version: str

    # source timing / data quality
    source_bar_end_ts: datetime          # конец последнего завершенного feature-bar
    iss_publish_ts: datetime | None      # SYSTIME / UPDATETIME, если есть
    algopack_publish_ts: datetime | None
    ingested_ts: datetime
    delayed_flag: bool
    complete_bar_flag: bool
    stale_seconds: float

    # executable state
    bid: float | None
    ask: float | None
    mid: float | None
    last: float | None
    spread_bps_live: float | None

    # candle / price state
    ret_15m_roll: float
    ret_60m_roll: float
    range_60m_norm: float
    close_location_60m: float
    roll_vwap_dev_60m: float
    rvol_60m_tod_z: float

    # TradeStats
    disb_5m_z: float
    disb_15m_ewm_z: float
    buy_sell_value_skew_5m_z: float
    pr_std_5m_z: float
    trades_5m_tod_z: float
    value_5m_tod_z: float

    # OrderStats
    net_queue_pressure_5m_z: float
    cancel_asym_5m_z: float
    cancel_put_ratio_b_z: float
    cancel_put_ratio_s_z: float

    # OBStats
    imb_bbo_vol_z: float
    imb_bbo_val_z: float
    imb_all_vol_z: float
    imb_all_val_z: float
    spread_bbo_z: float
    spread_1mio_z: float
    spread_lv10_minus_bbo_z: float
    levels_ratio_z: float
    book_vwap_gap_z: float

    # index / sector / regime
    imoex_ret_60m: float
    imoex_vol_60m: float
    sector_ret_60m: float
    rel_ret_vs_imoex_60m: float
    rel_ret_vs_sector_60m: float
    hi2_daily_z: float | None

    # events / status
    suspended_flag: bool
    security_changed_today: bool
    corp_action_window_flag: bool
    disclosure_window_flag: bool
    open_auction_window_flag: bool
    close_auction_window_flag: bool
```

С точки зрения engineering лучше сразу делать **multi-view schema**: отдельно price-state, отдельно trade-flow, отдельно order-flow, отдельно book-state, отдельно regime, отдельно event/status. Так легче проводить ablation tests и видеть, какой слой действительно добавляет alpha, а какой лишь дублирует существующие candle indicators. citeturn17search1turn22search16turn19view2

Внутри `MarketSnapshot` стоит хранить не только уровни признаков, но и **их изменения** и **time-of-day normalized версии**. Для вашего use case наиболее полезны три типа преобразований:
**level** (`imb_bbo_vol_z`), **delta** (`delta_10m`), **interaction** (`disb_15m_ewm_z / (1 + spread_bbo_z)` или `disb_15m_ewm_z * low_spread_flag`). Это хорошо соответствует как literature on OFI/depth, так и реальной торговой логике — сигнал сам по себе важен меньше, чем сигнал при приемлемой ликвидности и умеренной волатильности. citeturn17search1turn21search0turn43view3

## Чек-лист по leakage и timing

- **Никогда не смешивать delayed ISS equities с real-time индексами и AlgoPack**. В official ISS manual прямо указано, что без авторизации market data для акций могут идти с 15-минутной задержкой, при этом индексы — отдельный случай. Это самый опасный тип «ложной предсказуемости», когда индекс уже live, а акции фактически прошлые. citeturn37search2turn30view0

- **Использовать только завершенные бары признаков**. Для AlgoPack это означает: в решение на `t` можно включать лишь последнюю полностью закрытую 5-минутку и агрегаты из закрытых 5-минуток. Нельзя обучаться или торговать так, будто бар доступен в тот же миг, когда закончился, если вы не храните реальное publish/ingest time. citeturn37search0turn3view0turn9view0

- **Хранить три времени для каждой записи**: `interval_end`, `source_publish_time`, `ingested_at`. В ISS это могут быть `SYSTIME` и `UPDATETIME`; в календаре — `updatetime`; в ваших feature stores — собственное `ingested_at`. Решение должно видеть только данные, у которых `publish_time <= decision_ts`. citeturn3view0turn9view0turn10view0

- **Цель модели считать по mid / executable price, а не по last trade close-to-close**. Для super-short horizons bid-ask bounce способен создавать искусственные reversals. Минимум — оценивать forward return по midquote; если mid недоступен исторически, то хотя бы вычитать оценку half-spread и slippage из `spread_bbo` / `spread_1mio`. citeturn22search15turn21search0turn4view3turn3view0

- **Нормализацию по времени суток строить только на прошлом**. Нельзя считать z-score пятиминутки 11:30, используя будущие значения того же дня. Для intraday microstructure time-of-day seasonality очень сильна, но она должна быть point-in-time seasonal baseline, а не full-day hindsight correction. citeturn21search0turn17search1

- **Делать point-in-time universe**. В backtest нельзя брать список «сегодняшних ликвидных имен» и тащить его назад по истории. Официальные материалы ISS отдельно рекомендуют использовать listing history/boards history, а календарный раздел содержит `securities changes`; иначе появится survivorship bias и неправильные board mappings. citeturn25search3turn25search7turn9view0

- **Не полагаться на record dates page как на окончательную corporate-actions truth**. MOEX сама пишет, что эта страница носит информационный характер и без гарантий полноты/надежности. Для обучения и торговли по дивидендным окнам нужны structured corporate-action rules, disclosure / official notices и явная price-adjustment logic. citeturn40view0turn35view1

- **Использовать официальный торговый календарь и session schedule**. На MOEX нельзя жестко зашивать часы торгов и предполагать, что каждая бумага всегда торгуется в одном и том же режиме без исключений. Для future planned suspensions, special sessions, settlement-code restrictions и board-specific windows есть отдельные ISS tables. citeturn9view0

- **С MegaAlerts быть особенно осторожным**. Поле `Reference` содержит историческую статистику поведения цены после похожих аномалий. Если вы тянете исторические данные не в point-in-time срезе, это легко превращается в hidden look-ahead. Безопаснее сначала использовать только сам факт аномалии и ее код. citeturn10view1

- **Не оценивать alpha без учета liquidity gate**. Если backtest дает хороший gross signal, но входы происходят в моменты высокого `spread_1mio` или разреженного book, результат почти наверняка переоценен. Для short-horizon equity logic spread/depth — часть моделирования alpha, а не только часть simulator costs. citeturn4view2turn4view3turn17search1

- **Проверить правовой и коммерческий режим использования данных**. Официальная страница ISS прямо разделяет «ознакомление» и использование для прибыли/сервисов; live bot нельзя строить на предположении, что публичный delayed ISS feed годится и технически, и юридически. citeturn30view0

## План внедрения для develop_2

**Этап данных**. Сначала нужен не новый model stack, а правильный data plumbing. Источники для develop_2:
`AlgoPack: tradestats / orderstats / obstats / hi2`,
`ISS: candles / marketdata / calendars / indices / securities changes`,
опционально later — raw `trades/orderbook` при наличии подписки, если захотите выходить в 1m/event-level features. Все сырые таблицы сохранять в point-in-time слое с `secid`, `boardid`, `bar_end`, `source_publish_ts`, `ingested_ts`, `decision_eligible_flag`. citeturn37search0turn3view0turn9view0turn10view0

**Этап признаков**. Первый production-like feature set должен быть маленьким и объяснимым. Рекомендуемый v1 для develop_2:
`disb_5m_z`, `disb_15m_ewm_z`, `imb_bbo_vol_z`, `imb_bbo_val_z`, `spread_bbo_z`, `spread_1mio_z`, `net_queue_pressure_5m_z`, `cancel_asym_5m_z`, `pr_std_5m_z`, `ret_60m_roll`, `range_60m_norm`, `rvol_60m_tod_z`, `imoex_ret_60m`, `sector_rel_ret_60m`, `corp_action_window_flag`. Это покрывает direction, liquidity, risk, regime и event filter без раздувания dimensionality. citeturn4view1turn4view2turn4view4turn39view1turn39view0turn9view0

**Этап лейблов и оценки**. Основные target’ы: `fwd_ret_15m_mid` и `fwd_ret_30m_mid`; параллельно полезно считать classification target вида `fwd_ret > estimated_cost`. Метрики — not только accuracy, но и net expectancy, turnover, hit rate after costs и отдельно performance в low-spread / high-spread режимах. Если модель «работает» только до учета spread, значит это не alpha, а microstructure illusion. citeturn22search15turn21search0turn4view3

**Этап торговой логики**. В develop_2 не стоит сразу строить сложную ML-стратегию на 50+ фичах. Практичнее сделать rule-based or shallow-model overlay:
сигнал на long, если `trade_imbalance > threshold`, `book_imbalance > 0`, `spread_bbo` и `spread_1mio` ниже пределов, а `ret_60m_roll` не против тренда;
на short — симметрично.
Даже если потом появится LightGBM / logistic model, эти rules останутся useful sanity layer. citeturn17search1turn17search17turn4view2turn4view4

**Этап режима и фильтров**. Сразу встроить в bot три фильтра:
`liquidity_filter`, `event_filter`, `session_filter`.
`liquidity_filter` опирается на `spread_bbo`, `spread_1mio`, `levels`;
`event_filter` — на ISS calendar/disclosure flags;
`session_filter` — на official `session_schedule` и окна открытия/закрытия, где microstructure искажена сильнее всего. Это часто дает больше реального улучшения PnL, чем очередные слабые признаки. citeturn4view3turn9view0turn35view1

**Этап rollout**. Логичный sequence для develop_2:
сначала historical rebuild и walk-forward backtest, затем shadow mode на live data с логированием `decision_ts`, `available_source_ts`, `stale_seconds`, и только после этого ограниченный capital deployment. Для live-monitoring обязательны алерты на `delayed_flag`, пропуск feature-bar и расхождение между expected session state и ISS calendar state. citeturn30view0turn9view0turn3view0

## Открытые вопросы и ограничения

Прямых публичных исследований, которые бы **именно по MOEX TQBR** ранжировали `TradeStats`, `OrderStats` и `OBStats` по predictive power на 15–30-минутном горизонте, в просмотренных материалах не обнаружено. Поэтому ranking выше — это **практически обоснованная экстраполяция** из общей микроструктурной литературы и официальных определений полей AlgoPack, а не готовая MOEX-specific truth table. citeturn17search1turn17search17turn22search16turn4view1turn4view4

Также в доступной документации видно cadence продуктов — 10 секунд для real-time market data, 5 минут для Super Candles, 1 минута для MegaAlerts, daily 19:00 для HI2, — но не полностью раскрыта точная end-to-end latency point-in-time публикации каждого поля в вашем коммерческом контуре. Это нужно верифицировать в вашей production-среде до реального запуска. citeturn37search0turn10view0turn10view1

Наконец, слой disclosure/news в российском рынке нельзя считать полным и стабильным источником alpha из-за неравномерной машиночитаемости, event-driven природы и возможных ограничений раскрытия по отдельным эмитентам. Поэтому в develop_2 его лучше трактовать как **risk/event overlay**, а не как центральную сигнальную подсистему. citeturn35view1turn14news30turn40view0