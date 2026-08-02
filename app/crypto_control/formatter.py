"""Safe Russian rendering without raw JSON."""

from .models import public
def value(v,suffix=""):return "N/A" if v is None else f"{v}{suffix}"
class CryptoFormatter:
    def overview(self,o):
        d=public(o);p=d["production_summary"];c=d["candidate_summary"];s=d["scored_candidate_summary"]
        return "\n".join(["📈 Crypto-Bot",f"Режим: {value(d['mode'])}",f"Символ: {value(d['symbol'])}",f"Health: {value(d['health_status'])}",f"Последняя свеча: {value(d['last_candle'])}",f"Market lag: {value(d['market_lag'])}",f"Active halt: {value(d['active_halt'] or 'нет')}","",f"Production: позиция {value(p.get('side'))}, equity {value(p.get('equity'))}",f"Candidate: confidence {value(c.get('confidence'))} — {value(c.get('level'))}",f"Scored Candidate: {value(s.get('decision'))}, score {value(s.get('score'))}, порог {value(s.get('entry_threshold'))}, не хватает {value(s.get('distance_to_entry'))}",f"Данные: {value(d['data_timestamp'])}"])
