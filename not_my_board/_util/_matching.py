# pylint: disable=invalid-name
import collections

INFINITY = float("inf")


def find_matching(G):
    """Find the most pairs in a bipartite graph.

    The problem is better known as maximum cardinality matching. The bipartite
    graph `G` is described as a Mapping, where the keys are the vertices of one
    set (U) and the values are a Sequence of vertices of the other set (V),
    which describe the edges (E) of the graph. For example the graph with
    U=(U0, U1), V=(V0, V1) and E=((U0, V0), (U0, V1), (U1, V0)) can be written
    as:

        G = {
            'U0': ['V0', 'V1'],
            'U1': ['V0'],
        }

    The return value is a maximum matching M, described as a Mapping from all
    matched vertices in U to their matched vertex in V. For the example above,
    the return value would be:

        M = {
            'U0': 'V1',
            'U1': 'V0',
        }
    """
    # This is an implementation of the Hopcroft-Karp algorithm.

    # M is the current matching. It starts as a partial matching and is updated
    # until it is a maximum matching.
    M = {}
    M_reverse = {}
    layer = {}

    # This breadth-first search finds the shortest augmenting paths. An
    # augmenting path is a special path with the following rules:
    # - the path starts at a free vertex in U
    # - the path can only traverse unmatched edges from U to V
    # - the path can only traverse matched edges from V to U
    # - the path ends at a free vertex in V
    # The search saves the layer of each vertex in U, at which it was
    # encountered in the search, to guide the following depth-first search.
    def breadth_first_search():
        queue = collections.deque()

        # find free vertices in U to use as starting points
        for u in G:
            if u in M:
                layer[u] = INFINITY
            else:
                layer[u] = 0
                queue.append(u)
        layer[None] = INFINITY

        def is_shortest_path(u):
            return layer[u] < layer[None]

        while queue:
            u = queue.popleft()
            if is_shortest_path(u):
                for v in G[u]:
                    # Go from v to u over a matched edge. next_u is None, if v
                    # is free.
                    next_u = M_reverse.get(v)
                    if layer[next_u] is INFINITY:  # if not visited, yet
                        layer[next_u] = layer[u] + 1
                        queue.append(next_u)
        return layer[None] is not INFINITY  # did we reach a free v?

    # This depth-first search is guided by the layers found in the
    # breadth-first search to find the shortest augmenting paths and update the
    # matching M along the way. All previously matched edges in the path are
    # replaced by the unmatched edges in the path. Since the augmenting paths
    # start and end at a free vertex, every found path increases the number of
    # pairs by one.
    def depth_first_search(u):
        for v in G[u]:
            # Go from v to u over a matched edge. next_u is None, if v is free.
            next_u = M_reverse.get(v)
            if layer[next_u] == layer[u] + 1:
                if next_u is None or depth_first_search(next_u):
                    M[u], M_reverse[v] = v, u
                    return True

        # No path found for this u. Mark it, to not try again.
        layer[u] = INFINITY
        return False

    while breadth_first_search():
        # At least one augmenting path was found. Start a depth-first search at
        # every free vertex in U.
        for u in G:
            if u not in M:
                depth_first_search(u)

    return M


def _main():
    G = {
        "U0": ["V0", "V1"],
        "U1": ["V0", "V4"],
        "U2": ["V2", "V3"],
        "U3": ["V0", "V4"],
        "U4": ["V1", "V3"],
    }

    M = find_matching(G)
    for k, v in M.items():
        print(f"{k} -> {v}")


if __name__ == "__main__":
    _main()
