from __future__ import annotations

DEFAULT_UNIVERSE: tuple[str, ...] = (
    "SBER", "VTBR", "T", "MOEX",
    "LKOH", "ROSN", "GAZP", "NVTK", "SNGSP",
    "GMKN", "CHMF", "NLMK", "PLZL", "ALRS",
    "MGNT", "X5",
    "YDEX", "MTSS", "AFLT", "PIKK",
)

EMITTER_NAMES_BY_TICKER: dict[str, tuple[str, ...]] = {
    "SBER": ("Сбербанк", "Сбер", "Sberbank"),
    "VTBR": ("ВТБ", "VTB"),
    "T": ("ТКС", "Т-Технологии", "TCS Group", "Тинькофф", "T-Bank"),
    "MOEX": ("Московская биржа", "Мосбиржа", "Moex"),
    "LKOH": ("ЛУКОЙЛ", "Lukoil"),
    "ROSN": ("Роснефть", "Rosneft"),
    "GAZP": ("Газпром", "Gazprom"),
    "NVTK": ("Новатэк", "НОВАТЭК", "Novatek"),
    "SNGSP": ("Сургутнефтегаз", "Surgutneftegas"),
    "GMKN": ("Норникель", "ГМК", "Norilsk Nickel"),
    "CHMF": ("Северсталь", "Severstal"),
    "NLMK": ("НЛМК", "Новолипецкий", "Novolipetsk"),
    "PLZL": ("Полюс", "Polyus"),
    "ALRS": ("АЛРОСА", "Alrosa"),
    "MGNT": ("Магнит", "Magnit"),
    "X5": ("X5", "Икс 5", "Пятёрочка"),
    "YDEX": ("Яндекс", "Yandex"),
    "MTSS": ("МТС", "MTS"),
    "AFLT": ("Аэрофлот", "Aeroflot"),
    "PIKK": ("ПИК", "PIK", "ПИК СЗ", "PIK Group"),
}


SECTOR_MAP: dict[str, str] = {
    "SBER": "FN", "VTBR": "FN", "T": "FN", "MOEX": "FN",
    "LKOH": "OG", "ROSN": "OG", "GAZP": "OG", "NVTK": "OG", "SNGSP": "OG",
    "GMKN": "MM", "CHMF": "MM", "NLMK": "MM", "PLZL": "MM", "ALRS": "MM",
    "MGNT": "CN", "X5": "CN",
    "YDEX": "IT",
    "MTSS": "TL",
    "AFLT": "TN",
    "PIKK": "RE",
}

# Lot size = сколько акций в одном «лоте». ArenaGo трактует `quantity` в
# submit_order как ЛОТЫ и сам умножает на lot_size при расчёте order_value
# (см. логи: qty=10 GMKN @ 129 → order_value=12 900 ₽ = 10 × 10 × 129).
#
# Эта таблица — **fallback** на случай если MOEX ISS недоступен при старте.
# В рантайме scheduler подтягивает актуальные LOTSIZE из ISS
# (`scheduler.lot_sizes.in_use` в Loki показывает что реально используется).
# Если в Loki появится событие `scheduler.lot_sizes.iss_baseline_drift` —
# значит биржа изменила лот, обнови этот файл при следующем коммите.
LOT_SIZE_BY_TICKER: dict[str, int] = {
    # Snapshot MOEX ISS LOTSIZE на 2026-05-20. Биржа меняет лоты при
    # дроблении/конвертации (например, SBER/VTBR/ROSN недавно стали lot=1).
    # В рантайме scheduler обновляет это из ISS — если ISS недоступен,
    # этот fallback ≈ актуален.
    "SBER": 1,
    "VTBR": 1,
    "T": 1,
    "MOEX": 10,
    "LKOH": 1,
    "ROSN": 1,
    "GAZP": 10,
    "NVTK": 1,
    "SNGSP": 10,
    "GMKN": 10,
    "CHMF": 1,
    "NLMK": 10,
    "PLZL": 1,
    "ALRS": 10,
    "MGNT": 1,
    "X5": 1,
    "YDEX": 1,
    "MTSS": 10,
    "AFLT": 10,
    "PIKK": 1,  # fallback; рантайм подтянет актуальный LOTSIZE из ISS
}


def parse_universe(csv: str) -> tuple[str, ...]:
    if not csv or not csv.strip():
        return DEFAULT_UNIVERSE
    tickers: list[str] = []
    seen: set[str] = set()
    for raw in csv.split(","):
        ticker = raw.strip().upper()
        if not ticker or ticker in seen:
            continue
        tickers.append(ticker)
        seen.add(ticker)
    return tuple(tickers) or DEFAULT_UNIVERSE
