"""Hypothesis profiles for the suite: the same examples every run by default, exploration on request.

default   Derandomized and without an example database. A passing suite means the same thing each
          time, and a failure reproduces by running the test again. Budgets are small enough for
          every run of the suite.
explore   Fresh random examples, ten times as many, and the local .hypothesis database, which
          replays a failure until it is fixed. Select it with BERMAN_HYPOTHESIS_PROFILE=explore.

A failure prints the falsifying example, and for sequences the steps that reach it. Pin one worth
keeping as an explicit @example or a plain unittest case beside the property.

These tests use synthetic records, disposable workspaces and simulated callbacks. They establish
nothing about a native installation.
"""

import os

from hypothesis import HealthCheck, settings

QUIET = [HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much]
settings.register_profile(
    'default', derandomize=True, database=None, deadline=None, print_blob=True, suppress_health_check=QUIET
)
settings.register_profile('explore', derandomize=False, deadline=None, print_blob=True, suppress_health_check=QUIET)
PROFILE = os.environ.get('BERMAN_HYPOTHESIS_PROFILE', 'default')
settings.load_profile(PROFILE)
SCALE = 10 if PROFILE == 'explore' else 1


def budget(examples, steps=None):
    """Settings for one test: its example count under the default profile, scaled when exploring."""
    limits = {'max_examples': examples * SCALE}
    if steps is not None:
        limits['stateful_step_count'] = steps * (2 if SCALE > 1 else 1)
    return settings(**limits)
