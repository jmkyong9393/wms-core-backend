from decimal import Decimal, ROUND_HALF_UP


DEFAULT_NEW_STOCK_DISCOUNT_RATE = Decimal("0.1000")
PRICE_QUANTUM = Decimal("0.01")


def calculate_new_stock_default_price(
    base_price: Decimal,
) -> tuple[Decimal, Decimal]:
    """신간 재고의 기본 할인율과 판매가를 계산한다."""
    if base_price <= 0:
        raise ValueError("Book base price must be positive")

    sale_price = (
        base_price
        * (Decimal("1") - DEFAULT_NEW_STOCK_DISCOUNT_RATE)
    ).quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)

    return DEFAULT_NEW_STOCK_DISCOUNT_RATE, sale_price
