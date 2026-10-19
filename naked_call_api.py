import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from backtest_call import run_naked_call_full_report

router = APIRouter()


class CallBacktestParams(BaseModel):
    symbol: str = "EWY"
    years: Optional[float] = Field(5, ge=0)             # 0 / null = all data
    cycle_days: int = Field(21, ge=7)
    call_otm: float = Field(0.10, ge=0, le=5)           # decimal ABOVE spot, e.g. 0.10 = 10% OTM
    call_prp: float = Field(0.03, ge=0, le=1)           # decimal, e.g. 0.03 = 3% premium
    call_otm_hedge: Optional[float] = Field(None, gt=0, le=10)  # spread width, null = naked
    total_margin: Optional[float] = Field(None, gt=0)
    utilize: Optional[float] = Field(None, gt=0)        # e.g. 1.0 = 100%
    include_cycles: bool = True                          # set false for a smaller response


def _run(p: CallBacktestParams):
    if (p.total_margin is None) != (p.utilize is None):
        raise HTTPException(400, "Provide both total_margin and utilize, or neither.")
    try:
        return run_naked_call_full_report(
            symbol=p.symbol.strip().lstrip("^"),
            years=p.years,
            cycle_days=p.cycle_days,
            call_otm=p.call_otm,
            call_prp=p.call_prp,
            call_otm_hedge=p.call_otm_hedge,
            total_margin=p.total_margin,
            utilize=p.utilize,
            data_dir="data",
            include_cycles=p.include_cycles,
        )
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# JSON body (fetch / axios / curl)
@router.post("/naked_call_report")
def naked_call_report(params: CallBacktestParams):
    return _run(params)


# HTML form body (application/x-www-form-urlencoded or multipart)
@router.post("/naked_call_report/form")
async def naked_call_report_form(request: Request):
    form = await request.form()
    data = {k: v for k, v in form.items() if isinstance(v, str) and v.strip() != ""}
    try:
        params = CallBacktestParams(**data)
    except ValidationError as e:
        raise HTTPException(422, detail=json.loads(e.json()))
    return _run(params)
