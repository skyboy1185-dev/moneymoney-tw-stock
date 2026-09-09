# Local official quote relay

`tools/quote_relay.py` forwards raw TWSE MIS responses from the user's Windows
computer to the cloud. It cannot place orders. Run it with `--config PATH`;
the JSON configuration contains `backend` and a random `token`.

The cloud stores only the token's SHA-256 digest in
`/app/data/quote-relay.sha256` on its persistent volume. Missing configuration
disables the endpoint. The credential authorizes only relay target retrieval
and quote submission. Never commit or log the token.

The cloud chooses the symbols, parses exchange timestamps and accepts regular
quotes no more than 15 seconds old. Repeated and older observations cannot
overwrite a newer publication. A missing last trade (`z = -`) does not make
the best bid or ask a real trade. A working transport therefore does not imply
adequate coverage or permission to trade.

On this Windows installation, the hidden relay is started by the current
user's `TWSE Official Quote Relay.lnk` Startup shortcut. It starts after login,
not before login. The computer must remain awake and connected. A file lock
prevents duplicate long-running instances. Network failures retry with bounded
backoff; outside weekday market hours the process waits.

Local configuration and rotating logs are in `.artifacts/quote-relay/`, excluded
from Git and restricted to Administrator and SYSTEM. `config.status.json`
contains the last cycle timestamp, accepted/rejected counts and errors without
credentials. Delete the Startup shortcut to disable future automatic launches;
stop the Python process whose command line points to `tools/quote_relay.py` to
stop the current relay. Do not stop unrelated Python processes.

Verification: relay authentication, unknown symbols, stale/future timestamps,
duplicate publication and regressing quotes are covered in
`backend/tests/test_quote_relay.py`. A production check must separately inspect
transport status and the robot's quote freshness/coverage; HTTP 200 alone is
not evidence that trading has recovered.
