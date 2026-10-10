Overpass queries
================

The server answers a subset of [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL) at `/api/interpreter` (also `/overpass/api/interpreter`). Send the query as the `data` parameter of a GET or POST, or as the body of a POST:

	curl -d 'data=[out:json];nwr[amenity=pub](50.78,-1.10,50.80,-1.05);out center;' http://localhost:8000/api/interpreter

To use [Overpass Turbo](https://overpass-turbo.eu/), set its server to the address ending in `/api/`.

What is understood:

* Settings: `[out:xml]`, `[out:json]` or `[out:csv(...)]`, `[timeout:]`, `[bbox:south,west,north,east]`, and `[bbox]` with a separate `bbox=west,south,east,north` parameter. `[maxsize:]` is accepted and ignored.
* Queries for `node`, `way`, `rel`/`relation`, `nwr`, `nw`, `nr` and `wr`, with these filters:
    * tags: `[k]`, `[!k]`, `[k=v]`, `[k!=v]`, `[k~regex]` and `[k!~regex]` (add `,i` to ignore case);
    * place: a bounding box `(south,west,north,east)`, or `(around:metres,lat,lon)`, `(around:metres)` and `(around.set:metres)` for a distance from a position or from the elements of a set;
    * identity: `(id)` or `(id:a,b)`, and input sets `.name`;
    * links to a set: `(w)`, `(r)`, `(bn)`, `(bw)` and `(br)`, optionally with a set and a role as in `(r.routes:"stop")`;
    * last edit: `(uid:1,2)`, `(user:"name")`, `(newer:"2026-01-01T00:00:00Z")` and `(changed:"from","to")`.
* Named sets with `->.name`, unions `( ...; )`, differences `( a; - b; )`, a set as a statement `.name;`, and the recursion statements `>`, `>>`, `<` and `<<`.
* `out` with `ids`, `skel`, `body`, `tags` or `meta`; `geom`, `bb` or `center`; `count`; and a maximum number of elements. `qt` is accepted and ignored: output is in ID order.
* CSV output takes tag keys and the special fields `::id`, `::type`, `::otype`, `::lat`, `::lon`, `::version`, `::timestamp`, `::changeset`, `::uid`, `::user`, `::count`, `::count:nodes`, `::count:ways` and `::count:relations`, then optionally `;false` for no header line and a quoted separator in place of the tab.

Everything else in the language, such as areas, `foreach`, `if:` filters and `make`, is refused with a message naming the feature. Overpass XML queries are not read.

Differences from the main Overpass servers worth knowing:

* A way or relation is in a bounding box, or within a distance, if one of its nodes is (for a relation, a node of a member way counts). A way that only crosses the box, or passes near a position between two of its nodes, is not found. Likewise `around` a way or relation measures from its nodes.
* On a map whose `useBboxInQuery` setting is on, ways and relations are instead found in a bounding box by their own stored boxes: one indexed search, which also finds whatever has a box overlapping the area, including ways that cross it. Distances are still measured from nodes.
* A query must have something to search by: a bounding box or circles covering no more than `OVERPASS_AREA_MAXIMUM`, an ID, a set, or a filter of the form `[k]` or `[k=v]`. The other tag filters and the last edit filters alone would read the whole map and are refused.
* Finding every way in a bounding box takes about as long as downloading that area from `/api/0.6/map`. Queries with a `[k]` or `[k=v]` filter are much quicker.
* `(user:"name")` finds edits stored under that name, and edits by this server's account of that name. An `around` filter measures from at most 5000 positions.

The limits are set by `OVERPASS_TIMEOUT_DEFAULT`, `OVERPASS_TIMEOUT_MAXIMUM`, `OVERPASS_ELEMENTS_MAXIMUM`, `OVERPASS_RATE_LIMIT_REQUESTS` and `OVERPASS_RATE_LIMIT_WINDOW_SECONDS`, described in `pycrocosm/settings.py.template`. The older XAPI calls under `/overpass/xapi/` run on the same code.
