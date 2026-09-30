"""Water viscosity used to normalise pressure drops to a reference temperature.

Bed samples may be taken at different temperatures. Viscous head loss scales
with dynamic viscosity, so every sample is reduced to a common 20 °C reference
before a curve is fitted; otherwise a colder feed alone could masquerade as a
dirty bed.

The table holds handbook values in mPa*s at atmospheric pressure. Samples
outside the tabled range are clamped to the nearest endpoint rather than
extrapolated.
"""

from bisect import bisect_left

REFERENCE_TEMPERATURE_C = 20.0

# Temperature (°C) -> dynamic viscosity (mPa*s), handbook values for water.
_VISCOSITY_TABLE: tuple[tuple[float, float], ...] = (
    (0.0, 1.792),
    (5.0, 1.519),
    (10.0, 1.307),
    (15.0, 1.139),
    (20.0, 1.002),
    (25.0, 0.890),
    (30.0, 0.798),
    (35.0, 0.720),
    (40.0, 0.653),
)


def viscosity_mPa_s(temperature_c: float) -> float:
    """Dynamic viscosity of water at ``temperature_c``, in mPa*s."""
    table = _VISCOSITY_TABLE
    if temperature_c <= table[0][0]:
        return table[0][1]
    if temperature_c >= table[-1][0]:
        return table[-1][1]
    index = bisect_left(table, (temperature_c, -1.0))
    left_temp, left_mu = table[index - 1]
    right_temp, right_mu = table[index]
    ratio = (temperature_c - left_temp) / (right_temp - left_temp)
    return left_mu + ratio * (right_mu - left_mu)


def temperature_factor(temperature_c: float) -> float:
    """Multiplier that reduces a pressure drop to the reference temperature.

    A drop measured at ``T`` converts to its 20 °C equivalent by multiplying
    it with ``mu_ref / mu_T`` (density is treated as constant across the
    narrow operating band).
    """
    return viscosity_mPa_s(REFERENCE_TEMPERATURE_C) / viscosity_mPa_s(temperature_c)
