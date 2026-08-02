"""Strict bounded JSON parsing with legacy/N-A tolerance."""

import json
from typing import Any
from .models import *

class CryptoParseError(ValueError):pass
def _num(v):
    if v in (None,"N/A","n/a",""):return None
    try:return float(v)
    except (ValueError,TypeError):return None
def _dict(v):return v if isinstance(v,dict) else {}
def _list(v):return [str(x) for x in v] if isinstance(v,list) else []
def decode(text,max_bytes=1_048_576):
    if len(text.encode("utf-8"))>max_bytes:raise CryptoParseError("output_too_large")
    try:value=json.loads(text)
    except (json.JSONDecodeError,UnicodeError) as e:raise CryptoParseError("invalid_json") from e
    if not isinstance(value,dict):raise CryptoParseError("incompatible_schema")
    return value

class CryptoResultParser:
    def runtime(self,d):
        checks=d.get("checks") if isinstance(d.get("checks"),list) else []
        return {**d,"checks":[x for x in checks if isinstance(x,dict)]}
    def decision(self,d):
        b=_dict(d.get("score_breakdown",d));components=_dict(b.get("score_components",b.get("components")))
        return DecisionStatus(str(b.get("decision")) if b.get("decision") is not None else None,b.get("reason"),b.get("candle_timestamp"),_num(b.get("total_score",b.get("score"))),_num(b.get("entry_threshold")),_num(b.get("strong_entry_threshold",b.get("strong_threshold"))),_num(b.get("distance_to_entry")),_num(b.get("risk_allocation_pct",b.get("allocation_pct"))),_num(b.get("allocation_amount")),components,_list(b.get("limiting_components",b.get("limiters"))),_list(b.get("positive_factors")))
    def confidence(self,d):
        r=_dict(d.get("promotion_review",d.get("confidence_review",d)));c=r.get("confidence_score",r.get("confidence"))
        return ConfidenceReview(_num(c),r.get("confidence_level",r.get("level")),r.get("recommendation"),_list(r.get("blockers")),_list(r.get("positive_factors")),_num(r.get("days_observed")),int(r["closed_trades"]) if isinstance(r.get("closed_trades"),int) else None,int(r["comparable_candles"]) if isinstance(r.get("comparable_candles"),int) else None,r.get("stability"),r.get("stability_source"),bool(r.get("automatic_promotion",False)))
    def comparison(self,d,period):
        comp=_dict(d.get("comparison",d.get("strategy_comparison")));return StrategyComparison(period,_dict(comp.get("production",d.get("production"))),_dict(comp.get("candidate",d.get("candidate"))),_dict(comp.get("deltas")),comp.get("comparable_count"),_num(comp.get("agreement",comp.get("agreement_pct"))),comp.get("history_status"),_dict(comp.get("data_quality")))
    def equity(self,d,period,environment):
        env=_dict(_dict(d.get("environments")).get(environment));r=_dict(env.get("rolling"));a=_dict(env.get("aggregate"));q=_dict(env.get("quality"))
        start=_num(r.get("start_equity"));end=_num(r.get("end_equity",a.get("latest_equity")))
        return EquityHistory(period,start,end,(end-start) if start is not None and end is not None else _num(r.get("pnl")),_num(r.get("return_percent",r.get("return_pct"))),_num(r.get("max_drawdown_percent")),_num(r.get("current_drawdown_percent")),int(r["closed_trades"]) if isinstance(r.get("closed_trades"),int) else None,_num(r.get("fees")),_num(r.get("win_rate")),_num(r.get("profit_factor")),_num(r.get("daily_volatility")),_num(r.get("completeness_pct")),int(q["gap_count"]) if isinstance(q.get("gap_count"),int) else None)
