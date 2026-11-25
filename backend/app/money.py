from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        amount = value
    elif value is None or value == "":
        raise ValueError("金额为空")
    else:
        amount = Decimal(str(value))
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def money(value) -> str:
    return format(D(value), "f")
