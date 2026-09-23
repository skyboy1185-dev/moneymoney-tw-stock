# Free quote scheduling

Yahoo quote updates use two independent lanes, each targeting a five-second interval. Holdings and priority candidates use the priority lane. General stocks rotate in batches of at most 50, oldest attempted first. A slow general request does not block the priority lane.

The existing priority selector has a soft limit of 30 and preserves mandatory holdings beyond that limit. With 30 priority stocks and 270 general stocks, the general lane needs six batches (approximately 30 seconds when requests complete within the interval).

The local MIS relay receives priority flags from the backend and updates priority stocks plus one rotating general batch per cycle. Network latency and failure backoff can extend the target interval. No paid data subscription is enabled. Quote freshness, order-book freshness, spread limits and trading eligibility remain enforced.

Tests cover rotation, promotion, mandatory holdings above the soft limit, independent Yahoo lanes, cancellation and relay priority flags. Closed-market verification cannot establish next-session source availability or trading performance.
