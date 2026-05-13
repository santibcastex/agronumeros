-- =============================================================
-- AGRONUMEROS — Schema completo
-- Ejecutar en Supabase → SQL Editor
-- =============================================================

-- — 1. CULTIVOS —————————————————————————————————————————————
create table if not exists cultivos (
  id        serial primary key,
  codigo    text not null unique,
  nombre    text not null
);

insert into cultivos (codigo, nombre) values
  ('soja_rosario',        'Soja'),
  ('maiz_rosario',        'Maíz'),
  ('trigo_rosario',       'Trigo'),
  ('girasol_rosario',     'Girasol'),
  ('sorgo_rosario',       'Sorgo'),
  ('soja_bahia_blanca',   'Soja'),
  ('maiz_bahia_blanca',   'Maíz'),
  ('trigo_bahia_blanca',  'Trigo'),
  ('girasol_bahia_blanca','Girasol'),
  ('cebada_bahia_blanca', 'Cebada'),
  ('soja_darsena',        'Soja'),
  ('maiz_darsena',        'Maíz'),
  ('trigo_darsena',       'Trigo'),
  ('girasol_darsena',     'Girasol'),
  ('sorgo_darsena',       'Sorgo'),
  ('soja_quequen',        'Soja'),
  ('maiz_quequen',        'Maíz'),
  ('trigo_quequen',       'Trigo'),
  ('girasol_quequen',     'Girasol'),
  ('cebada_quequen',      'Cebada')
on conflict (codigo) do nothing;

-- — 2. PRECIOS AGRO ————————————————————————————————————————
create table if not exists precios_agro (
  id          bigserial primary key,
  cultivo_id  int references cultivos(id),
  fecha       date not null,
  precio_ars  numeric(12,2),
  precio_usd  numeric(10,2),
  zona        text,
  unique (cultivo_id, fecha)
);
create index if not exists idx_precios_agro_fecha on precios_agro(fecha desc);

-- — 3. RETENCIONES ————————————————————————————————————————
create table if not exists retenciones (
  id              serial primary key,
  cultivo_id      int references cultivos(id),
  porcentaje      numeric(5,2),
  vigente_desde   date,
  observaciones   text
);

insert into retenciones (cultivo_id, porcentaje, vigente_desde, observaciones)
select c.id, r.pct, r.desde::date, r.obs
from (values
  ('soja_rosario',    33, '2024-01-01', 'Decreto 877/2025'),
  ('maiz_rosario',    12, '2024-01-01', 'Decreto 877/2025'),
  ('trigo_rosario',   12, '2024-01-01', 'Decreto 877/2025'),
  ('girasol_rosario', 7,  '2024-01-01', 'Decreto 877/2025'),
  ('sorgo_rosario',   12, '2024-01-01', 'Decreto 877/2025')
) as r(cod, pct, desde, obs)
join cultivos c on c.codigo = r.cod
on conflict do nothing;

-- — 4. EXPORTACIONES AGRO ———————————————————————————————————
create table if not exists exportaciones_agro (
  id            bigserial primary key,
  cultivo_id    int references cultivos(id),
  mes           date not null,
  valor_fob_usd numeric(16,2),
  unique (cultivo_id, mes)
);

-- — 5. INDICADORES GLOBALES ————————————————————————————————
create table if not exists indicadores (
  id      serial primary key,
  codigo  text not null unique,
  nombre  text,
  unidad  text
);

insert into indicadores (codigo, nombre, unidad) values
  ('sp500',           'S&P 500',        'USD'),
  ('dow_jones',       'Dow Jones',      'USD'),
  ('nasdaq',          'Nasdaq',         'USD'),
  ('vix',             'VIX',            'índice'),
  ('merval',          'Merval',         'ARS'),
  ('riesgo_pais',     'Riesgo País',    'bps'),
  ('usd_ars_oficial', 'Dólar Oficial',  'ARS'),
  ('usd_ars_mep',     'Dólar MEP',      'ARS'),
  ('usd_ars_blue',    'Dólar Blue',     'ARS'),
  ('usd_ars_ccl',     'Dólar CCL',      'ARS'),
  ('soja_chicago',    'Soja Chicago',   'USD/tn'),
  ('maiz_chicago',    'Maíz Chicago',   'USD/tn'),
  ('trigo_chicago',   'Trigo Chicago',  'USD/tn'),
  ('petroleo_wti',    'Petróleo WTI',   'USD/bbl'),
  ('oro',             'Oro',            'USD/oz'),
  ('plata',           'Plata',          'USD/oz'),
  ('cobre',           'Cobre',          'USD/lb'),
  ('eur_usd',         'EUR/USD',        ''),
  ('usd_brl',         'USD/BRL',        ''),
  ('dxy',             'DXY',            'índice'),
  ('btc_usd',         'Bitcoin',        'USD'),
  ('eth_usd',         'Ethereum',       'USD')
on conflict (codigo) do nothing;

-- — 6. PRECIOS GLOBALES ————————————————————————————————————
create table if not exists precios_globales (
  id           bigserial primary key,
  indicador_id int references indicadores(id),
  fecha        date not null,
  valor        numeric(18,4),
  unique (indicador_id, fecha)
);
create index if not exists idx_precios_globales_fecha on precios_globales(fecha desc);

-- — 7. SERIES DIARIAS (BADLAR, etc.) ————————————————————————
create table if not exists series_diarias (
  id      bigserial primary key,
  codigo  text not null,
  fecha   date not null,
  valor   numeric(12,4),
  unique (codigo, fecha)
);

-- — 8. COMBUSTIBLES POR PROVINCIA ——————————————————————————
create table if not exists combustibles_provincia (
  id              bigserial primary key,
  provincia       text not null,
  producto        text not null,
  anio            int,
  mes             int,
  precio_promedio numeric(10,2),
  cantidad_eess   int,
  unique (provincia, producto, anio, mes)
);

-- — 9. RLS — habilitar lectura anónima ————————————————————
alter table cultivos              enable row level security;
alter table precios_agro          enable row level security;
alter table retenciones           enable row level security;
alter table exportaciones_agro    enable row level security;
alter table indicadores           enable row level security;
alter table precios_globales      enable row level security;
alter table series_diarias        enable row level security;
alter table combustibles_provincia enable row level security;

create policy "anon read cultivos"               on cultivos              for select using (true);
create policy "anon read precios_agro"           on precios_agro          for select using (true);
create policy "anon read retenciones"            on retenciones           for select using (true);
create policy "anon read exportaciones_agro"     on exportaciones_agro    for select using (true);
create policy "anon read indicadores"            on indicadores           for select using (true);
create policy "anon read precios_globales"       on precios_globales      for select using (true);
create policy "anon read series_diarias"         on series_diarias        for select using (true);
create policy "anon read combustibles_provincia" on combustibles_provincia for select using (true);
