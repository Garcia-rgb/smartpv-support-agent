"""Use SQLAlchemy's bundled Python implementation in desktop processes.

The Windows loader can stall while loading optional Cython extensions. The
library ships equivalent Python modules; the offline package already uses them.
This finder makes source launches behave the same way without editing dependencies.
"""

import importlib.abc
import importlib.util
import sys
from pathlib import Path


class SQLAlchemyPythonFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith("sqlalchemy.") or not fullname.endswith("_cy") or not path:
            return None
        filename = fullname.rsplit(".", 1)[-1] + ".py"
        for directory in path:
            source = Path(directory) / filename
            if source.is_file():
                return importlib.util.spec_from_file_location(fullname, source)
        return None


def prefer_python_sqlalchemy():
    if not any(isinstance(finder, SQLAlchemyPythonFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, SQLAlchemyPythonFinder())
