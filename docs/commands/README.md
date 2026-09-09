# Commands

All commands are Discord slash commands (`/`).

## Categories

- [Club Management](club-management.md) — Add, edit, remove, and list clubs
- [Quota Management](quota-management.md) — Set quotas, view history, force checks
- [Channel Settings](channel-settings.md) — Configure report and alert channels
- [Member Management](member-management.md) — Add, activate, deactivate members
- [User Commands](user-commands.md) — Self-service commands for members
- [Transfers](transfers.md) — Queue for a spot in another club and review the waiting list
- [Charts & Stats](charts.md) — Progress charts and bot statistics
- [Event Feed](events.md) — Announce new Uma Musume banners and events as they go live

---

## Quick Reference

| Command | Who | Description |
|---|---|---|
| `/add_club` | Admin | Register a new club |
| `/remove_club` | Admin | Delete a club |
| `/edit_club` | Admin | Modify club settings |
| `/list_clubs` | Admin | View all clubs in server |
| `/activate_club` | Admin | Reactivate a deactivated club |
| `/quota` | Admin | Set daily quota for a club |
| `/quota_history` | Admin | View quota changes this month |
| `/delete_quota` | Admin | Remove a specific quota entry |
| `/force_check` | Admin | Manually trigger daily check |
| `/recalculate` | Admin | Recalculate bombs without clearing data |
| `/reset_month` | Admin | Manually trigger monthly reset |
| `/transfer_request` | Member | Queue for a spot in another club (needs `/link_trainer`) |
| `/my_transfers` | Member | View or withdraw your own transfer requests |
| `/transfer_queue` | Anyone | See a club's waiting list (leaders approve here) |
| `/set_transfer_channel` | Admin | Announce new transfer requests in a channel |
| `/post_transfer_info` | Admin | Post the how-to-request guide, ready to pin |
| `/set_report_channel` | Admin | Set daily report channel |
| `/set_alert_channel` | Admin | Set alert channel |
| `/channel_settings` | Admin | View channel config |
| `/post_monthly_info` | Admin | Post monthly info board |
| `/set_events_channel` | Admin | Announce new Uma Musume events in a channel |
| `/disable_events_channel` | Admin | Stop event announcements |
| `/events` | Anyone | Current banners, missions and events |
| `/events_status` | Admin | Event feed health |
| `/events_preview` | Admin | Preview an announcement embed |
| `/update_monthly_info` | Admin | Refresh monthly info board |
| `/add_member` | Admin | Manually add a member |
| `/deactivate_member` | Admin | Deactivate a member |
| `/activate_member` | Admin | Reactivate a member |
| `/bomb_status` | Admin | View active bombs |
| `/link_trainer` | Member | Link Discord to trainer name |
| `/unlink` | Member | Remove trainer link |
| `/my_status` | Member | View your own quota status |
| `/member_status` | Member | View any member's status |
| `/notification_settings` | Member | Manage DM preferences |
| `/progress_chart` | Anyone | Fan progression chart this month |
| `/previous_month` | Anyone | Last month's final fan stats |
| `/stats` | Author | Bot-wide statistics |
