"""
Fitness-Complexity algorithm (Tacchella, Cristelli & Pietronero, 2012) for
estimating a proxy Economic Complexity from a binary country-product
specialisation matrix.

Why Fitness-Complexity rather than the more commonly-cited Method of
Reflections (Hidalgo & Hausmann, 2009, the algorithm the Atlas of Economic
Complexity itself is built on): Method of Reflections relies on eigenvector
decomposition of the country-country correlation structure, which needs a
reasonably large number of countries to be numerically stable. With only
5 countries (the SACU set -- see compute_proxy_eci.py for why), that
decomposition is close to degenerate. Fitness-Complexity is the standard
recommended alternative for exactly this small-N situation: it's a
non-linear iterative algorithm, not an eigendecomposition, so it stays
well-behaved with few countries. This is exactly the kind of caveat that
belongs in the data itself, not just in a design conversation -- see the
`iterations_to_converge` and `country_count` fields in the output CSV.

This module has no dependency on pandas, CSV formats, or the registry --
it takes a plain binary numpy matrix and returns fitness/complexity scores,
so it can be unit-tested against hand-computable cases independent of any
real trade data.
"""
import numpy as np


def fitness_complexity(matrix, max_iterations=200, tolerance=1e-9, floor=1e-12):
    """
    matrix: 2D numpy array, shape (n_countries, n_products), binary (0/1),
        where matrix[c, p] == 1 means country c has revealed comparative
        advantage (RCA >= 1) in product p.

    Returns a dict with:
        fitness: 1D array, length n_countries -- higher = more "fit"/complex
        complexity: 1D array, length n_products -- higher = harder to produce
        iterations: how many iterations actually ran before convergence
        converged: bool
        zero_diversity_countries: indices of any country with no RCA>=1
            products at all (a real data problem, not a modelling artifact --
            the caller should investigate rather than trust the score for
            that country)
    """
    matrix = np.asarray(matrix, dtype=float)
    n_countries, n_products = matrix.shape
    if n_countries == 0 or n_products == 0:
        raise ValueError("matrix must have at least one country and one product")

    diversity = matrix.sum(axis=1)
    zero_diversity_countries = np.where(diversity == 0)[0].tolist()

    fitness = np.ones(n_countries)
    complexity = np.ones(n_products)

    iterations_run = 0
    converged = False
    for iteration in range(1, max_iterations + 1):
        iterations_run = iteration
        prev_fitness = fitness.copy()

        fitness_raw = matrix @ complexity  # sum_p M[c,p] * Q[p]

        inv_fitness = 1.0 / np.maximum(prev_fitness, floor)
        denom = matrix.T @ inv_fitness  # sum_c M[c,p] / F[c]
        denom_safe = np.where(denom <= floor, np.inf, denom)
        complexity_raw = 1.0 / denom_safe

        fitness_mean = fitness_raw.mean()
        complexity_mean = complexity_raw.mean()
        fitness = fitness_raw / fitness_mean if fitness_mean > floor else fitness_raw
        complexity = complexity_raw / complexity_mean if complexity_mean > floor else complexity_raw

        max_change = np.max(np.abs(fitness - prev_fitness))
        if max_change < tolerance:
            converged = True
            break

    return {
        "fitness": fitness,
        "complexity": complexity,
        "iterations": iterations_run,
        "converged": converged,
        "zero_diversity_countries": zero_diversity_countries,
    }
