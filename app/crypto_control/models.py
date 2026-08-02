"""Typed public crypto diagnostics."""

from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass(slots=True)
class CryptoOverview:
    mode:str|None=None;symbol:str|None=None;timeframe:str|None=None;current_price:float|None=None;last_candle:str|int|None=None;market_lag:float|None=None;health_status:str|None=None;active_halt:str|None=None;production_summary:dict[str,Any]=field(default_factory=dict);candidate_summary:dict[str,Any]=field(default_factory=dict);scored_candidate_summary:dict[str,Any]=field(default_factory=dict);data_timestamp:str|None=None
@dataclass(slots=True)
class PositionStatus:
    environment:str="production";side:str|None=None;quantity:float|None=None;entry_price:float|None=None;market_price:float|None=None;cash:float|None=None;equity:float|None=None;realized_pnl:float|None=None;unrealized_pnl:float|None=None;total_pnl:float|None=None;return_pct:float|None=None;fees:float|None=None;drawdown:float|None=None;last_updated:str|int|None=None
@dataclass(slots=True)
class DecisionStatus:
    decision:str|None=None;reason:str|None=None;candle_timestamp:str|int|None=None;score:float|None=None;entry_threshold:float|None=None;strong_threshold:float|None=None;distance_to_entry:float|None=None;allocation_pct:float|None=None;allocation_amount:float|None=None;components:dict[str,Any]=field(default_factory=dict);limiters:list[str]=field(default_factory=list);positive_factors:list[str]=field(default_factory=list)
@dataclass(slots=True)
class StrategyComparison:
    period:str="24h";production:dict[str,Any]=field(default_factory=dict);candidate:dict[str,Any]=field(default_factory=dict);deltas:dict[str,Any]=field(default_factory=dict);comparable_count:int|None=None;agreement:float|None=None;history_status:str|None=None;data_quality:dict[str,Any]=field(default_factory=dict)
@dataclass(slots=True)
class ConfidenceReview:
    confidence:float|None=None;level:str|None=None;recommendation:str|None=None;blockers:list[str]=field(default_factory=list);positive_factors:list[str]=field(default_factory=list);days_observed:float|None=None;closed_trades:int|None=None;comparable_candles:int|None=None;stability:float|str|None=None;stability_source:str|None=None;automatic_promotion:bool=False
@dataclass(slots=True)
class EquityHistory:
    period:str="all";start_equity:float|None=None;end_equity:float|None=None;pnl:float|None=None;return_pct:float|None=None;max_drawdown:float|None=None;current_drawdown:float|None=None;trades:int|None=None;fees:float|None=None;win_rate:float|None=None;profit_factor:float|None=None;volatility:float|None=None;completeness:float|None=None;gaps:int|None=None;source:str="historical_equity_snapshots"
@dataclass(slots=True)
class RuntimeIssue:
    severity:str;code:str;component:str;summary:str;technical_detail:str|None=None;suggested_read_only_check:str|None=None

def public(value):return asdict(value) if hasattr(value,"__dataclass_fields__") else value
