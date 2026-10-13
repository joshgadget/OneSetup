import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def params_with(**overrides):
    """A copy of the shipped parameter set with dotted keys overridden.

    Tests use this to exercise the alternatives the spec says to keep as separate
    variants, without changing the file that ships.
    """
    import yaml
    from engine.config import Params, load_params

    base = load_params()
    raw = yaml.safe_load(base.source_path.read_bytes())
    for dotted, value in overrides.items():
        node = raw
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    return Params(raw, base.source_path, base.file_hash)
