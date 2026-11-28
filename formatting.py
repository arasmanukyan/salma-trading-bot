"""
Price formatting helpers.
Mikro-fiyatlı coin'leri (örn. STAY/USDT: 0.00000743) de okunaklı gösterecek
akıllı ondalık formatı.
"""


def fmt_price(p) -> str:
    """
    Akıllı ondalık fiyat formatı.

    - p >= 100        -> 2 ondalık   (59,769.00)
    - 1 <= p < 100    -> 4 ondalık   (14.8400)
    - 0.001 <= p < 1  -> 6 ondalık   (0.014840)
    - p < 0.001       -> 8 ondalık   (0.00000743)
    """
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "-"

    ap = abs(p)
    if ap >= 100:
        return f"{p:,.2f}"
    elif ap >= 1:
        return f"{p:,.4f}"
    elif ap >= 0.001:
        return f"{p:,.6f}"
    else:
        return f"{p:,.8f}"


if __name__ == "__main__":
    for v in [59769.0, 14.84, 0.01484, 0.00000743, 100, 1, 0.001, 0, 1234567.891]:
        print(f"{v!r:>18} -> {fmt_price(v)}")
