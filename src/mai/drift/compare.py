def subsystem_of(path: str, depth: int = 3) -> str:
    """Group a file path by its directory, truncated to `depth` segments."""
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    return "/".join(parts[:-1][:depth])


def _blank() -> dict:
    return {"shared": 0, "diverged": 0, "identical": 0, "only_a": 0, "only_b": 0}


def compare_trees(tree_a: dict[str, str], tree_b: dict[str, str],
                  depth: int = 3) -> dict[str, dict]:
    """Per-subsystem counts of identical/diverged/unique files between two trees.

    Trees map path -> git blob SHA; equal SHA means byte-identical content.
    """
    stats: dict[str, dict] = {}
    for path in set(tree_a) | set(tree_b):
        bucket = stats.setdefault(subsystem_of(path, depth), _blank())
        in_a, in_b = path in tree_a, path in tree_b
        if in_a and in_b:
            bucket["shared"] += 1
            if tree_a[path] == tree_b[path]:
                bucket["identical"] += 1
            else:
                bucket["diverged"] += 1
        elif in_a:
            bucket["only_a"] += 1
        else:
            bucket["only_b"] += 1
    return stats
