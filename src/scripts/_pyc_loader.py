"""Custom import hook to load .pyc files from __pycache__ when source .py is missing."""
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys

class PycFinder(importlib.abc.MetaPathFinder):
    """Find modules by looking for .pyc files in __pycache__ directories."""

    def __init__(self, base_dir):
        self.base_dir = os.path.abspath(base_dir)
        self._cache_tag = sys.implementation.cache_tag  # e.g. 'cpython-312'

    def find_spec(self, fullname, path, target=None):
        parts = fullname.split('.')

        # Build candidate paths
        if path is None:
            search_dirs = [self.base_dir]
        else:
            search_dirs = list(path)

        for search_dir in search_dirs:
            # Normalize the search dir to be under base_dir
            if not os.path.isabs(search_dir):
                search_dir = os.path.join(self.base_dir, search_dir)

            name = parts[-1]

            # Check for package (__init__)
            pkg_pycache = os.path.join(search_dir, name, '__pycache__')
            init_pyc = os.path.join(pkg_pycache, f'__init__.{self._cache_tag}.pyc')
            if os.path.isfile(init_pyc):
                # It's a package
                pkg_dir = os.path.join(search_dir, name)
                return importlib.util.spec_from_file_location(
                    fullname,
                    init_pyc,
                    loader=importlib.machinery.SourcelessFileLoader(fullname, init_pyc),
                    submodule_search_locations=[pkg_dir],
                )

            # Check for module
            mod_pycache = os.path.join(search_dir, '__pycache__')
            mod_pyc = os.path.join(mod_pycache, f'{name}.{self._cache_tag}.pyc')
            if os.path.isfile(mod_pyc):
                return importlib.util.spec_from_file_location(
                    fullname,
                    mod_pyc,
                    loader=importlib.machinery.SourcelessFileLoader(fullname, mod_pyc),
                )

        return None


def install(base_dir=None):
    """Install the PycFinder as a meta path finder."""
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    finder = PycFinder(base_dir)
    # Insert at position 0 to take priority
    sys.meta_path.insert(0, finder)
    return finder
