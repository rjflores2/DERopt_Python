"""Per-scenario data lookup with fallback to the base/default series.

Mirrors the sparse-override design of ``DataContainer.scenario_timeseries`` /
``scenario_import_prices_by_node``: a scenario that doesn't override a given series
uses the base value, so a case with no scenarios configured (``scenario_keys ==
["_default"]``) behaves identically to today's deterministic model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from data_loading.schemas import DataContainer


def validate_scenario_probabilities(
    scenario_keys: list[str], scenario_probability: dict[str, float]
) -> None:
    """Fail fast on invalid scenario keys/probabilities.

    Single source of truth for the arithmetic checks (non-empty, no blank/whitespace keys,
    no duplicates, every key has a probability, every probability > 0, probabilities sum to
    1.0 within 1e-6) shared by ``DataContainer.validate_scenarios``, ``model.core.build_model``,
    and ``run.build_run_data``'s config-layer validation -- so the three layers can't drift
    the way they previously did (one checked for blank keys, the others didn't).
    """
    if not scenario_keys:
        raise ValueError("scenario_keys must be non-empty")
    if any(not k.strip() for k in scenario_keys):
        raise ValueError(f"scenario keys must be non-empty (non-whitespace): {scenario_keys}")
    if len(set(scenario_keys)) != len(scenario_keys):
        raise ValueError(f"duplicate scenario keys: {scenario_keys}")
    missing = [s for s in scenario_keys if s not in scenario_probability]
    if missing:
        raise ValueError(f"scenario_probability missing entries for scenarios: {missing}")
    non_positive = [s for s in scenario_keys if scenario_probability[s] <= 0]
    if non_positive:
        raise ValueError(f"scenario probabilities must be > 0; got non-positive for: {non_positive}")
    total = sum(scenario_probability[s] for s in scenario_keys)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"scenario probabilities must sum to 1.0 (within 1e-6); got {total}")


def series_for_scenario(data: DataContainer, scenario_key: str, series_key: str) -> list[float]:
    """Return ``series_key``'s series for ``scenario_key``, falling back to the base series."""
    per_scenario = data.scenario_timeseries.get(scenario_key)
    if per_scenario is not None and series_key in per_scenario:
        return per_scenario[series_key]
    return data.timeseries[series_key]


def import_price_for_scenario(data: DataContainer, scenario_key: str, node: str) -> list[float] | None:
    """Return ``node``'s import-price series for ``scenario_key``, falling back to the base value.

    Returns ``None`` (not an error) when neither the scenario nor the base data define a
    price for this node -- e.g. an islanded/demand-charge-only case. Callers decide how to
    treat "no price configured" (typically a zero-price default), since that's a modeling
    choice, not a data-integrity error.
    """
    per_scenario = data.scenario_import_prices_by_node.get(scenario_key)
    if per_scenario is not None and node in per_scenario:
        return per_scenario[node]
    if data.import_prices_by_node is None:
        return None
    return data.import_prices_by_node.get(node)


def utility_rate_for_scenario(data: DataContainer, scenario_key: str, node: str) -> Any | None:
    """Return ``node``'s parsed utility-rate object for ``scenario_key``, falling back to the
    base value. Returns ``None`` when neither defines a rate for this node."""
    per_scenario = data.scenario_utility_rate_by_node.get(scenario_key)
    if per_scenario is not None and node in per_scenario:
        return per_scenario[node]
    if data.utility_rate_by_node is None:
        return None
    return data.utility_rate_by_node.get(node)


def has_any_scenario_utility_override(data: DataContainer, scenario_keys: list[str]) -> bool:
    """True if any scenario defines its own import-price or utility-rate override.

    Used to decide whether the utility block must be built even when the base case has no
    prices/rates configured (e.g. base ``utility_mode="islanded"`` but individual scenarios
    set their own ``utility_tariffs``) -- the block-attachment decision must consider
    scenario-level data, not just the base, or scenario-only tariffs get silently dropped.
    """
    return any(
        bool(data.scenario_import_prices_by_node.get(s)) or bool(data.scenario_utility_rate_by_node.get(s))
        for s in scenario_keys
    )
