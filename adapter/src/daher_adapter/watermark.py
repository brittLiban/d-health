"""The durable poll position (DH-011E, ADR-015).

Two rules carry all the safety here, and both are about what the watermark must *refuse* to do:

1. **Bootstrap exactly once, from the server's clock.** ADR-015 decided there is no historical
   backfill: on first start the position is the deployment instant, read from the OpenMRS server
   rather than the adapter container. Re-deriving "now" on a later boot would skip every order
   placed while the adapter was down, which is why the bootstrap value is persisted and then
   never recomputed.

2. **Never move backwards.** A regression would re-present orders the ledger has already
   completed — harmless in itself, the ledger absorbs them — but it would also mask a real bug
   by making a stuck watermark look like it was working. Regression is refused loudly instead.

The accepted consequence of rule 1, recorded so nobody reads it as an oversight: an order placed
before deployment never reaches the laboratory, **including one still active**, and nothing
alerts on its absence. It has to be re-placed by a clinician.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.instants import ServerInstant, parse_instant
from daher_adapter.ledger import Direction
from daher_adapter.models import Watermark

logger = logging.getLogger("daher_adapter.watermark")


class WatermarkRegression(RuntimeError):
    """Something tried to move a watermark backwards."""


class WatermarkRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get_or_bootstrap(
        self,
        *,
        resource_type: str,
        direction: Direction,
        server_now: ServerInstant,
    ) -> ServerInstant:
        """Return the persisted position, creating it from `server_now` only on first ever call.

        `server_now` must be a value the **server** emitted (the bundle's own `meta.lastUpdated`
        is one), never `datetime.now()` here. The entire watermark contract is comparison against
        server-issued timestamps, and a few seconds of skew in the wrong direction drops orders.
        """
        existing = self._load(resource_type=resource_type, direction=direction)
        if existing is not None:
            return existing

        row = Watermark(
            direction=direction,
            resource_type=resource_type,
            watermark_raw=server_now.raw,
            bootstrapped_raw=server_now.raw,
        )
        with self._sessions() as session:
            try:
                session.add(row)
                session.commit()
            except IntegrityError:
                # Another process bootstrapped the same stream first. Its value wins; ours is
                # discarded rather than overwriting a position that may already have advanced.
                session.rollback()
                concurrent = self._load(resource_type=resource_type, direction=direction)
                if concurrent is None:  # pragma: no cover - unique violation implies a row
                    raise
                return concurrent

        logger.info(
            "watermark_bootstrapped",
            extra={
                "resource_type": resource_type,
                "direction": direction,
                # The instant is operational metadata, not clinical data.
                "bootstrapped_at": server_now.raw,
            },
        )
        return server_now

    def current(self, *, resource_type: str, direction: Direction) -> ServerInstant | None:
        return self._load(resource_type=resource_type, direction=direction)

    def bootstrapped_at(self, *, resource_type: str, direction: Direction) -> ServerInstant | None:
        """The first position ever persisted. §8's reconciliation counts only from here."""
        with self._sessions() as session:
            raw = session.scalar(
                select(Watermark.bootstrapped_raw).where(
                    Watermark.direction == direction,
                    Watermark.resource_type == resource_type,
                )
            )
        return parse_instant(raw) if raw is not None else None

    def advance(
        self,
        *,
        resource_type: str,
        direction: Direction,
        to: ServerInstant,
    ) -> ServerInstant:
        """Move the position forward. Callers must already have processed the whole cohort.

        Refuses to move backwards, and treats "no change" as success so a cycle that found
        nothing new is not an error.
        """
        with self._sessions.begin() as session:
            row = session.scalar(
                select(Watermark)
                .where(
                    Watermark.direction == direction,
                    Watermark.resource_type == resource_type,
                )
                .with_for_update()
            )
            if row is None:
                raise WatermarkRegression(
                    f"no watermark exists for {direction}/{resource_type}; bootstrap it first"
                )
            persisted = parse_instant(row.watermark_raw)
            if to < persisted:
                raise WatermarkRegression(
                    f"refusing to move the {direction}/{resource_type} watermark backwards from "
                    f"{row.watermark_raw!r} to {to.raw!r}. A regression re-presents completed "
                    f"work and disguises a stalled poller as a working one."
                )
            row.watermark_raw = to.raw
        return to

    def _load(self, *, resource_type: str, direction: Direction) -> ServerInstant | None:
        with self._sessions() as session:
            raw = session.scalar(
                select(Watermark.watermark_raw).where(
                    Watermark.direction == direction,
                    Watermark.resource_type == resource_type,
                )
            )
        return parse_instant(raw) if raw is not None else None
