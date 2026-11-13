"""Installed requirement facts and compatibility, without loading a study or presentation.

Only this module imports packaging, lazily so verification can diagnose its own damaged
installation. Requirements are evaluated in this interpreter, including requested extras;
unrequested extras and false environment markers contribute no dependencies.
"""

from importlib import metadata
import platform


def inspect():
    """Return (installed versions, problems) for the active runtime dependency closure.

    Each dependency edge is checked, even if another edge already reached that distribution.
    Cycles terminate, and extras requested along different edges are all visited. Importability
    is a separate check: metadata alone establishes neither intact files nor usable APIs.
    """
    try:
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import canonicalize_name
    except Exception as exc:
        raise ValueError(
            f'Dependencies cannot be checked: packaging does not import ({type(exc).__name__}: {exc}). '
            'Reinstall Berman with its dependencies'
        ) from exc

    installed, problems, visited = {}, [], set()
    pending = [('berman', frozenset())]
    while pending:
        name, extras = pending.pop()
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            installed[name] = None
            continue
        if name != 'berman':
            installed[name] = distribution.version
        python = distribution.metadata.get('Requires-Python')
        if python and not SpecifierSet(python).contains(platform.python_version(), prereleases=True):
            problems.append(f'{name} requires Python {python}; running {platform.python_version()}')
        for raw in distribution.requires or []:
            requirement = Requirement(raw)
            if requirement.marker and not any(requirement.marker.evaluate({'extra': extra}) for extra in extras | {''}):
                continue
            child = canonicalize_name(requirement.name)
            pending.append((child, frozenset(requirement.extras)))
            try:
                version = metadata.version(child)
            except metadata.PackageNotFoundError:
                continue  # The pending visit records absence once, independently of its parents.
            if not requirement.specifier.contains(version, prereleases=True):
                problems.append(f'{name} requires {requirement}; installed {child} {version}')
    return dict(sorted(installed.items())), sorted(set(problems))
