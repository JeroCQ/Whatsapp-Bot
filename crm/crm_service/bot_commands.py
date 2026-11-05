"""CRM command ledger. Durability before POST, never automatic resend after ambiguity."""

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from crm_service.bot_gateway import BotGateway, BotGatewayError
from crm_service.models import AuditEvent, BotCommand, CRMConversation, SourceConversation, now
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
        except BotGatewayError:
            if cached:
                return cached, cached_code
            return {
                "command_id": command_id,
                "status": "unknown",
                "control": None,
                "provider_message_id": None,
                "error": "query_command_until_resolved",
            }, 202
        code = 202 if result["status"] in {"pending", "unknown"} else 200
        with self.sessions() as session:
            exists = session.get(BotCommand, (self.gateway.business.id, command_id)) is not None
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
        body = {**payload, "actor": principal.profile}
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
            or control.get("owner") != principal.profile
        ):
            error_code = "confirmed_human_control_required"
        elif operation == "close" and not (
            (control["phase"] == "human" and control["confirmed"] and control.get("owner") == principal.profile)
            or (control["phase"] in {"waiting", "ai"} and not control.get("owner"))
        ):
            error_code = "confirmed_human_control_required"
        elif operation == "take" and control.get("owner") not in {None, "", principal.profile}:
            error_code = "conversation_owned_by_another_actor"
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
            result = self.gateway.execute(operation, phone, body)
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
