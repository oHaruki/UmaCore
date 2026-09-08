"""
Transfer request queue.

Someone asks for a spot in a club, the club's leaders approve or reject it, and
the requester is told which happened. The row is the waiting list: a request is
"in the queue" for exactly as long as its status is ``pending``, so a decision
removes it from every queue view without deleting the history of it.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from uuid import UUID
import logging

from config.database import db

logger = logging.getLogger(__name__)

PENDING = 'pending'
APPROVED = 'approved'
REJECTED = 'rejected'
CANCELLED = 'cancelled'

DECIDED = (APPROVED, REJECTED, CANCELLED)

_COLUMNS = """
    request_id, to_club_id, from_club_id, from_club_name, discord_user_id,
    discord_name, trainer_name, trainer_id, note, status, decided_by_name,
    decided_at, decision_note, announce_channel_id, announce_message_id,
    created_at, updated_at
"""


@dataclass
class TransferRequest:
    """One person's request to move into one club."""
    request_id: UUID
    to_club_id: UUID
    from_club_id: Optional[UUID]
    from_club_name: Optional[str]
    discord_user_id: int
    discord_name: str
    trainer_name: str
    trainer_id: Optional[str]
    note: Optional[str]
    status: str
    decided_by_name: Optional[str]
    decided_at: Optional[datetime]
    decision_note: Optional[str]
    announce_channel_id: Optional[int]
    announce_message_id: Optional[int]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @property
    def origin(self) -> str:
        """Where they are coming from, in one phrase."""
        return self.from_club_name or "elsewhere"

    @classmethod
    async def submit(cls, *, to_club_id: UUID, discord_user_id: int, discord_name: str,
                     trainer_name: str, trainer_id: Optional[str] = None,
                     from_club_id: Optional[UUID] = None,
                     from_club_name: Optional[str] = None,
                     note: Optional[str] = None) -> 'TransferRequest':
        """Queue a request, or correct the one already queued.

        Running the command a second time updates the existing pending row rather
        than adding a second place in the same queue. ``created_at`` is
        deliberately left alone, so fixing a typo in a trainer ID does not cost
        someone the place they have been waiting in.
        """
        query = f"""
            INSERT INTO transfer_requests
                (to_club_id, from_club_id, from_club_name, discord_user_id,
                 discord_name, trainer_name, trainer_id, note)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (to_club_id, discord_user_id) WHERE status = 'pending'
            DO UPDATE SET
                from_club_id   = EXCLUDED.from_club_id,
                from_club_name = EXCLUDED.from_club_name,
                discord_name   = EXCLUDED.discord_name,
                trainer_name   = EXCLUDED.trainer_name,
                trainer_id     = EXCLUDED.trainer_id,
                note           = EXCLUDED.note,
                updated_at     = NOW()
            RETURNING {_COLUMNS}
        """
        row = await db.fetchrow(query, to_club_id, from_club_id, from_club_name,
                                discord_user_id, discord_name, trainer_name,
                                trainer_id, note)
        logger.info(
            f"Transfer request queued: {trainer_name} ({discord_user_id}) -> club {to_club_id}"
        )
        return cls(**dict(row))

    @classmethod
    async def get_by_id(cls, request_id: UUID) -> Optional['TransferRequest']:
        row = await db.fetchrow(
            f"SELECT {_COLUMNS} FROM transfer_requests WHERE request_id = $1",
            request_id,
        )
        return cls(**dict(row)) if row else None

    @classmethod
    async def get_queue(cls, club_id: UUID) -> List['TransferRequest']:
        """The waiting list for a club, oldest request first.

        Order is presentation, not policy: a leader may approve anyone on this
        list regardless of where they sit in it.
        """
        rows = await db.fetch(
            f"""SELECT {_COLUMNS} FROM transfer_requests
                WHERE to_club_id = $1 AND status = 'pending'
                ORDER BY created_at ASC""",
            club_id,
        )
        return [cls(**dict(r)) for r in rows]

    @classmethod
    async def get_pending_for_user(cls, discord_user_id: int) -> List['TransferRequest']:
        rows = await db.fetch(
            f"""SELECT {_COLUMNS} FROM transfer_requests
                WHERE discord_user_id = $1 AND status = 'pending'
                ORDER BY created_at ASC""",
            discord_user_id,
        )
        return [cls(**dict(r)) for r in rows]

    async def position(self) -> int:
        """This request's 1-based place in its club's queue."""
        return await db.fetchval(
            """SELECT COUNT(*) + 1 FROM transfer_requests
               WHERE to_club_id = $1 AND status = 'pending' AND created_at < $2""",
            self.to_club_id, self.created_at,
        )

    @classmethod
    async def decide(cls, request_id: UUID, status: str, decided_by_name: str,
                     decision_note: Optional[str] = None) -> Optional['TransferRequest']:
        """Settle a request. Returns None if it was already decided.

        The ``status = 'pending'`` guard is what makes a decision idempotent
        across surfaces: a leader clicking approve on the dashboard while another
        approves from Discord produces one decision and one DM, not two.
        """
        if status not in DECIDED:
            raise ValueError(f"Invalid transfer status: {status}")

        row = await db.fetchrow(
            f"""UPDATE transfer_requests
                SET status = $2, decided_by_name = $3, decision_note = $4,
                    decided_at = NOW(), updated_at = NOW()
                WHERE request_id = $1 AND status = 'pending'
                RETURNING {_COLUMNS}""",
            request_id, status, decided_by_name, decision_note,
        )
        if not row:
            return None
        logger.info(f"Transfer request {request_id} {status} by {decided_by_name}")
        return cls(**dict(row))

    async def set_announcement(self, channel_id: int, message_id: int) -> None:
        """Remember the announcement post, so a decision can edit it."""
        await db.execute(
            "UPDATE transfer_requests SET announce_channel_id = $2, announce_message_id = $3 "
            "WHERE request_id = $1",
            self.request_id, channel_id, message_id,
        )
        self.announce_channel_id = channel_id
        self.announce_message_id = message_id
