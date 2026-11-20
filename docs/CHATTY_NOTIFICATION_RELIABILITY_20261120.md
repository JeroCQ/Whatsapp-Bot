# Chatty notifications: reliability and device delivery

## Delivered scope

Existing central CRM only: notification content/snapshots, outbox dispatch, authenticated enrollment, push-only service worker and Avisos UI. No migrations, new variables/providers, Meta sender changes, bot prompts, ownership changes or polling acceleration. Both current installations and existing upgrades use the same JSON fields and existing varchar outbox statuses; original source schemas stay unchanged.

The central runtime already distributes the commercial service's existing NOTIFICATIONS_ENABLED/VAPID_PRIVATE_KEY/VAPID_SUBJECT to the central API and worker. Missing values must be reported, not duplicated per business. Different endpoints mean independent device subscriptions. Revocation is by the local subscription ID, not by operator-wide deletion.

## Semantics

- A new human handoff carries the bot's actual reason, business and customer name, plus the latest customer preview. Unknown reasons are explicitly unknown; priority codes are not invented handoff reasons. A new ordinary AI conversation alone does not page humans.
- Paused human cases generate new-message notices through the existing incoming watermark. Initial history, outgoing messages, delivery/read events and ordinary refreshes do not create notices. Existing transfer retains queue/owner validation and emits a transfer label.
- A stable hashed recipient/conversation topic replaces queued older pushes. The worker shows one notification per conversation and retains up to four received previews, ordered by conversation transition. Android/iOS decide truncation and visual grouping. Authenticated history also gets readable titles/reasons.
- Push priority is high, provider TTL is one hour and pending outbox events are bounded to 24 hours. New current events take precedence over seeding stale history. Offline time and system restrictions can still delay or prevent presentation.
- Provider failures retry at 5/15/45/120/300 seconds (at most six attempts), honoring a bounded Retry-After. 404/410 expires only that subscription; permanent 400/401/403/413 is reported as failed. One stalled provider request is bounded to four seconds.
- Current clients advertise receipt_version=1 on enrollment. A 2xx without a presentation receipt can be sent to the gateway at most two more times (45 seconds, then 120 seconds). The SAME event ID is deduplicated by the worker, including audio. Only events after the first receipt-capable enrollment qualify; no historical replay.
- presented means showNotification resolved. opened means the notification was clicked. Neither means a person read the CRM conversation or that the phone audibly rang. The gateway cannot prove presentation. Old sent rows remain historically unconfirmed.
- Receipt authentication is a 24-hour capability derived from the existing VAPID private key, scoped to an event/subscription pair. It cannot access CRM data, change read/assignment/control or send a message. A bounded worker queue retries lost receipts on the next push, binding renewal or click; no credential is stored in the worker. Receipt state is monotonic.
- Foreground UI claims do not suppress OS pushes. Both channels share only an audio claim, reducing duplicate sound. Native Push remains visible even with a visible page, as userVisibleOnly expects. Local audio requires gesture-unlocked AudioContext.
- A network failure while renewing the same canonical operator does not unsubscribe a valid endpoint or erase its binding. Focus/online recovery is throttled to once per minute, not a polling loop. Actual account changes/logout still fence callbacks and revoke the local endpoint. Memo's queues stay distinct; Tanaka/Velvet share their canonical operator.
- Successfully acknowledged visible read watermarks close only this device's matching older notification. Clicking a notification opens the conversation; it does not itself take control, mark read or send a reply. Normal existing visible-message read logic remains responsible for reads.

## What a PWA cannot copy from WhatsApp

The screenshots show native Android conversation notifications: MessagingStyle, shortcuts/People, group summary and direct-reply/read/mute actions. Standard Web Notifications expose title/body/icon/badge/tag/renotify and optional buttons, not that full native API. Chatty offers a real Open Conversation button where supported, not nonfunctional reply/mute controls. No private WhatsApp backend implementation is claimed to be known or copied.

There is no cross-device, offline or deadline guarantee. For a business requiring guaranteed human response, operational escalation/acknowledgement and a second channel would be a separate product decision. A native client would unlock richer presentation and platform integration but still cannot guarantee networking, user permissions or human response.

## Single publication and manual preparation

1. Deploy this backend commit to the existing central API, compatibility API and active central worker, preserving their runtime roles, sources, start commands and flags. Do not deploy bot consumers or run SQL.
2. Push the frontend commit to chatty-3.0/main. In Lovable, wait for Git sync, then Publish once.
3. On EACH intended device open https://chatty.tech.briosos.org (not a Lovable preview), reload and sign in as its real operator.
4. Open Avisos. Press Activar avisos and accept the system prompt. If already enrolled, press Actualizar diagnóstico; renewal is idempotent. A saved permission alone is not success.
5. Confirm recipient, browser subscription and server enrollment. Admin must opt into desired businesses in Negocios que recibes. That business preference is shared by the operator's devices; Push disable affects only this device/browser profile.
6. Android: permit app/browser notifications and sound; check Do Not Disturb and battery/background restrictions; do not force-stop the browser. iPhone/iPad: iOS 16.4+, install to Home Screen, open that installation and allow notifications there; check Focus and Settings > Notifications.

## Manual checks with Daniel's own test number only

No automated tests, app runs, builds, browser automation, real customer messages or simulated delivery were executed for this change. Deploy success/ready is not runtime notification validation.

1. Enroll the SAME operator on two devices. Lock one and use another browser on the second. From the test WhatsApp number request an advisor: both should receive the correct business/contact and actual handoff reason. The already enrolled device must not be removed by enrollment on the second.
2. With Chatty visible, send another test message requiring human attention: one local toast and at most one audible alert for that event; the OS notification is not discarded merely because a tab is visible. Local sound and OS sound have different settings.
3. Send three messages in the human case: one conversation notification updates with previews rather than three independent cards. Expand it: available detail depends on OS/browser. Tap Abrir conversación and verify the correct business/contact, with no automatic control/send operation.
4. Read the visible latest test messages: the local older notice closes after successful acknowledgement; Humano, owner and AI pause remain unchanged. A new incoming message restores the notification.
5. Transfer the test case between Memo's Detal/Mayoristas. New notices must reach the current authorized queue and registered devices, never the other account. Also verify Tanaka/Velvet labels under their shared operator.
6. Reload the same account and briefly disconnect/reconnect. Enrollment should persist/recheck; the ledger should prevent replay sounds. Logout/switch accounts: old-account notifications must not appear. Expired subscriptions require re-enrollment, not a false working badge.
7. Avisos > Actualizar diagnóstico: compare provider acceptance with presented/opened confirmations per device. A missing confirmation is unverified, not evidence of successful display. Receipt delivery itself can be delayed offline.

## Primary references reviewed

- https://developer.android.com/social-and-messaging/guides/communication/notifications-conversations
- https://developer.android.com/develop/ui/views/notifications/group
- https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/showNotification
- https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
- https://web.dev/articles/push-notifications-web-push-protocol
- https://www.rfc-editor.org/rfc/rfc8030
- https://github.com/web-push-libs/pywebpush
