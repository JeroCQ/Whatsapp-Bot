"""Atomic, brand-scoped follow-up state. No Redis flush or blind send retries.

Reservations can expire before a provider call. Once marked ``sending``, a crash
is ambiguous and must never automatically send that attempt again.
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone

STATE_TTL = 8 * 86400


def epoch(value=None):
    if value is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).timestamp()
    return float(value)


def keys(phone, token="", stage="", attempt=0):
    business = os.getenv("BUSINESS_ID", "").strip().lower()
    scope = f"{business}|{os.getenv('WA_PHONE_NUMBER_ID', '')}|{phone}"
    subject = hashlib.sha256(scope.encode()).hexdigest()
    prefix = f"whatsapp-follow-up-v2:{{{subject}}}"
    identifier = hashlib.sha256(f"{token}|{stage}|{attempt}".encode()).hexdigest()
    return [prefix + ":active", prefix + ":activity", prefix + ":last-send", prefix + ":attempt:" + identifier,
            f"whatsapp-follow-up-token:{phone}", prefix + ":incoming:" + hashlib.sha256(token.encode()).hexdigest(),
            f"whatsapp-follow-up-token:{phone}:{stage}:{attempt}"]


_SCRIPT = r"""
local op, token, now, lease, spacing, max_tries, ttl, outcome, provider = unpack(ARGV)
now, spacing, max_tries, ttl = tonumber(now), tonumber(spacing), tonumber(max_tries), tonumber(ttl)
local function reply(status, retry_at, count)
  return cjson.encode({status=status, retry_at=retry_at or 0, retry_count=count or 0, reservation_id=lease})
end
if op == 'activity' then
  if token ~= '' and not redis.call('SET', KEYS[6], '1', 'NX', 'EX', ttl) then return reply('unchanged') end
  local previous = tonumber(redis.call('HGET', KEYS[2], 'at') or '0')
  local old_event = redis.call('HGET', KEYS[2], 'event')
  if now < previous or (now == previous and old_event == token) then return reply('unchanged') end
  redis.call('HSET', KEYS[2], 'at', now, 'event', token)
  redis.call('EXPIRE', KEYS[2], ttl)
  redis.call('SET', KEYS[1], '!cancelled', 'EX', ttl)
  redis.call('DEL', KEYS[5])
  return reply('invalidated')
end
if op == 'invalidate' then
  redis.call('SET', KEYS[1], '!cancelled', 'EX', ttl)
  redis.call('DEL', KEYS[5])
  return reply('invalidated')
end
if op == 'register' then
  local latest = tonumber(redis.call('HGET', KEYS[2], 'at') or '0')
  local latest_event = redis.call('HGET', KEYS[2], 'event')
  if latest > now or (latest == now and provider ~= '' and latest_event ~= provider) then return reply('stale') end
  redis.call('SET', KEYS[1], token, 'EX', ttl)
  redis.call('DEL', KEYS[5])
  return reply('registered')
end
local active = redis.call('GET', KEYS[1])
-- Adopt a legacy sequence only once, in this isolated deployment. A tombstone
-- prevents old RQ jobs from resurrecting an invalidated legacy token.
if not active and redis.call('GET', KEYS[5]) == token then
  redis.call('SET', KEYS[1], token, 'EX', ttl)
  redis.call('HSET', KEYS[2], 'legacy_token', token)
  redis.call('EXPIRE', KEYS[2], ttl)
  active = token
end
if active ~= token then return reply('stale') end
if redis.call('HGET', KEYS[2], 'legacy_token') == token and redis.call('EXISTS', KEYS[7]) == 1 then
  return reply('legacy_claim_ambiguous')
end
if op == 'consume' then
  redis.call('SET', KEYS[1], '!cancelled', 'EX', ttl)
  redis.call('DEL', KEYS[5])
  return reply('claimed')
