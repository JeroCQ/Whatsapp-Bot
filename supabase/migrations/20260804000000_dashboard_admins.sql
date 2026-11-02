-- Only the service-role-backed Edge Function reads this allow-list.
create table if not exists public.dashboard_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

alter table public.dashboard_admins enable row level security;

-- Deliberately no browser-facing policies: authenticated users cannot add
-- themselves. Administrators are inserted manually from the Supabase SQL editor.
revoke all on table public.dashboard_admins from anon, authenticated;
