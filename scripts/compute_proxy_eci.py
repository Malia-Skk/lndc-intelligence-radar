"""
Phase 2 — proxy Economic Complexity for Lesotho and SACU peers.

Lesotho has no official score in the Atlas of Economic Complexity (export
data-quality issues -- see the strategy's own Ch.1.4/2.7). Rather than try
to replicate a globally-comparable ECI (which was attempted and deferred --
see comtrade_world_basket's status in config/sources.yaml -- because a
correct global denominator means trusting ~200 historically-inconsistent
Comtrade reporter codes with no way to verify them from this environment),
this computes something smaller, honest, and directly useful: a
SACU-REGIONAL proxy, benchmarking Lesotho against Botswana, Eswatini,
Namibia and South Africa specifically. That's also a better fit for the
strategy's actual target -- Outcome 5 says "#1 in SACU," not "#1 globally."

Method, in order:
  1. Take each SACU country's export basket at the HS 2-digit chapter level
     (already ingested by comtrade_sacu_basket).
  2. Compute a REGIONAL Revealed Comparative Advantage (RCA) per
     country/product/year:

         RCA[c,p] = (X[c,p] / X[c]) / (X[SACU,p] / X[SACU])

     where X[c,p] is country c's exports of product p, X[c] is country c's
     total exports (all products), and X[SACU,*] is the same summed across
     all 5 SACU countries. RCA >= 1 means country c is more specialised in
     product p than the SACU region is on average.
  3. Binarise: M[c,p] = 1 if RCA[c,p] >= 1, else 0.
  4. Run the Fitness-Complexity algorithm (lib/complexity.py) on M to get a
     Fitness score per country and a Complexity score per product.
  5. Rank the 5 countries by Fitness within each year -- this rank is what
     "#1 in SACU" (Outcome 5) can actually be tracked against.

A year is skipped entirely if any SACU country is missing export data for
it (see min_countries_required in the registry) -- comparing Lesotho against
a SACU "average" that's silently missing a competitor for that year would
distort the ranking, not just be less precise.

This OVERWRITES its output completely on every run (unlike the fetch
scripts' append-and-dedup pattern) -- see config/sources.yaml's
"PHASE 2 — DERIVED COMPUTATIONS" section for why.
"""
import datetime
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from lib.complexity import fitness_complexity  # noqa: E402
from lib.registry import get_computation, get_source  # noqa: E402

COMP = get_computation("proxy_eci")
SACU_SOURCE = get_source("comtrade_sacu_basket")

INPUT_PATH = os.path.join(os.path.dirname(__file__), "..", SACU_SOURCE["output"])
ECI_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", COMP["outputs"][0])
TOP_PRODUCTS_OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", COMP["outputs"][1])

REQUIRED_COUNTRIES = {c["name"] for c in COMP["min_countries_required"]}
COUNTRY_CODE_LOOKUP = {c["name"]: c["iso3"] for c in COMP["min_countries_required"]}

RCA_THRESHOLD = 1.0
TOP_N_PRODUCTS = 5


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_export_basket(path):
    if not os.path.exists(path):
        raise SystemExit(
            f"{path} does not exist yet -- run the comtrade_sacu_basket fetch "
            "at least once before this computation."
        )
    df = pd.read_csv(path)
    df = df[df["flow"] == "Export"].copy()
    df["trade_value_usd"] = pd.to_numeric(df["trade_value_usd"], errors="coerce")
    df = df.dropna(subset=["trade_value_usd"])
    return df


def usable_years(df):
    """A year is usable only if every required SACU country reported at
    least some export data for it -- see the module docstring for why a
    partially-reported year is excluded rather than computed anyway."""
    coverage = df.groupby("year")["reporter"].apply(set)
    usable, skipped = [], []
    for year, reporters in coverage.items():
        missing = REQUIRED_COUNTRIES - reporters
        if missing:
            skipped.append((year, sorted(missing)))
        else:
            usable.append(year)
    return sorted(usable), skipped


def build_matrices(df, year):
    """Returns (countries, products, value_matrix) for one year, as a
    countries x products numpy array of total export value, aligned to
    sorted country/product lists so array order is stable and inspectable."""
    year_df = df[df["year"] == year]
    pivot = year_df.pivot_table(
        index="reporter", columns="cmd_code", values="trade_value_usd",
        aggfunc="sum", fill_value=0.0,
    )
    pivot = pivot.reindex(sorted(REQUIRED_COUNTRIES))  # stable, explicit country order
    countries = list(pivot.index)
    products = list(pivot.columns)
    return countries, products, pivot.to_numpy()


def compute_rca(value_matrix):
    """Regional RCA -- see module docstring for the formula. Returns an
    (n_countries, n_products) array of RCA values."""
    country_totals = value_matrix.sum(axis=1, keepdims=True)  # X[c]
    region_total = value_matrix.sum()  # X[SACU]
    product_region_totals = value_matrix.sum(axis=0, keepdims=True)  # X[SACU, p]

    with np.errstate(divide="ignore", invalid="ignore"):
        country_share = value_matrix / country_totals  # X[c,p] / X[c]
        region_share = product_region_totals / region_total  # X[SACU,p] / X[SACU]
        rca = country_share / region_share

    rca = np.nan_to_num(rca, nan=0.0, posinf=0.0, neginf=0.0)
    return rca