end
if op == 'claim' then
  if redis.call('HSETNX', KEYS[4], 'state', 'claimed') == 1 then
    redis.call('EXPIRE', KEYS[4], ttl)
    return reply('claimed')
  end
  return reply('duplicate')
end
local state = redis.call('HGET', KEYS[4], 'state')
local count = tonumber(redis.call('HGET', KEYS[4], 'count') or '0')
if op == 'reserve' then
  if state == 'sent' or state == 'unknown' or state == 'sending' or state == 'cancelled' or state == 'claimed' then
    return reply(state == 'sent' and 'duplicate' or state, 0, count)
  end
  if state == 'exhausted' or count >= max_tries then return reply('exhausted', 0, count) end
  local available = tonumber(redis.call('HGET', KEYS[4], 'available') or '0')
  if available > now then return reply('deferred', available, count) end
  local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
  if previous + spacing > now then return reply('deferred', previous + spacing, count) end
  count = count + 1
  redis.call('HSET', KEYS[4], 'state', 'reserved', 'lease', lease, 'available', now + 60, 'count', count)
  redis.call('EXPIRE', KEYS[4], ttl)
  return reply('reserved', 0, count)
end
if redis.call('HGET', KEYS[4], 'lease') ~= lease then return reply('stale_lease', 0, count) end
if op == 'begin' then
  if state ~= 'reserved' then return reply(state or 'stale', 0, count) end
  local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
  if previous + spacing > now then
    redis.call('HSET', KEYS[4], 'state', 'retry', 'available', previous + spacing, 'count', count - 1)
    return reply('deferred', previous + spacing, count - 1)
  end
  -- Store this before contacting Meta: attempts from other workers, including
  -- accumulated jobs from an outage, cannot race a provider request.
  redis.call('HSET', KEYS[4], 'state', 'sending', 'began_at', now, 'previous_send_at', previous, 'guard_at', now + 60)
  redis.call('SET', KEYS[3], now + 60, 'EX', ttl)
  return reply('sending', 0, count)
end
if op == 'finish' then
  if state ~= 'reserved' and state ~= 'sending' then return reply(state or 'stale', 0, count) end
  local new_state = 'unknown'
  local available = 0
  if outcome == 'accepted' then new_state = 'sent'
  elseif outcome == 'cancelled' then new_state = 'cancelled'
  elseif outcome == 'confirmed_failure' then
    new_state = count < max_tries and 'retry' or 'exhausted'
    available = now + 60
  end
  if outcome == 'accepted' or outcome == 'unknown' then
    local current = tonumber(redis.call('GET', KEYS[3]) or '0')
    local guard = tonumber(redis.call('HGET', KEYS[4], 'guard_at') or '-1')
    redis.call('SET', KEYS[3], current == guard and now or math.max(current, now), 'EX', ttl)
  elseif outcome == 'confirmed_failure' and state == 'sending' then
    local guard = tonumber(redis.call('HGET', KEYS[4], 'guard_at') or '-1')
    if tonumber(redis.call('GET', KEYS[3]) or '0') == guard then
      redis.call('SET', KEYS[3], redis.call('HGET', KEYS[4], 'previous_send_at') or '0', 'EX', ttl)
    end
  end
  redis.call('HSET', KEYS[4], 'state', new_state, 'available', available, 'provider_id', provider)
  return reply(new_state, available, count)
end
return reply('invalid_operation')
"""


def execute(connection, operation, phone, token="", stage="", attempt=0, *, reservation_id="", now=None,
            min_spacing_seconds=900, max_retries=3, ttl_seconds=STATE_TTL, outcome="", provider_message_id=""):
    lease = reservation_id or uuid.uuid4().hex
    raw = connection.eval(_SCRIPT, 7, *keys(phone, token, stage, attempt), operation, token, str(epoch(now)),
                          lease, str(max(1, int(min_spacing_seconds))), str(max(1, int(max_retries))),
                          str(max(3600, int(ttl_seconds))), outcome, provider_message_id or "")
    return json.loads(raw)
