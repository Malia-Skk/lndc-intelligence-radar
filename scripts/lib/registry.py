"""
Loads config/sources.yaml so fetch scripts read their scope (countries,
indicators, cadence, output path) from one shared file instead of hardcoding
it at the top of each script.

Deliberately minimal: this is a lookup helper, not a generic task runner.
Each data source (World Bank, Comtrade, GDELT, ...) has a different enough
API shape that a single "generic fetcher" would be more fragile than helpful
— every fetch script still owns its own request/parsing logic, it just
stops owning its own copy of *which countries and indicators* to ask for.
"""
import os

import yaml

REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "sources.yaml")


def load_registry(path=REGISTRY_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_source(source_id, path=REGISTRY_PATH):
    """Return the registry entry for a given source id, resolving a
    `countries: <group_name>` reference into the actual list of country
    dicts so callers never need to know the group indirection exists."""
    registry = load_registry(path)
    for src in registry.get("sources", []):
        if src.get("id") == source_id:
            src = dict(src)  # shallow copy — don't mutate the cached parse
            countries_ref = src.get("countries")
            if isinstance(countries_ref, str):
                src["countries"] = get_country_group(countries_ref, path)
            return src
    available = [s.get("id") for s in registry.get("sources", [])]
    raise KeyError(f"No source '{source_id}' in registry. Available ids: {available}")


def get_country_group(group_name, path=REGISTRY_PATH):
    registry = load_registry(path)
    groups = registry.get("countries", {})
    if group_name not in groups:
        raise KeyError(f"No country group '{group_name}' in registry. Available groups: {list(groups)}")
    return groups[group_name]


def get_computation(computation_id, path=REGISTRY_PATH):
    """Same idea as get_source(), but looks in the `computations` list --
    derived/computed steps that read already-ingested data rather than
    calling an external API. Resolves `min_countries_required: <group_name>`
    the same way get_source() resolves `countries:`."""
    registry = load_registry(path)
    for comp in registry.get("computations", []):
        if comp.get("id") == computation_id:
            comp = dict(comp)
            countries_ref = comp.get("min_countries_required")
            if isinstance(countries_ref, str):
                comp["min_countries_required"] = get_country_group(countries_ref, path)
            return comp
    available = [c.get("id") for c in registry.get("computations", [])]
    raise KeyError(f"No computation '{computation_id}' in registry. Available ids: {available}")