def top_products_for_country(rca_row, products, cmd_desc_lookup, n=TOP_N_PRODUCTS):
    order = np.argsort(-rca_row)[:n]
    return [
        (products[i], cmd_desc_lookup.get(products[i], ""), round(float(rca_row[i]), 3))
        for i in order if rca_row[i] > 0
    ]


def main():
    df = load_export_basket(INPUT_PATH)
    cmd_desc_lookup = (
        df[["cmd_code", "cmd_desc"]].drop_duplicates("cmd_code").set_index("cmd_code")["cmd_desc"].to_dict()
    )

    years, skipped_years = usable_years(df)
    for year, missing in skipped_years:
        print(f"Skipping {year}: missing export data for {missing}.")

    if not years:
        raise SystemExit("No year has complete SACU coverage -- nothing to compute. Check the input data.")

    computed_at = utc_now_iso()
    source_pulled_at = df["pulled_at"].max() if "pulled_at" in df.columns else ""

    eci_rows = []
    top_product_rows = []

    for year in years:
        countries, products, value_matrix = build_matrices(df, year)
        rca = compute_rca(value_matrix)
        binary_matrix = (rca >= RCA_THRESHOLD).astype(float)
        diversity = binary_matrix.sum(axis=1)

        result = fitness_complexity(binary_matrix)
        fitness = result["fitness"]
        # Fitness-Complexity scores routinely span many orders of magnitude
        # once one economy (here, South Africa) dominates the group -- the
        # non-linear iteration amplifies the gap multiplicatively each round.
        # Ranking is unaffected (argsort works on any monotonic scale), but
        # reporting raw fitness at fixed decimal precision is misleading: it
        # rounds every country except the dominant one to a flat 0.000000,
        # which looks like a bug or "no signal" rather than the real,
        # legitimate result it is. log-Fitness is the standard fix in the
        # literature -- it compresses the scale into something plottable and
        # comparable, and is the value that should actually feed any future
        # trend chart, not raw fitness_score.
        log_fitness = np.log(np.maximum(fitness, 1e-300))

        if result["zero_diversity_countries"]:
            flagged = [countries[i] for i in result["zero_diversity_countries"]]
            print(f"  WARNING {year}: {flagged} have zero products with regional RCA >= 1 -- check their input data.")

        # rank 1 = highest fitness = most regionally complex
        order = np.argsort(-fitness)
        rank_by_country = {countries[idx]: rank + 1 for rank, idx in enumerate(order)}

        print(
            f"Year {year}: converged={result['converged']} in {result['iterations']} iterations. "
            f"Ranking (1=highest fitness): " + ", ".join(f"{c}={rank_by_country[c]}" for c in countries)
        )

        for i, country in enumerate(countries):
            eci_rows.append({
                "computed_at": computed_at,
                "source_data_pulled_at": source_pulled_at,
                "year": year,
                "country": country,
                "country_code": COUNTRY_CODE_LOOKUP.get(country, ""),
                "method": "fitness_complexity_v1",
                "scope": "sacu_regional_proxy",
                "fitness_score": f"{fitness[i]:.6e}",
                "log_fitness_score": round(float(log_fitness[i]), 4),
                "sacu_rank": rank_by_country[country],
                "num_countries_in_ranking": len(countries),
                "diversity_products_above_rca1": int(diversity[i]),
                "iterations_to_converge": result["iterations"],
                "converged": result["converged"],
            })

            for cmd_code, cmd_desc, rca_value in top_products_for_country(rca[i], products, cmd_desc_lookup):
                top_product_rows.append({
                    "computed_at": computed_at,
                    "year": year,
                    "country": country,
                    "country_code": COUNTRY_CODE_LOOKUP.get(country, ""),
                    "cmd_code": cmd_code,
                    "cmd_desc": cmd_desc,
                    "regional_rca": rca_value,
                })

    os.makedirs(os.path.dirname(ECI_OUTPUT_PATH), exist_ok=True)
    pd.DataFrame(eci_rows).to_csv(ECI_OUTPUT_PATH, index=False)
    pd.DataFrame(top_product_rows).to_csv(TOP_PRODUCTS_OUTPUT_PATH, index=False)

    print(f"\nWrote {len(eci_rows)} rows to {ECI_OUTPUT_PATH}")
    print(f"Wrote {len(top_product_rows)} rows to {TOP_PRODUCTS_OUTPUT_PATH}")

    lesotho_latest = [r for r in eci_rows if r["country"] == "Lesotho" and r["year"] == years[-1]]
    if lesotho_latest:
        r = lesotho_latest[0]
        print(f"\nLesotho, {years[-1]}: SACU rank {r['sacu_rank']} of {r['num_countries_in_ranking']} "
              f"(log-fitness={r['log_fitness_score']}, diversity={r['diversity_products_above_rca1']} products). "
              f"Note: compare log_fitness_score across countries/years, not the raw fitness_score column -- "
              f"raw Fitness routinely spans many orders of magnitude and a ratio of raw scores is not a "
              f"meaningful 'N times more complex' statement.")


if __name__ == "__main__":
    main()
