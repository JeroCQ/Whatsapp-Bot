"""CRM command ledger. Durability before POST, never automatic resend after ambiguity."""

import hashlib
import json
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from crm_service.bot_gateway import BotGateway, BotGatewayError
from crm_service.models import (
    Agent,
    AuditEvent,
    BotCommand,
    CRMConversation,
    NotificationEvent,
    PushDelivery,
    SourceConversation,
    now,
)
from crm_service.routing import attention_lock, authorize_case
from crm_service.security import Actor
from crm_service.sync import lock_id


class CommandCoordinator:
    def __init__(self, sessions: sessionmaker[Session], gateway: BotGateway):
        self.sessions, self.gateway = sessions, gateway

    def _row(self, session: Session, command_id: str) -> BotCommand | None:
        return session.scalar(
            select(BotCommand)
            .where(BotCommand.business_id == self.gateway.business.id, BotCommand.command_id == command_id)
            .with_for_update()
        )

    def _audit(self, session: Session, row: BotCommand, action: str, status: str) -> None:
        session.add(
            AuditEvent(
                business_id=row.business_id,
                phone_number=row.phone_number,
                profile=row.profile,
                action=action,
                details={"command_id": row.command_id, "operation": row.operation, "status": status},
            )
        )

    def _save(self, command_id: str, result: dict[str, Any], http_status: int) -> tuple[dict[str, Any], int]:
        with self.sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id(self.gateway.business.id)})
            row = self._row(session, command_id)
            assert row is not None
            if row.operation == "transfer":
                result = {**{k: row.response[k] for k in ("target_queue", "target_actor") if k in row.response}, **result}
                if row.status != "confirmed" and result["status"] == "confirmed":
                    control = result.get("control") or {}
                    if control.get("phase") != "human" or control.get("is_paused") is not True or control.get("owner") != result.get("target_actor"):
                        raise BotGatewayError("transfer_not_confirmed", 502)
                    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": attention_lock(row.business_id, row.phone_number)})
                    crm = session.get(CRMConversation, (row.business_id, row.phone_number))
                    assert crm is not None
                    crm.assigned_to = str(result["target_actor"])
                    crm.classification = {**crm.classification, "attention_queue": result["target_queue"],
                                          "queue_needs_classification": False, "queue_transferred": True,
                                          "queue_control_version": control["version"]}
                    crm.version += 1
                    crm.transition += 1
                    events = select(NotificationEvent.id).where(NotificationEvent.business_id == row.business_id,
                                                                 NotificationEvent.phone_number == row.phone_number)
                    session.execute(update(PushDelivery).where(PushDelivery.event_id.in_(events), PushDelivery.status == "pending").values(status="disabled"))
                    session.add(NotificationEvent(business_id=row.business_id, phone_number=row.phone_number, transition=crm.transition,
                                                  link=f"/chats?chat={row.business_id}:{row.phone_number}&bucket=needs_reply"))
                    self._audit(session, row, "crm.queue_transferred", "confirmed")
            if row.operation == "take" and result["status"] == "confirmed":
                crm = session.get(CRMConversation, (row.business_id, row.phone_number))
                control = result.get("control") or {}
                if crm and row.business_id == "memos" and control.get("owner") and type(control.get("version")) is int:
                    crm.classification = {**crm.classification, "queue_control_version": control["version"]}
                if crm and crm.assigned_to != row.profile:
                    crm.assigned_to = row.profile
                    crm.version += 1
            if row.operation == "close" and row.status != "confirmed" and result["status"] == "confirmed":
                control = result.get("control") or {}
                if control.get("phase") != "ai" or control.get("is_paused") is not False:
                    raise BotGatewayError("ai_return_not_confirmed", 502)
                self._release_projection(session, row, "return", control["version"])
            row.status, row.response, row.http_status = str(result["status"]), result, http_status
            row.updated_at = now()
            self._audit(session, row, "bot.command_result", row.status)
        return result, http_status

    def _release_projection(self, session: Session, command: BotCommand, kind: str, version: int | None) -> None:
        key = (command.business_id, command.phone_number)
        crm, source = session.get(CRMConversation, key), session.get(SourceConversation, key)
        if crm is None or source is None:
            raise BotGatewayError("crm_conversation_not_found", 404)
        if kind != "dismiss":
            crm.bucket = "ai_active"
        crm.classification = {
            **crm.classification, "bucket": crm.bucket, "in_human_inbox": False,
            **({"priority": 0, "priority_reason": "no_human_attention_required", "attention_since": None,
                "waiting_seconds": 0} if kind != "dismiss" else {}),
            "inbox_release": {"command_id": command.command_id, "at": now().isoformat(), "kind": kind,
                              "control_version": version, "handoff_request_id": source.handoff.get("request_id")},
        }
        crm.version += 1
        source.dirty = True

    def dismiss(self, principal: Actor, phone: str, command_id: str, version: int) -> tuple[dict[str, Any], int]:
        """CRM-only dismissal requires the bot's explicit missing-state response, never a generic 404."""
        business = self.gateway.business.id
        principal.authorize(business)
        digest = hashlib.sha256(json.dumps([business, phone, principal.profile, version]).encode()).hexdigest()
        with self.sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id(business)})
            row = self._row(session, command_id)
            if row:
                if row.operation != "dismiss" or row.phone_number != phone or row.profile != principal.profile or row.request_hash != digest:
                    raise BotGatewayError("idempotency_key_conflict", 409)
                return row.response, row.http_status
            try:
                self.gateway.control(phone)
            except BotGatewayError as error:
                if error.code != "conversation_state_not_found":
                    raise
            else:
                raise BotGatewayError("existing_bot_state_requires_real_return", 409)
            crm = session.get(CRMConversation, (business, phone))
            if crm is None:
                raise BotGatewayError("crm_conversation_not_found", 404)
            if crm.version != version or crm.bucket not in {"awaiting_human", "human_active"}:
                raise BotGatewayError("crm_version_or_bucket_changed", 409)
            result = {"command_id": command_id, "status": "confirmed", "control": None, "crm_only": True}
            row = BotCommand(business_id=business, command_id=command_id, phone_number=phone,
                             profile=principal.profile, operation="dismiss", request_hash=digest,
                             status="confirmed", response=result, http_status=200)
            session.add(row)
            self._release_projection(session, row, "dismiss", None)
            self._audit(session, row, "crm.historical_dismissed", "confirmed")
            return result, 200

    def _recover(
        self, phone: str, command_id: str, cached: dict[str, Any] | None = None, cached_code: int = 202
    ) -> tuple[dict[str, Any], int]:
        try:
            result = self.gateway.command(phone, command_id)
        except BotGatewayError as error:
            if error.code == "bot_command_not_found":
                # A missing receipt is not proof that a message was never sent.
                # Read current authority; never repeat the original POST.
                try:
                    control = self.gateway.control(phone)
                except BotGatewayError:
                    control = None
                result = {
                    **(cached or {}), "command_id": command_id,
                    "status": (cached or {}).get("status", "unknown"),
                    "control": control, "error": "command_result_missing_no_resend",
                }
                return self._reconcile_recovery(phone, command_id, result)
            if cached:
                return cached, cached_code
            return {
                "command_id": command_id,
                "status": "unknown",
                "control": None,
                "provider_message_id": None,
                "error": "query_command_until_resolved",
            }, 202
        return self._reconcile_recovery(phone, command_id, result)

    def _reconcile_recovery(
        self, phone: str, command_id: str, result: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        with self.sessions() as session:
            row = session.get(BotCommand, (self.gateway.business.id, command_id))
            if row and row.phone_number != phone:
                raise BotGatewayError("command_not_found", 404)
            control = result.get("control") or {}
            agent = session.get(Agent, row.profile) if row else None
            aliases = agent.preferences.get("_crm_operator", {}).get("aliases", []) if agent else []
            if row and result["status"] in {"pending", "unknown"}:
                took = (
                    row.operation == "take" and control.get("phase") == "human"
                    and control.get("confirmed") is True and control.get("is_paused") is True
                    and control.get("owner") in (row.profile, *aliases)
                )
                returned = (
                    row.operation == "close" and control.get("phase") == "ai"
                    and control.get("is_paused") is False and not control.get("owner")
                )
                transferred = row.operation == "transfer" and control.get("phase") == "human" and control.get("confirmed") is True and control.get("owner") == row.response.get("target_actor")
                if took or returned or transferred:
                    result = {**result, "status": "confirmed", "reconciled_from_control": True}
                    result.pop("error", None)
            exists = row is not None
        code = 202 if result["status"] in {"pending", "unknown"} else 200
        return self._save(command_id, result, code) if exists else (result, code)

    def status(self, principal: Actor, phone: str, command_id: str) -> tuple[dict[str, Any], int]:
        principal.authorize(self.gateway.business.id)
        with self.sessions() as session:
            row = session.get(BotCommand, (self.gateway.business.id, command_id))
            if row and row.phone_number != phone:
                raise BotGatewayError("command_not_found", 404)
            if row and row.operation == "dismiss":
                return row.response, row.http_status
            cached, code = (row.response, row.http_status) if row else (None, 202)
        return self._recover(phone, command_id, cached, code)

    def execute(
        self, principal: Actor, phone: str, operation: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        result: dict[str, Any]
        business = self.gateway.business.id
        principal.authorize(business)
        with self.sessions() as session:
            crm = session.get(CRMConversation, (business, phone))
            known = session.get(BotCommand, (business, str(payload["command_id"])))
            replaying = bool(known and known.profile == principal.profile and known.phone_number == phone and known.operation == operation)
            if crm and not replaying:
                authorize_case(principal, crm)
            elif crm is None and business == "memos" and not principal.is_admin:
                raise BotGatewayError("crm_conversation_not_found", 404)
        body = {**payload, "actor": principal.profile}
        if operation == "transfer":
            target = principal.queue_actors.get(str(payload.get("target_queue")))
            if business != "memos" or not target:
                raise BotGatewayError("queue_transfer_not_authorized", 403)
            if crm and not replaying and crm.classification.get("attention_queue", "retail") == payload["target_queue"]:
                raise BotGatewayError("already_in_target_queue", 409)
            body["target_actor"] = target
        command_id = str(body["command_id"])
        immutable_body = body
        if body.get("attachment_id"):
            attachment = self.gateway.attachment(phone, str(body["attachment_id"]), principal.profile)
            if attachment.get("availability") != "ready" or not attachment.get("sha256"):
                raise BotGatewayError("attachment_not_ready", 409)
            # Asset IDs are immutable at the bot, and the ledger also pins their
            # actual content identity without adding fields to the command wire body.
            immutable_body = {**body, "attachment_sha256": attachment["sha256"]}
        digest = hashlib.sha256(
            json.dumps([business, phone, operation, immutable_body], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        replay = False
        cached: dict[str, Any] | None = None
        cached_code = 202
        with self.sessions.begin() as session:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": lock_id("bot-command:" + business + ":" + command_id)},
            )
            row = self._row(session, command_id)
            if row:
                if (
                    row.request_hash != digest
                    or row.profile != principal.profile
                    or row.phone_number != phone
                    or row.operation != operation
                ):
                    raise BotGatewayError("idempotency_key_conflict", 409)
                if row.status != "not_forwarded":
                    replay, cached, cached_code = True, row.response or None, row.http_status
                else:
                    row.status, row.updated_at = "checking", now()
            else:
                row = BotCommand(
                    business_id=business,
                    command_id=command_id,
                    phone_number=phone,
                    profile=principal.profile,
                    operation=operation,
                    request_hash=digest,
                    status="checking",
                    response=({"command_id": command_id, "status": "unknown", "control": None,
                               "target_queue": body["target_queue"], "target_actor": body["target_actor"]}
                              if operation == "transfer" else {}),
                )
                session.add(row)
                session.flush()
                self._audit(session, row, "bot.command_requested", "checking")
        if replay:
            # Read-only recovery. No retrying POST, including after process death in 'checking'/'forwarding'.
            return self._recover(phone, command_id, cached, cached_code)
        try:
            control = self.gateway.control(phone)
        except BotGatewayError as error:
            result = {
                "command_id": command_id,
                "status": "not_forwarded",
                "control": None,
                "provider_message_id": None,
                "error": error.code,
            }
            self._save(command_id, result, error.status_code)
            return result, error.status_code
        error_code = None
        if control["version"] != payload["expected_version"]:
            error_code = "authoritative_version_changed"
        elif operation == "messages" and (
            control["phase"] != "human"
            or not control["confirmed"]
            or not control["is_paused"]
            or not control["can_send"]
            or not principal.owns(control.get("owner"))
        ):
            error_code = "confirmed_human_control_required"
        elif operation == "close" and not (
            (control["phase"] == "human" and control["confirmed"] and principal.owns(control.get("owner")))
            or (control["phase"] in {"waiting", "ai"} and not control.get("owner"))
        ):
            error_code = "confirmed_human_control_required"
        elif operation == "take" and control.get("owner") and not principal.owns(control.get("owner")):
            error_code = "conversation_owned_by_another_actor"
        elif operation == "transfer" and not (
            control["is_paused"] and ((control["phase"] == "human" and control["confirmed"] and principal.owns(control.get("owner")))
                                       or (control["phase"] == "waiting" and not control.get("owner")))
        ):
            error_code = "confirmed_owner_or_unassigned_waiting_required"
        if error_code:
            return self._save(
                command_id,
                {
                    "command_id": command_id,
                    "status": "rejected",
                    "control": control,
                    "provider_message_id": None,
                    "error": error_code,
                },
                409,
            )
        if operation == "close" and control["phase"] == "ai" and control["is_paused"] is False:
            # Already authoritative AI: persist the operator's inbox decision without fabricating a bot event.
            return self._save(command_id, {"command_id": command_id, "status": "confirmed", "control": control}, 200)
        with self.sessions.begin() as session:
            row = self._row(session, command_id)
            assert row is not None
            if row.status not in {"checking", "not_forwarded"} and row.response:
                return row.response, row.http_status
            row.status, row.updated_at = "forwarding", now()
            self._audit(session, row, "bot.command_forwarded", "forwarding")
        try:
            if principal.owns(control.get("owner")):
                body["actor"] = control["owner"]
            result = self.gateway.execute(operation, phone, {k: v for k, v in body.items() if k != "target_queue"})
        except BotGatewayError as error:
            status = "unknown" if error.ambiguous else "rejected"
            result = {
                "command_id": command_id,
                "status": status,
                "control": control,
                "provider_message_id": None,
                "error": error.code,
            }
            return self._save(command_id, result, 202 if error.ambiguous else error.status_code)
        return self._save(command_id, result, 202 if result["status"] in {"pending", "unknown"} else 200)
