# Transfers

A waiting list for people moving into a club — between the server's own clubs, or in from anywhere else. Instead of posting a name, trainer ID and "Turfcore to Horsecore" in a channel and pinging a mod, people queue up with a command and the club's leaders work through the list.

A request is on the waiting list for exactly as long as nobody has decided it. Approving or declining removes it from the queue and DMs the requester.

---

## /transfer_request

Queue for a spot in a club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | The club you want to transfer into |
| `trainer_id` | Only from outside | Your in-game trainer ID, if you are not in one of the server's clubs |
| `note` | No | Anything the club's leaders should know |

**If you are in one of the server's clubs**, link your trainer once with `/link_trainer` and leave `trainer_id` out. Your trainer name, trainer ID and current club all come from the link, so there is nothing to type and nothing to get wrong.

**If you are coming from somewhere else**, there is no member record to link to, so give your trainer ID instead — the number on your in-game profile, spaces and all. The bot looks it up on uma.moe and queues the name uma.moe holds for that ID, plus the club uma.moe says you are in as where you are coming from. The name is never typed: a retyped ID is the one thing in this flow nobody can otherwise check, and a leader who sends an invite to a wrong digit gets silence back, which looks exactly like the person ignoring it. With the lookup, a wrong digit either fails outright or shows a stranger's name on your confirmation.

| What happens | Why |
|---|---|
| uma.moe has no trainer with that ID | Refused — check the ID against your profile |
| uma.moe can't be reached | Refused with "try again in a few minutes"; your ID is not called wrong |
| The ID belongs to an active member of one of the server's clubs | Sent to `/link_trainer` instead, so the club they are leaving is recorded |
| You are linked and type a *different* ID | Refused rather than quietly queueing your linked trainer — `/unlink` first if the link is wrong |

The reply is private and shows your position in the queue. Running the command again for the same club edits the request you already have rather than putting you in the queue twice, and does not cost you your place.

You cannot queue for a club you are already an active member of. Rejoining a club you previously left is fine, by link or by ID.

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

The guide walks through linking, requesting and waiting for the DM, in that order — a member's `/transfer_request` is refused until their trainer is linked, so a guide that led with it would teach the failing order. The request step also tells people from outside the server's clubs to add `trainer_id:`. It lists the clubs in the server that can be requested.

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

DMs are sent to the Discord account that ran `/transfer_request` — for a member, the same account the trainer link belongs to. Someone with DMs closed simply is not reached; the decision still stands and the queue still updates.

Someone from outside shows up in the queue with the club uma.moe has them in, or "elsewhere" if they are in none. Approving them does not create a member record: they appear on the roster once they have joined in-game and the next scrape picks them up, and can `/link_trainer` from then on.

---

## On the dashboard

The **Transfers** page shows the same queue for the active club, with how long each person has been waiting, and approves or declines with the same effect. Decisions from either surface settle the request exactly once, so two leaders working at the same time cannot send one person two different answers.
