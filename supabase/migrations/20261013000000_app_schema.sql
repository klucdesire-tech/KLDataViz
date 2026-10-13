-- KLDataViz Studio en ligne : organisations, membres, invitations, usage, réglages, planifications.
-- Schéma « app » : lu et écrit uniquement par le serveur (connexion PostgreSQL directe).
-- Il n'est pas exposé par l'API REST Supabase ; RLS activé sans politique = aucun accès anon/authenticated.

create schema if not exists app;

create table if not exists app.organizations (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  plan        text not null default 'essai' check (plan in ('essai', 'pro', 'equipe', 'suspendu')),
  trial_end   timestamptz,
  created_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now()
);

create table if not exists app.memberships (
  user_id     uuid not null references auth.users(id) on delete cascade,
  org_id      uuid not null references app.organizations(id) on delete cascade,
  role        text not null default 'analyst' check (role in ('owner', 'analyst', 'viewer')),
  created_at  timestamptz not null default now(),
  primary key (user_id, org_id)
);
create index if not exists memberships_org_idx on app.memberships(org_id);

create table if not exists app.invitations (
  id          uuid primary key default gen_random_uuid(),
  org_id      uuid not null references app.organizations(id) on delete cascade,
  email       text not null,
  role        text not null default 'analyst' check (role in ('owner', 'analyst', 'viewer')),
  token       text not null unique,
  invited_by  uuid references auth.users(id) on delete set null,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null,
  accepted_at timestamptz
);
create index if not exists invitations_email_idx on app.invitations(lower(email));

create table if not exists app.usage_monthly (
  org_id      uuid not null references app.organizations(id) on delete cascade,
  month       text not null,
  analyses    integer not null default 0,
  ai_calls    integer not null default 0,
  primary key (org_id, month)
);

create table if not exists app.org_settings (
  org_id      uuid primary key references app.organizations(id) on delete cascade,
  langue      text not null default 'Français',
  detail      text not null default 'simple',
  updated_at  timestamptz not null default now()
);

create table if not exists app.schedules (
  id          text primary key,
  org_id      uuid not null references app.organizations(id) on delete cascade,
  source      text not null,
  name        text not null,
  freq        text not null check (freq in ('daily', 'weekly', 'monthly')),
  hour        integer not null check (hour between 0 and 23),
  format      text not null check (format in ('pdf', 'excel', 'html')),
  recipients  text[] not null default '{}',
  enabled     boolean not null default true,
  created     timestamptz not null default now(),
  last_run    timestamptz,
  last_status text,
  last_file   text,
  next_run    timestamptz
);
create index if not exists schedules_due_idx on app.schedules(next_run) where enabled;

alter table app.organizations enable row level security;
alter table app.memberships   enable row level security;
alter table app.invitations   enable row level security;
alter table app.usage_monthly enable row level security;
alter table app.org_settings  enable row level security;
alter table app.schedules     enable row level security;

revoke all on schema app from anon, authenticated;
revoke all on all tables in schema app from anon, authenticated;
