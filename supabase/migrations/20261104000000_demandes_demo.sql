-- KLDataViz : demandes de démonstration envoyées depuis la page d'accueil (sans compte).
-- Lues et traitées dans l'administration ; aucun accès direct pour anon ni authenticated.

create table if not exists app.demo_requests (
  id          uuid primary key default gen_random_uuid(),
  created_at  timestamptz not null default now(),
  name        text not null check (char_length(name) between 2 and 120),
  email       text not null check (char_length(email) between 5 and 254),
  org         text not null check (char_length(org) between 2 and 120),
  phone       text check (char_length(phone) <= 40),
  message     text check (char_length(message) <= 1500),
  status      text not null default 'nouvelle' check (status in ('nouvelle', 'traitee', 'ecartee')),
  handled_by  text,
  handled_at  timestamptz,
  org_id      uuid references app.organizations(id) on delete set null   -- espace créé pour cette demande
);

create index if not exists demo_requests_created_idx on app.demo_requests (created_at desc);
create index if not exists demo_requests_email_idx on app.demo_requests (lower(email), created_at desc);

alter table app.demo_requests enable row level security;
revoke all on app.demo_requests from anon, authenticated;
