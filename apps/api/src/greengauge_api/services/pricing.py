import json
from dataclasses import dataclass

from ..models import ModelUsageDelta


@dataclass(frozen=True)
class ModelRates:
    input: float = 0
    cached_input: float = 0
    cache_write: float = 0
    output: float = 0


class ModelPricingCatalog:
    """Token prices are USD per one million tokens and are runtime-configurable."""

    def __init__(self, rates: dict[str, ModelRates]):
        self.rates = {name.lower(): value for name, value in rates.items()}

    @classmethod
    def from_json(cls, raw: str) -> "ModelPricingCatalog":
        payload = json.loads(raw)
        return cls({
            name: ModelRates(
                input=float(values.get("input", 0)),
                cached_input=float(values.get("cached_input", 0)),
                cache_write=float(values.get("cache_write", values.get("input", 0))),
                output=float(values.get("output", 0)),
            )
            for name, values in payload.items()
        })

    def cost(self, usage: ModelUsageDelta) -> float:
        model_name = usage.model.lower()
        configured = self.rates.get(model_name)
        if configured is None:
            configured = next(
                (rates for alias, rates in self.rates.items() if alias in model_name),
                ModelRates(),
            )
        input_rate = usage.input_cost_per_million if usage.input_cost_per_million is not None else configured.input
        cached_rate = (
            usage.cached_input_cost_per_million
            if usage.cached_input_cost_per_million is not None
            else configured.cached_input
        )
        cache_write_rate = (
            usage.cache_write_cost_per_million
            if usage.cache_write_cost_per_million is not None
            else configured.cache_write
        )
        output_rate = usage.output_cost_per_million if usage.output_cost_per_million is not None else configured.output
        uncached_input_tokens = max(
            0, usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
        )
        return round((
            uncached_input_tokens * input_rate
            + usage.cached_input_tokens * cached_rate
            + usage.cache_write_tokens * cache_write_rate
            + usage.output_tokens * output_rate
        ) / 1_000_000, 8)
