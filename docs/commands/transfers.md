# Transfers

A waiting list for people moving between clubs. Instead of posting a name, trainer ID and "Turfcore to Horsecore" in a channel and pinging a mod, a member queues up with a command and the club's leaders work through the list.

A request is on the waiting list for exactly as long as nobody has decided it. Approving or declining removes it from the queue and DMs the requester.

---

## /transfer_request

Queue for a spot in another club.

| Parameter | Required | Description |
|---|---|---|
| `club` | Yes | The club you want to transfer into |
| `trainer_name` | Yes | Your in-game trainer name |
| `trainer_id` | Yes | Your in-game trainer ID |
| `from_club` | No | The club you're leaving |
| `note` | No | Anything the club's leaders should know |

The reply is private and shows your position in the queue. Running the command again for the same club edits the request you already have rather than putting you in the queue twice — correcting a typo does not cost you your place.

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

## /set_transfer_channel

Announce new transfer requests in a channel. Admin or club editor only.

| Parameter | Required | Description |
|---|---|---|
| `channel` | Yes | Channel for the announcements |
| `club` | Yes | The club |

Optional. Without it the queue still works — it is just reviewed on demand rather than announced.

Announcements carry no approve/decline buttons on purpose. A club taking a dozen requests a week would otherwise end up with a dozen live control panels in one channel, where the one that gets clicked is whichever scrolled past most recently rather than whichever is next. Each announcement is rewritten to show the outcome once a decision is made.

---

## What the requester is told

| Outcome | DM |
|---|---|
| Approved | Their request was approved, and to check their in-game notifications for the new invite |
| Declined | Their request was not accepted, with the reason if one was given |

DMs are sent to the Discord account that ran `/transfer_request` — no `/link_trainer` needed, since someone transferring in from outside has no member record yet. A member with DMs closed simply is not reached; the decision still stands and the queue still updates.

---

## On the dashboard

The **Transfers** page shows the same queue for the active club, with how long each person has been waiting, and approves or declines with the same effect. Decisions from either surface settle the request exactly once, so two leaders working at the same time cannot send one person two different answers.
