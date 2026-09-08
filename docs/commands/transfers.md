# Transfers

A waiting list for people moving between clubs. Instead of posting a name, trainer ID and "Turfcore to Horsecore" in a channel and pinging a mod, a member queues up with a command and the club's leaders work through the list.

A request is on the waiting list for exactly as long as nobody has decided it. Approving or declining removes it from the queue and DMs the requester.

---

## /transfer_request

Queue for a spot in another club. **Requires a linked trainer** — run `/link_trainer` once first.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | The club you want to transfer into |
| `note` | No | Anything the club's leaders should know |

Your trainer name, trainer ID and current club all come from your link, so there is nothing to type and nothing to get wrong. A retyped ID is the one thing in this flow nobody can check: a leader who sends an invite to a wrong digit gets silence back, which looks exactly like the person ignoring it.

The reply is private and shows your position in the queue. Running the command again for the same club edits the request you already have rather than putting you in the queue twice, and does not cost you your place.

You cannot queue for a club you are already an active member of. Rejoining a club you previously left is fine.

---

## /my_transfers

See your own pending requests and how far up each queue you are, or withdraw one.

No parameters.

---

## /transfer_queue

See who is waiting to join a club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | The club whose queue you want to see |

Anyone can read the queue. If you can manage the club, the same reply carries a panel: pick a request, then **Approve** or **Decline**. Declining asks for an optional reason, which is passed on to the requester.

Positions are order of arrival, not a rule — you can approve anyone on the list, in any order.

---

## /post_transfer_info

Post the how-to-request guide in a channel, ready to pin. Admin or server manager role only.

| Parameter | Required | Description |
|---|---|---|
| `channel` | No | Where to post it (defaults to the channel you run it in) |

The guide walks through linking, requesting and waiting for the DM, in that order — `/transfer_request` is refused until a trainer is linked, so a guide that led with it would teach the failing order. It also lists the clubs in the server that can be requested.

It is **not** pinned automatically. Pinning needs Manage Messages, which the bot otherwise never asks for, and a guide that quietly failed to pin would be worse than one you pin by hand — so right-click the message and pin it yourself.

The guide is a snapshot, not a live board. Run the command again to post an up-to-date one if your club list changes.

---

## /set_transfer_channel

Announce new transfer requests in a channel. Admin or club editor only.

| Parameter | Required | Description |
|---|---|---|
| `channel` | Yes | Channel for the announcements |
| `club` | Yes | The club |

Optional, but this is what makes requests visible: with no transfer channel set, nothing is posted when someone queues up, and the queue is only seen by whoever runs `/transfer_queue` or opens the dashboard.

Announcements carry no approve/decline buttons on purpose. A club taking a dozen requests a week would otherwise end up with a dozen live control panels in one channel, where the one that gets clicked is whichever scrolled past most recently rather than whichever is next. Each announcement is rewritten to show the outcome once a decision is made.

---

## What the requester is told

| Outcome | DM |
|---|---|
| Approved | Their request was approved, and to check their in-game notifications for the new invite |
| Declined | Their request was not accepted, with the reason if one was given |

DMs are sent to the Discord account that ran `/transfer_request`, which is the same account the trainer link belongs to. A member with DMs closed simply is not reached; the decision still stands and the queue still updates.

Because the queue reads from `/link_trainer`, it only covers trainers UmaCore already tracks. Someone joining from a club the bot does not watch has no member record to link to and cannot queue — add them with `/add_member` first, or handle that arrival by hand.

---

## On the dashboard

The **Transfers** page shows the same queue for the active club, with how long each person has been waiting, and approves or declines with the same effect. Decisions from either surface settle the request exactly once, so two leaders working at the same time cannot send one person two different answers.
