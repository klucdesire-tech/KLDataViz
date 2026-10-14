-- KLDataViz : administration de la plateforme — forfaits paramétrables, annonces,
-- administrateurs, réglages du site public, suspension d'un espace.

create table if not exists app.plans (
  key          text primary key check (key ~ '^[a-z0-9_-]{2,30}$'),
  label        text not null,
  price        numeric,                      -- null = « Sur devis »
  currency     text not null default 'FCFA',
  period       text not null default 'mois',
  analyses     integer not null default 10 check (analyses >= 0),
  ai           integer not null default 50 check (ai >= 0),
  members      integer not null default 1 check (members >= 0),
  trial_days   integer check (trial_days is null or trial_days between 1 and 365),
  description  text not null default '',
  features     text[] not null default '{}',
  highlight    boolean not null default false,
  public       boolean not null default true,   -- affiché sur la page d'accueil
  sort         integer not null default 0,
  updated_at   timestamptz not null default now()
);

insert into app.plans (key, label, price, analyses, ai, members, trial_days, description, features, highlight, public, sort) values
  ('essai', 'Essai', 0, 10, 50, 1, 14, 'Pour découvrir Hermes sur vos propres données.',
   array['14 jours gratuits', '10 analyses', '50 questions à l''IA', '1 utilisateur'], false, true, 1),
  ('pro', 'Pro', null, 100, 1000, 5, null, 'Pour une équipe data qui produit des rapports chaque mois.',
   array['100 analyses par mois', '1 000 questions à l''IA', '5 utilisateurs', 'Rapports planifiés par e-mail'], true, true, 2),
  ('equipe', 'Équipe', null, 1000, 10000, 25, null, 'Pour les directions et organisations multi-sites.',
   array['1 000 analyses par mois', '10 000 questions à l''IA', '25 utilisateurs', 'Accompagnement à la prise en main'], false, true, 3),
  ('suspendu', 'Suspendu', 0, 0, 0, 0, null, 'Espace sans accès (essai terminé ou suspendu).', '{}', false, false, 99)
on conflict (key) do nothing;

alter table app.organizations drop constraint if exists organizations_plan_check;
alter table app.organizations drop constraint if exists organizations_plan_fkey;
alter table app.organizations add constraint organizations_plan_fkey
  foreign key (plan) references app.plans(key) on update cascade;
alter table app.organizations add column if not exists suspended boolean not null default false;

create table if not exists app.platform_admins (
  email       text primary key check (email = lower(email)),
  added_by    text,
  created_at  timestamptz not null default now()
);

create table if not exists app.announcements (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  body        text not null default '',
  level       text not null default 'info' check (level in ('info', 'success', 'warning')),
  audience    text not null default 'all' check (audience in ('all', 'public', 'app')),
  link_label  text,
  link_url    text check (link_url is null or link_url ~ '^(https://|/)'),
  starts_at   timestamptz,
  ends_at     timestamptz,
  active      boolean not null default true,
  created_by  text,
  created_at  timestamptz not null default now()
);

create table if not exists app.site_settings (
  key         text primary key,
  value       jsonb not null,
  updated_at  timestamptz not null default now()
);

insert into app.site_settings (key, value) values
  ('hero_title', '"Vos données expliquées en minutes, pas en semaines."'),
  ('hero_subtitle', '"Importez un fichier Excel ou une feuille Google. Hermes vérifie la qualité, trouve ce qui compte et prépare vos rapports, en français."'),
  ('contact_email', '""'),
  ('contact_whatsapp', '""')
on conflict (key) do nothing;

alter table app.plans           enable row level security;
alter table app.platform_admins enable row level security;
alter table app.announcements   enable row level security;
alter table app.site_settings   enable row level security;
revoke all on all tables in schema app from anon, authenticated;
